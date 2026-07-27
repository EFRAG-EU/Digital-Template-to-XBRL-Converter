# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

EFRAG's converter that turns a filled-in VSME Excel "Digital Template" into a validated Inline XBRL
report (plus report package, viewer and xBRL-JSON). Mapping is driven by Excel **named ranges** whose
names match the local name of the corresponding taxonomy concept. Ships as both a CLI (`scripts/`) and
a Flask web app.

## Environment and commands

Two virtualenvs exist in the checkout. `.venv-py314` is the default for all work; `.venv-py311` exists
only for checking the 3.11 baseline (`requires-python = ">=3.11"`, which is what CI runs). Never invoke
a bare `python`.

```powershell
.venv-py314/Scripts/python -m pip install -e ".[dev]"    # editable install with dev extras
```

### Tests

The three test trees are run separately in CI and are best run separately locally too.

```powershell
.venv-py314/Scripts/python -m pytest tests/unitTests
.venv-py314/Scripts/python -m pytest tests/integrationTests
.venv-py314/Scripts/python -m pytest tests/webappTests

# single test / single case
.venv-py314/Scripts/python -m pytest tests/unitTests/xlsx_template_reader/test_ranges.py::test_name
```

- Tests marked `@pytest.mark.slow` (notably full Arelle validation in
  `tests/integrationTests/test_expectedFactCounts.py`) are **skipped by default**. Enable with
  `--run-slow` or `FORCE_RUN=1`. They run automatically on `main` and PRs to `main`.
- After any change to the conversion pipeline, run `tests/integrationTests/test_expectedFactCounts.py`
  — it pins per-template fact counts and catches silent fact loss.
- `tests/unitTests/xlsx_template_reader/test_fact_creator_characterization.py` snapshots every fact
  (concept, value, aspects, footnotes) from the 1.2.0 and 1.3.0 samples. Regenerate deliberately, only
  when a change to the output is intended:
  `.venv-py314/Scripts/python tests/unitTests/xlsx_template_reader/test_fact_creator_characterization.py`
- `tests/unitTests/test_import_cycles.py` fails on any new circular import inside `mireport`.

### Lint and types

CI runs ruff only, but mypy config is strict-ish for `src/` (`disallow_untyped_defs`, tests excluded).

```powershell
.venv-py314/Scripts/python -m ruff check .
.venv-py314/Scripts/python -m ruff format --check .
.venv-py314/Scripts/python -m mypy src
```

### Running the converter

```powershell
# Excel -> Inline XBRL (add --viewer / --json; output path may be a file or a directory)
.venv-py314/Scripts/python scripts/parse-and-ixbrl.py example.xlsx output.html

# dump named ranges from a workbook (debugging what the reader sees)
.venv-py314/Scripts/python scripts/parse-and-dump.py example.xlsx

# validate an existing report / build a viewer for it
.venv-py314/Scripts/python scripts/check-report.py report.html

# web app (auto-reload)
.venv-py314/Scripts/python -m flask --app digital_converter_webapp run --debug
```

`--skip-validation` on `parse-and-ixbrl.py` is a development-only shortcut. Do not propose it as a way
to make things faster in anything that matters — Arelle validation is the point of the tool.

### Regenerating taxonomy data

Taxonomy metadata is **pre-baked JSON** in `src/mireport/data/taxonomies/`; nothing at runtime reads
`.xsd`/linkbases. The JSON is produced by Arelle from taxonomy package zips (which are not in this
repo):

```powershell
.venv-py314/Scripts/python scripts/update-taxonomy.py --entry-point <URL> src/mireport/data/taxonomies/vsme-YYYY-MM-DD.json path/to/*.zip
```

`scripts/dump-taxonomy.py` dumps concept/presentation info (including to xlsx) from the baked JSON.

### Front-end assets

`src/digital_converter_webapp/static/style.css` is a **generated, committed** Tailwind build. Edit
`src/digital_converter_webapp/templates/source.css`, then regenerate (requires `npm install`):

```powershell
npx @tailwindcss/cli -i .\src\digital_converter_webapp\templates\source.css -o .\src\digital_converter_webapp\static\style.css --minify
npx eslint .
```

## Architecture

Two packages under `src/`: `mireport` (all the conversion logic, no Flask) and
`digital_converter_webapp` (thin Flask UI over it). The CLI scripts and the web app are two front ends
driving the same pipeline.

### The pipeline

```
.xlsx  ──XlsxProcessor──▶  InlineReport (facts + aspects)
       ──Jinja templates──▶  HTML in the aoix template dialect
       ──ixbrltemplates.Parser──▶  Inline XBRL
       ──▶ report package (.zip)  ──Arelle──▶  validation messages / viewer / xBRL-JSON
```

`InlineReport.getInlineReport()` renders `mireport/report/inline_report_templates/` and then hands the
HTML to `aoix` (`ixbrltemplates`), which rewrites the template markup into real `ix:` tagging. This is
why report templates contain aoix-specific attributes rather than hand-written Inline XBRL, and why
default aspects (entity, currency, periods, numeric transforms) are passed to the template as an
`aoix` dict rather than applied per fact.

### Taxonomy layer (`mireport/taxonomy.py`)

`loadBuiltInTaxonomyJSON()` must run before `getTaxonomy(entryPoint)` — every entry point (CLI, web
app, test conftest) does this first. Taxonomies live in a module-level registry keyed by entry-point
URL; the Excel workbook names its entry point, which is how the right taxonomy version is chosen.
`Taxonomy.resolveConcept(text, by_qname=/by_name=/by_label=...)` is the single lookup used to turn
strings from Excel or JSON config into `Concept`s; it filters candidates before checking ambiguity and
raises `AmbiguousComponentException` when several survive.

### Disclosure config (`mireport/data/disclosures/vsme.json`)

Behaviour that varies per taxonomy/template lives in JSON, not code: which named ranges supply the
aoix defaults and reporting periods, report title/subtitle ranges, entity-scheme label→URI mapping,
data-type→unit and concept→unit maps, unit-id→complex-unit definitions, cell-value→taxonomy-label
aliases, and the layout strategy. Loaded as `VSME_DEFAULTS`, indexed by entry point via
`getDisclosureConfig()`, and parsed once into the typed `ConverterConfig`. Prefer extending this JSON
over hard-coding template specifics.

### Excel reading (`mireport/xlsx_template_reader/`)

Public surface is just `XlsxProcessor` and `TemplateCheckResult`; every `_`-prefixed module is
internal. The chain, in order:

1. **`_reader.WorkbookReader`** — the only thing that touches openpyxl cells. Returns `CellValue`
   objects (`.hasValue`, `.as_str(fallback=...)`, `.as_date()`), tracks which defined names went unused,
   and normalises Excel oddities (`#VALUE!`, `-`, error codes).
2. **`_binder.WorkbookBinder`** — scrapes defined names against the taxonomy and produces
   `WorkbookBindings`: simple concept ranges, hypercube `TableBinding`s, footnote bindings, and the set
   of concepts flagged as externally-valued. Named-range conventions: a range named for a concept's
   local name binds to that concept; `template_*` ranges are template metadata; `enum_*` ranges are
   ignored. `_constants.TAXONOMY_NAME_ALIASES` / `UNHANDLED_NAMES_TO_IGNORE` hold the temporary VSME
   workarounds.
3. **`_fact_creator.FactCreator`** — walks the bindings and builds facts, delegating hypercube tables
   to `_tables.TableFactCreator` (one fact per row, dimensions read from the same row), units to
   `_units.UnitResolver` (documented resolution ladder), member-label resolution to
   `_enumerations.resolveMemberByLabel` (exact standard label → configured alias → closest EE-domain
   match, scoped to the relevant domain), and footnotes to `_footnotes.FootnoteFactCreator`.

The ordering inside the fact-creation ladder is load-bearing: externally-valued concepts are
registered as partial facts even when their cell is empty, so checks against `value is None` there are
not dead code.

### Results and messaging (`mireport/conversionresults.py`)

Nothing prints or raises its way through the conversion. Both front ends create a
`ConversionResultsBuilder`, pass it down, and everything reports through it — `Severity` ×
`MessageType` (Excel Parsing / Conversion / XBRL Validation / Dev Info / Progress), usually with an
Excel cell reference so the UI can point users at the offending cell. `Messenger` is the terse wrapper
used inside the xlsx reader. Errors accumulate; `abortEarlyIfErrors()` raises `EarlyAbortException` at
defined checkpoints rather than failing at the first problem. Dev-info messages are hidden from users
(`--devinfo` on the CLI reveals them).

### Layout (`mireport/report/layout.py`, `disclosure_layout.py`)

The report's structure is derived from the taxonomy's presentation linkbase:
`ReportLayoutOrganiser.organise()` turns presentation groups into sections and tables, guided by a
`DisclosureLayoutStrategy` selected per entry point (`layoutStrategy` in the disclosure JSON), which
also builds the table of contents and section labels.

### Arelle boundary (`mireport/arelle/`)

All Arelle usage is confined here. `report_info.ArelleReportProcessor` validates report packages and
generates viewers/xBRL-JSON via `arelle.api.Session`; `taxonomy_info` is the build-time side that
produces the taxonomy JSON; `support.py` adapts Arelle to the rest of the codebase —
`ArelleProcessingResult` translates Arelle log records into `Message`s, and
`ArelleQNameCanonicaliser` maps Arelle's per-document QName prefixes onto `mireport.xml.QName`'s
one-prefix-per-namespace model. Arelle model objects are lxml elements: test them with `is not None`,
never for truthiness (a childless element is falsy).

### Partial / external facts

Some disclosures take their value from an uploaded Word document rather than a cell. Those concepts
are registered via `InlineReport.addPartialFact()` and must be completed with
`completePartialFact()`/`replaceFactValue()`; `getInlineReport()` refuses to render while any remain.
The web app implements this as the `partial-facts` upload page, the CLI as `--extra-data`
(`replacementTextblockValues`, which also covers footnotes, label overrides and intro/back-cover
matter, converted from `.docx` by mammoth).

### Web app (`digital_converter_webapp/`)

`create_app()` loads taxonomies, configures a server-side session backend (filesystem by default,
redis via the `redis` extra), discovers taxonomy packages for offline Arelle, and registers the single
`basic` blueprint. Per-upload state and generated artifacts live in the server-side session keyed by a
conversion id (`/conversions/<id>`, `/downloadFile/<id>/<ftype>/`, `/viewer/<id>/`). Config comes from
`.env` + `FLASK_*` env vars in normal operation; tests pass an explicit `test_config` mapping so they
never pick up a developer's `.env`.

## Conventions

- `from __future__ import annotations` at the top of modules, with type-only imports under
  `if TYPE_CHECKING:`. Never use string annotations.
- Naming: PascalCase classes, camelCase methods, snake_case properties and module-level functions.
  Existing files mix camelCase methods with snake_case properties — match the file you're editing.
- Optional-value APIs take a keyword-only `fallback=`, not `default=` (e.g.
  `CellValue.as_str(fallback="")`).
- Keep `mireport` free of Flask, and keep Arelle imports inside `mireport/arelle/`.
- Ruff: line length 88, isort enabled (`extend-select = ["I"]`).
- Sample and test workbooks: `digital-templates/` (shipped templates + samples, versioned by template
  version) and `tests/data/`.
