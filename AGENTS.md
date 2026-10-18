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

Tests run in parallel by default (`-n auto --dist loadgroup` is in pytest's `addopts`, matching CI);
`loadgroup` keeps tests that share a slow fixture on one worker. Pass `-n0` to run serially, which is
quicker for a single test and needed for a debugger.

```powershell
.venv-py314/Scripts/python -m pytest tests/unitTests
.venv-py314/Scripts/python -m pytest tests/integrationTests
.venv-py314/Scripts/python -m pytest tests/webappTests

# single test / single case
.venv-py314/Scripts/python -m pytest tests/unitTests/xlsx_template_reader/test_ranges.py::test_name -n0
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

Supplementary PDFs are supplied to the CLI through `--extra-data`, as
`{"pdfAttachments": [{"path": "annex1.pdf"}]}` with paths relative to the JSON file. Converting them
for display needs `pdf2htmlEX` or `pdftohtml` on PATH, or `PDF_CONVERTER_PATH` (environment or
`.env`, no `FLASK_` prefix — `findConverter()` reads it, so both front ends resolve the same binary);
without any of those they are attached unconverted and a warning is issued. The web app also has
`FLASK_ENABLE_SUPPLEMENTARY_PDFS`, on unless set false, which removes the upload field entirely.

### Regenerating taxonomy data

Taxonomy metadata is **pre-baked JSON** in `src/mireport/data/taxonomies/`; nothing at runtime reads
`.xsd`/linkbases. The JSON is produced by Arelle from taxonomy package zips (which are not in this
repo):

```powershell
# one new taxonomy
.venv-py314/Scripts/python scripts/update-taxonomy.py --entry-point <URL> --output src/mireport/data/taxonomies/vsme-YYYY-MM-DD.json path/to/*.zip

# every built-in JSON, each from the entry point it records (--dry-run: report, don't replace)
.venv-py314/Scripts/python scripts/update-taxonomy.py --regenerate-builtin ../webapp_taxonomies/*.zip
```

`scripts/dump-taxonomy.py` dumps concept/presentation info (including to xlsx) from the baked JSON.

The Unit Type Registry JSON (`src/mireport/data/registries/utr.json`) is baked
separately, from `utr.xml` read by `mireport.utr_xml`. It needs no taxonomy
packages and fetches <https://www.xbrl.org/utr/utr.xml> unless told otherwise:

```powershell
.venv-py314/Scripts/python scripts/update-taxonomy.py --utr-output src/mireport/data/registries/utr.json
# --utr-source <URL or local utr.xml> to read from somewhere else
```

A taxonomy run can also write `--status-report report.md` (or `.html`, which pastes into Teams as
a table) with everything it found.

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

### The fact model (`mireport/report/model.py`, `fact.py`, `factbuilder.py`, `aoix.py`)

A `Fact` is typed values, not aoix strings: `concept`, `value`, a `FactPeriod`, a `Unit` (measures over
measures; `Unit.fromUnitString("(a*b)/c", resolve)` / `unit.toUnitString()` are the strict OIM Common unit
string), `decimals`, `scale`, `ExplicitDimensionValue`s, `TypedDimensionValue`s (the member's text,
unescaped) and enumeration members. Build one with a `FactBuilder`, which keeps its setters
(`setSimpleUnit`, `setCurrency`, `setNamedPeriod`, `setTypedDimension`,
`setEnumerationValue`/`setEnumerationSet`...) and does the validation. **aoix text is written in exactly
one place, `mireport/report/aoix.py`**, from those fields and in the current syntax only
(`typed-value-wrapper=`, `units=a/b`); `period-type`, `escape`, `transform` and `fn-refs` are derived there,
not stored. A typed value that aoix cannot quote (a double quote) is refused rather than written with the
deprecated `typed` keyword. A monetary fact carries its currency (the report's default, unless it says
otherwise).

Periods are OIM's: datetimes, a duration running up to (not including) its end, so the start of one year *is*
the end of the one before (`mireport/report/periods.py`, the only place with day arithmetic: `fromDays` in,
`firstDay`/`lastDay` out for aoix and for people). A report declares named `ReportPeriod`s; a fact holds a
`FactPeriod`, a report period and which `PeriodPart` of it (`Whole` for a duration concept, `Start` or `End` for
an instant one). `oim` is what XBRL calls it and is what duplicates and headings compare, `part` is what the
producer meant: an opening balance of 2025 and a closing balance of 2024 are one instant but two statements,
shown as the first day of 2025 and the last of 2024, and told to aoix as `instant-start` and `instant`. A
producer that knows says so (`setNamedPeriod(name, at=PeriodPart.Start)`); one that has only an OIM period
(xBRL-JSON) uses `setPeriod(oim)`, which `InlineReport.locatePeriod` resolves, preferring the end.

`setPercentageFact(fraction, decimals)` takes XBRL's own value and decimals (0.1250 at 4 is shown as 12.50
with `ix:scale -2`); `setPercentageValue` takes a spreadsheet's display decimals.

### Layout (`mireport/report/layout/`, `disclosure_layout.py`)

The report's structure is derived from the taxonomy's presentation linkbase:
`ReportLayoutOrganiser.organise()` turns presentation groups into sections, guided by a
`DisclosureLayoutStrategy` selected per entry point (`layoutStrategy` in the disclosure JSON), which also
builds the table of contents and section labels. The package is `model` (sections, tables), `grid` (one
`GridBuilder`: say which row and column each fact belongs to, and it fills the grid), `headers` (pure
functions: what a table says once and what it says per column) and `organiser`. A *list* group shows one
line per concept; a *table* group follows its hypercube. A dimensional fact in a list group gets a table
built from its own dimensions ("... - by Target Category and Target Identifier"), so a taxonomy that
defines its hypercubes only in the definition linkbase still renders everything.
`report.requireAllFactsRendered = True` turns any fact that would appear nowhere into an error instead
of leaving it out.

### Arelle boundary (`mireport/arelle/`)

All Arelle usage is confined here. `report_info.ArelleReportProcessor` validates report packages and
generates viewers/xBRL-JSON via `arelle.api.Session`; `taxonomy_info` is the build-time side that
produces the taxonomy JSON; `support.py` adapts Arelle to the rest of the codebase —
`ArelleProcessingResult` translates Arelle log records into `Message`s, and
`ArelleQNameCanonicaliser` maps Arelle's per-document QName prefixes onto `mireport.xml.QName`'s
one-prefix-per-namespace model. Arelle model objects are lxml elements: test them with `is not None`,
never for truthiness (a childless element is falsy).

### Supplementary PDFs (`mireport/pdf_converter/`)

Optional PDFs uploaded alongside the workbook. Each is **always** attached to the report package
verbatim under `{top}/attachments/`; where a converter is available each is *also* converted to XHTML
and added to the report's **Inline XBRL document set**. Conversion failures (including "no converter
installed") are `Severity.WARNING`, never errors, so the feature degrades to "the PDF still ships".

Two converters are supported and the best available is chosen at runtime by
`findConverter()` — see `_backends.py`, where adding a third means adding a subclass and a registry
entry and nothing else:

- **pdf2htmlEX**, preferred: keeps the PDF's own fonts and selectable text.
- **pdftohtml** (Poppler), fallback: renders each page to a bitmap. Lower fidelity but available on
  Windows. This build has **no `-dataurls`**, so `_inline.py` inlines its page images as `data:` URIs
  from the working directory before it is destroyed — that step runs for every backend.

`-xml` is not usable for this: it emits Poppler's own `pdf2xml` extraction format, not XHTML, and
still writes external images.

The document set is what forces the package layout in `mireport/report/reportpackage.py`. The first
three reasons are Arelle's, not ours; the fourth is the iXBRL specification's, with Arelle merely the
processor that enforces it here:

- a file directly under `{top}/reports/` is its own entry point, so the tagged report moves down into
  `{top}/reports/{stem}/` alongside its members and nothing is left at the top of `reports/`;
- `_makeOptions()` in `arelle/report_info.py` always enables the `inlineXbrlDocumentSet` plug-in;
  without it Arelle refuses to load a multi-file report entry at all;
- `generateInlineViewer()` gets one output file per set member, so its extras are carried on
  `ArelleProcessingResult.viewer_document_set_members` and must be served under their own filenames;
- an Inline XBRL Document is *defined* as a well-formed XML document containing Inline XBRL Elements
  (iXBRL 1.1 §3.1), so a member needs at least one `ix:` element — a namespace declaration is not
  enough — and a Validating Conformant Processor accepts only documents valid against the
  XHTML + iXBRL schema (§3.3.1), i.e. **XHTML Strict**. Arelle reports the first as
  `arelle:nonIxdsDocument` and the second via `XhtmlValidate.xhtmlValidate`. So
  `_xhtml.normaliseToXhtml()` injects an empty `ix:header` inside a `display:none` div (the form
  §8.1.2 recommends; only one header across the whole set need carry anything, §8.1.3) and repairs
  what the converters emit: `data-*`, `<meta charset>` and the `div` hit area inside every link
  annotation, which becomes a `span` (pdf2htmlEX); `lang`,
  `bgcolor`/`link`/`vlink`, `<a name>`, and body-level `<style>` (pdftohtml). Every rule there came
  from a real validation error, not a precaution
  — don't remove one without re-running `tests/integrationTests/test_real_pdf_converter.py`. An
  element outside XHTML Strict is *refused* (`_rejectUnknownElements`), never translated on a guess,
  as are `object`/`param`/`form`/`input`/`button`/`select`/`textarea`/`base` — Strict defines them,
  but neither converter emits them and each embeds, submits or re-points URLs.
- **Script is stripped entirely** — the `<script>` element, `on*` handler attributes and
  `javascript:` URLs. The last two are the one preventive rule here: neither converter emits them,
  but members are served same-origin by the web app, which also sends them a `script-src 'none'`
  CSP (`MEMBER_CSP`; the viewer itself is exempt, being script).

Tests substitute `tests/data/stub-pdf-converter.py` for the real binaries via executable shims named
after each backend, so the subprocess layer and *both* command lines are genuinely exercised on
Windows. `tests/integrationTests/test_real_pdf_converter.py` is the only test that runs a real
converter, and skips when neither is on PATH.

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

`__init__.py` holds only `create_app()`. The rest is split by concern:

| Module | Owns |
|---|---|
| `routes.py` | The routes and request hooks on the `basic` blueprint, and `register_routes(app)`, which `create_app` calls. Handlers stay thin. It imports `migration`, so the migration routes come with it |
| `app_setup.py` | Startup configuration: logging, feature flags, session backend, Arelle/taxonomy packages, PDF converter report, `brokenApp`, `getArelle`, `DEPLOYMENT_DATETIME` |
| `conversion.py` | `Conversion` / `ConversionStore`, the typed view over the per-id session dicts |
| `conversion_service.py` | The Excel → Inline XBRL pipeline (`doConversion`) and the viewer / xBRL-JSON generated from it. Flask-free: the caller passes in the Arelle processor and PDF mode |
| `captcha.py` | The id-keyed math captcha and the captcha + CSRF check for `/upload` |
| `forms.py` | Upload limits (`MAX_FILE_SIZE`, `MAX_PDF_COUNT`), image field map, form parsing |
| `viewer.py` | Serving the viewer and its document set members (with the member CSP) |
| `migration.py` | Template migration checks and routes |
| `locales.py`, `template_helpers.py` | Locale list for the UI; Jinja helpers |

Route handlers reach conversion state through `ConversionStore.fromFlask()` and the `Conversion`
properties, never raw `session[id]` dicts. Its setters mark the session modified, which a nested dict
write would not do on its own, and they keep stored values to what the session backend can round-trip
(it hands `NamedTuple`s back as lists, hence `FilelikeAndFileName.from_tuple` on every read). Only the
CSRF token and captcha answers are read from `session` directly.

Web app tests are grouped by the module they exercise: Flask-free modules in
`tests/unitTests/digital_converter_webapp/`, routes and end-to-end behaviour in `tests/webappTests/`
(`test_upload.py`, `test_conversion_service.py`, `test_viewer.py`, …). Shared helpers are fixtures in
`tests/webappTests/conftest.py` (`upload`, `make_pdf`, `conversion_of`, the session-scoped
`converted_id`).

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
