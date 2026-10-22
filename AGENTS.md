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
  (written by `mireport.xbrljson_writer`, sorted, with `mireport:scale` and the message counts as
  extension properties) from the 1.2.0 and 1.3.0 samples. Regenerate deliberately, only when a change to the output is intended:
  `.venv-py314/Scripts/python -m tests.unitTests.xlsx_template_reader.test_fact_creator_characterization`
- A test that needs a taxonomy shaped differently from the baked ones (a presentation group with no
  hypercube, say) builds it with `tests/unitTests/synthetic_taxonomy.py` and registers it with the
  `register_taxonomy` fixture, which removes it again. Don't hand-roll taxonomy JSON in a test file.
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
# Excel -> Inline XBRL (add --viewer, or --json for xBRL-JSON written from the report and validated by
# Arelle unless --skip-validation; output path may be a file or a directory)
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

### Report string translations

The report's fixed strings are translated from gettext catalogs,
`src/mireport/data/disclosures/translations/vsme/<language>.po`, collected from a template's
Translations sheet by `scripts/collect-report-translations.py` and reviewed in git. Nothing
compiles them at runtime: building the package (`hatch_build.py`, editable installs included)
compiles them into the untracked `generated/translations/`, and the wheel carries the `.mo` files
beside the catalogs. After changing a catalog in a checkout, recompile:

```powershell
.venv-py314/Scripts/python scripts/compile-report-translations.py
```

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
       ──▶ report package (.zip)  ──Arelle──▶  validation messages / viewer
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
   `_units.UnitResolver` (documented resolution ladder; a monetary concept with no currency cell takes the
   report's currency, never a UTR pick; a type that `UTR.requiresComplexUnit`, such as emissions per
   monetary unit, takes a divide unit built from `UTR.complexUnitFor`: a numerator unit typed in the unit
   cell, else for each side the report's currency for a monetary side, the `dataTypesToUnits` entry
   (`ConverterConfig.unitForDataType`, by local name) or the UTR's only unit, and no fact, with a warning
   naming the missing side, otherwise; `UTR.valid` is false for any single unit on such a type; a `False`
   from the resolver means no fact), member-label resolution to
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

Periods are OIM's: datetimes (an `InlineReport` takes only whole days, and `addPeriod` refuses a period with a time of day; a `Report` as data has no such limit), a duration running up to (not including) its end, so the start of one year *is*
the end of the one before (`mireport/report/periods.py`, the only place with day arithmetic: `fromDays` in,
`firstDay`/`lastDay` out for aoix and for people). A report declares named `ReportPeriod`s; a fact holds a
`FactPeriod`, a report period and which `PeriodPart` of it (`Whole` for a duration concept, `Start` or `End` for
an instant one). `oim` is what XBRL calls it and is what duplicates and headings compare, `part` is what the
producer meant: an opening balance of 2025 and a closing balance of 2024 are one instant but two statements,
both shown as 2024 (an instant is named as the end of the period it ends), and told to aoix as `instant-start` and `instant`. A
producer that knows says so (`setNamedPeriod(name, at=PeriodPart.Start)`); one that has only an OIM period
(xBRL-JSON) uses `setPeriod(oim)`, which `InlineReport.locatePeriod` resolves, preferring the end.

Dimensional validity (a matching hypercube; a declared typed dimension must be present; a concept in no
hypercube is unrestricted, as in XDT, so a taxonomy restricts it with a closed hypercube with no dimension;
typed value *content* is not checked) runs in `FactBuilder.buildFact`, the only place a `Fact` is made;
`FactBuilder.fromFact` copies one to change. Duplicates follow the
OIM / the "Handling Duplicate Facts" WGN
(<https://www.xbrl.org/WGN/xbrl-duplicates/WGN-2025-01-14/xbrl-duplicates-2025-01-14.html>; `report/duplicates.py`): `duplicateClass(a, b)` is complete, consistent, inconsistent, or
None (alternative facts, e.g. differing only in unit). Periods compare as OIM's (`FactPeriod.oim`), so the opening
balance of one period and the closing balance of the one before are duplicates of each other, though each keeps
its own cell and heading. `InlineReport.reportInconsistentDuplicateFacts`
reports each inconsistent set to the report preparer. A table cell keeps the first of complete duplicates,
the more precise of consistent ones, and the first with a warning otherwise; only a complete duplicate
left out counts as not lost. `tests/unitTests/table_integrity.py` checks a rendered document's structure
(no browser needed).

`setPercentageFact(fraction, decimals)` takes XBRL's own value and decimals (0.1250 at 4 is shown as 12.50
with `ix:scale -2`); `setPercentageValue` takes a spreadsheet's display decimals.

### Layout (`mireport/report/layout/`, `disclosure_layout.py`)

The report's structure is derived from the taxonomy's presentation linkbase:
`ReportLayoutOrganiser.organise()` turns presentation groups into sections, guided by a
`DisclosureLayoutStrategy` selected per entry point (`layoutStrategy` in the disclosure JSON), which also
builds the table of contents and section labels. The package is `model` (sections, tables), `grid` (one
`GridBuilder`: say which row and column each fact belongs to, and it fills the grid), `headers` (pure
functions: what a table says once and what it says per column) and `organiser`.

A presentation group is a sequence of **blocks** (`PresentationGroup.blocks`): *line items* (reportable
concepts no hypercube of the role claims) and *cubes* (one hypercube's table). The definition linkbase
decides which rows belong to which cube (the role's `HypercubeDeclaration`s, for the cubes the group
presents); the presentation supplies only their order and headings, which is why a table's line items can
sit as siblings of its hypercube. The group's style (Empty, List, Table, Hybrid) follows from its blocks,
and the organiser makes one section per block, so a role can hold several tables (the role's title and table-of-contents entry come once, from the first
block; each block's `subheading`, the abstract or cube that leads it, is an `h3` over it; later blocks are `continuation`s) and line items before, between and after them. A line-items
block is a list; a cube block with one dimension is a table on that axis. A cube with **no** dimension is
a *statement* (`GridBuilder.statement`): a row for each relationship in presentation order, a column for
each period, and a row's preferred label picks its label, its part of the period (`periodStartLabel`,
`periodEndLabel`) and its sign (`negatedLabel`: the row shows the opposite sign to the fact's value, as
aoix's `sign=flip`, so the tagged value is unchanged). A concept shows in every role that claims it. Rows are
flat, not indented by depth. A dimensional fact in a line-items block gets a table built from its own
dimensions ("... - by Target Category and Target Identifier"), so a taxonomy that defines its hypercubes
only in the definition linkbase still renders everything.
`report.requireAllFactsRendered = True` turns any fact that would appear nowhere into an error instead
of leaving it out.

### Periods and a prior period

A report has a current period (`setDefaultPeriodName`), an optional prior period (`setPriorPeriodName`),
and any other periods (such as the VSME baseline and target years); `periodRole` and `reportingPeriods` say
which is which. A fact's period (its `FactPeriod`: the report period, and
which `PeriodPart` of it) is part of its table cell, so a current and a prior value for the same
concept and dimension sit side by side, and so do the opening and the closing balance of one period (the start
of `cur` and the end of `prior` are one OIM instant, but each is its own cell and its own heading): a column holding facts from more than one period is laid out once
per period, each period said once over its columns; a list shows a concept's prior value, and an
opening balance, beside the closing one that leads the line. When every column is in a single period (one period, or baseline and target years each in
their own column) nothing changes. **The VSME Excel template reports one period** (FAQ item 7), so
comparatives come from last year's report, always a separate file, and no template contract is needed:
`mireport.comparatives.PriorReport.fromFile` reads it as a whole report (xBRL-JSON, a
workbook in its own template version, one migrated but not yet saved in Excel being refused, or
Inline XBRL or a report package (`.xbri`, `.xbr`, `.zip`) through Arelle, whose errors about it are warnings here; offline, Arelle needs last
year's taxonomy package too), and its
`mergeInto` (`mireport.report.comparatives.mergePriorReport`) makes its period this report's prior one (`--prior PATH` on
`parse-and-ixbrl.py` and `xbrljson-to-ixbrl.py`). The current report's own period is its default
duration; its other durations (baseline and target years) are left alone, but a prior period, or a
duration overlapping its own or ending where it starts, means it already has comparatives, and the merge
raises. From last year's report the one duration that ends where ours starts (at any length) is taken,
with the instants at its ends; facts in its other periods are left out with one info message. Only
non-nil numbers and booleans are copied, and any other fact in a table row with one (the same
presentation group and dimensions), each rebuilt with a `FactBuilder` so every check re-runs,
without footnotes, and each keeps its unit. A comparative is taken only beside this year's fact for
the same concept and dimensions; with nothing to compare it with it is left out. The current report wins: a comparative with a `duplicateKey` already present (the
shared closing and opening instant, say) is skipped, quietly if consistent, with a warning if not.
Another entity raises. Last year's report on another version of our taxonomy (one the same disclosure
config names, `areVersionsOfOneTaxonomy`; VS is a version of VSME) has the facts it would give matched to ours by `alignToTaxonomy`: concepts,
dimensions and members by local name. One naming something we lack, or whose concept's period type
or data type differs, is left out and counted with the merge's other skips, one warning for each reason.
Another taxonomy raises, and a merge that raises stops the conversion. The disclosure config's
`periods[].role` (`current`, the default, or `prior`) and its skip-if-absent rule stay for a future
template with prior-year cells.

### xBRL-JSON reader (`mireport/xbrljson_reader/`)

`XbrlJsonProcessor` is the counterpart of `XlsxProcessor` for a report that already exists as xBRL-JSON:
it maps OIM facts onto `FactBuilder` and returns an `InlineReport`, so the same themed Inline XBRL comes
out. `scripts/xbrljson-to-ixbrl.py` drives it. The document's `documentInfo.taxonomy` is the report's
entry point (a set of documents), and a taxonomy baked for exactly that set must already be loaded. It
reads one entity. Every duration the facts use is declared as a report period, named from its dates;
the one with the most facts is the default (a tie goes to the one that ends last). No period is ever
the prior one: every OIM fact says its own period, so the reader infers nothing (comparatives come from a separate file, above). A percent is shown
x100 with `ix:scale -2` but keeps its value and decimals as given. In strict mode (`--strict`)
anything that cannot be added or would appear nowhere in the rendered report is an error.

The document is read first into `document.XbrlJsonDocument` / `XbrlJsonFact` (shape-checked data, no
taxonomy; `fromDict`/`fromFile`, `toDict`/`toFile`); anything that writes xBRL-JSON builds one of these,
so the reader never depends on a writer. Only a document that is not xBRL-JSON raises; a fact the reader
cannot take (bad shape or period, time zone, non-finite number, unit the UTR forbids, monetary without a
currency, member outside its domain, explicit dimension at its default, a nil typed dimension value,
another language) costs that fact alone, with a warning carrying the OIM error code. `T24:00:00` is parsed by hand (`_periods.py`),
since Python before 3.14 cannot. A nil fact is read for a nillable concept.

### Report and the xBRL-JSON writer (`mireport/report/report.py`, `mireport/xbrljson_writer/`)

`Report` is a report as data (taxonomy, entity, periods, facts, footnotes, nil facts); `InlineReport`
subclasses it with everything about how it looks (locale, theme, titles, aoix, rendering, package).
`toXbrlJson(report)` writes any `Report` as the reader's `XbrlJsonDocument`, in canonical form
(`xbrl:canonicalValues`): facts in report order (or sorted, `sortFacts`) with ids `f1`..., a
dimensionless number with no unit (OIM's `xbrli:pure`), namespaces only for prefixes used, footnotes as
`xbrl:note` facts, with the `footnote` link type and group declared (xBRL-JSON predefines neither;
Arelle reports `xbrlje:unknownLinkType` otherwise). This is the only xBRL-JSON the project produces:
Arelle's `saveLoadableOIM` is not used. `scripts/parse-and-ixbrl.py --json` writes one for a converted
workbook, beside the output, and the webapp writes one for every conversion; each is validated by
`ArelleReportProcessor.validateXbrlJson` (Arelle loads OIM natively), in the webapp always and in the
script unless `--skip-validation`. `scripts/check-report.py` validates a `.json` report the same way.

### Coverage reports (`mireport/coverage_report_generator/`)

`buildCoverageReportSet` builds, as `Report`s written with the xBRL-JSON writer, the reports the
taxonomy repo pins (`no-facts`, `all-nil`, `all-values`): a fact for every reportable concept and one for each hypercube dimension value it carries,
in a taxonomy-agnostic way (everything comes from the baked `Taxonomy`). Two properties hold by
construction. **No two facts share a context** (concept, period, dimensions): a fact for dimensions already
covered is skipped, since duplicates and alternative facts do not belong in a coverage report. **No fact
carries a unit the UTR forbids**: units are checked as they are written, including types that take a complex unit (see
`UTR.complexUnitFor`: emissions per monetary unit and the like have no named unit and are reported in a divide
unit such as `utr:tCO2e/iso4217:EUR`; leaving them unitless would read as `xbrli:pure`). What each fact
*shows* is a per-concept rotation: the first fact takes the concept's usual unit and value, each further
one (each dimension value) takes the next variant, namely another permitted unit or an enumeration set's
empty selection. A variant no dimension fact reached (a concept with no dimension) goes at the
proof-of-reportability dimensions in an earlier period, one further year back each
(`SampleEntityPeriod.shifted`), so it has a context of its own; a later shifted period that holds an instant
but no duration gets an anchor duration fact, because the reader needs an instant at the start or end of a
declared duration.

### Arelle boundary (`mireport/arelle/`)

All Arelle usage is confined here. `report_info.ArelleReportProcessor` validates report packages and
generates viewers and validates packages and xBRL-JSON via `arelle.api.Session`; `taxonomy_info` is the build-time side that
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
  `normaliseDocument()` (`mireport/html_to_xhtml/`) injects an empty `ix:header` inside a
  `display:none` div (the form §8.1.2 recommends; only one header across the whole set need carry anything, §8.1.3) and repairs
  what the converters emit: `data-*`, `<meta charset>` and the `div` hit area inside every link
  annotation, which becomes a `span` (pdf2htmlEX); `lang`,
  `bgcolor`/`link`/`vlink`, `<a name>`, and body-level `<style>` (pdftohtml). Every rule there came
  from a real validation error, not a precaution
  — don't remove one without re-running `tests/integrationTests/test_real_pdf_converter.py`. An
  element outside XHTML Strict is *refused* (`rejectUnknownElements`), never translated on a guess,
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
`completePartialFact()` (`replaceFactValue()` only replaces a built fact); `getInlineReport()`
refuses to render while any remain.
The web app implements this as the `partial-facts` upload page, the CLI as `--extra-data`
(`replacementTextblockValues`, which also covers footnotes, label overrides and intro/back-cover
matter, converted from `.docx` by mammoth).
Both convert through `mireport/docx_converter/`. The web app runs mammoth in a child process (`_worker.py`)
that is killed after `DEFAULT_TIMEOUT_SECONDS`, since a crafted document can keep mammoth's regexes busy
and a thread can't be stopped; a document it can't convert stops the conversion with an error. The CLI's
user supplies their own documents, so it converts in-process. `DocxConversion.addTo` makes mammoth's style
messages (only formatting lost) DevInfo, and the rest one deduplicated warning per document. mammoth's
HTML then goes through `mireport.html_to_xhtml.normaliseFragment`: the same XHTML Strict rules as a PDF
annex (alt on every image, `<s>` and checkboxes translated, script stripped) without the document assembly.

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
| `conversion_service.py` | The Excel → Inline XBRL pipeline (`doConversion`, which also writes the report as xBRL-JSON and has Arelle validate the package and the JSON) and the viewer generated from it. Flask-free: the caller passes in the Arelle processor and PDF mode |
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

Last year's report, the upload's optional `prior` file, is `Conversion.priorReport`. `doConversion`
merges it (`PriorReport.fromFile(...).mergeInto(...)`) once this year's Word documents are in, and one that can't be
merged stops the conversion. With migration on, an older prior workbook is offered the migration page
too, after this year's: `conv.prior` is the same conversion seen from last year's workbook, with its
own migration state (`prior_` keys), which the migration routes use when given `?prior=True`;
`skip_prior_migration` uses the workbook as it is.

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
