import argparse
import html
import json
import logging
import os
import shutil
import sys
import time
import warnings
from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from rich.markup import escape
from rich.table import Table
from rich.text import Text

from mireport.arelle.support import ArelleProcessingResult
from mireport.arelle.taxonomy_info import callArelleForTaxonomyInfo
from mireport.cli import (
    configure_rich_output,
    diagnosticHtml,
    diagnosticMarkdown,
    get_console,
    getEntryPointsFromPackages,
    pickEntryPointFromPackages,
    printDiagnosticTable,
    printEntryPointTable,
    validateTaxonomyPackages,
)
from mireport.cli import (
    console_print as print,
)
from mireport.conversionresults import Severity
from mireport.diagnostics import AbstractDiagnostic, Diagnostic
from mireport.entrypoints import EntryPointSet, entryPointSetOf
from mireport.exceptions import TaxonomyException
from mireport.taxonomy import builtInTaxonomyJsonPaths, loadTaxonomyJSON
from mireport.taxonomy_checker import TaxonomyChecker

SEVERITY_STYLES = {
    Severity.ERROR: "bold red",
    Severity.WARNING: "yellow",
    Severity.INFO: "dim",
}


def parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Extract taxonomy information from zip files and save to JSON file."
    )
    parser.add_argument(
        "taxonomy_zips",
        type=str,
        nargs="+",
        help="Path to the taxonomy zip files to be used (globs such as *.zip, and directories of zips, are permitted).",
    )
    # Not required=True: main() checks that one mode was given, after first
    # catching the removed `update-taxonomy.py out.json *.zip` form.
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--output",
        type=Path,
        metavar="TAXONOMY_JSON",
        help="Path to the taxonomy JSON file to be created.",
    )
    mode.add_argument(
        "--regenerate-builtin",
        action="store_true",
        help="Regenerate every built-in taxonomy JSON in src/mireport/data/taxonomies "
        "in place, each from the entry point it records. The taxonomy packages "
        "must include the supporting packages (codelists, country, nace, ...). A "
        "file is only replaced when regeneration succeeds and its content has "
        "changed.",
    )
    mode.add_argument(
        "--list-entry-points",
        action="store_true",
        help="List the entry points declared by the taxonomy packages and exit.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="With --regenerate-builtin: regenerate to a temporary file and report "
        "which built-in files would change, without replacing any. Exits 1 if any "
        "would change.",
    )
    parser.add_argument(
        "--utr-output",
        type=Path,
        default=None,
        help="Path to the UTR JSON file to be used.",
    )
    parser.add_argument(
        "--entry-point",
        type=str,
        action="append",
        default=None,
        help="With --output: entry point to the taxonomy. Repeat it for an entry "
        "point that names several documents. If omitted, you are prompted to pick "
        "one of the entry points declared by the taxonomy packages.",
    )
    parser.add_argument(
        "--status-report",
        type=Path,
        default=None,
        metavar="PATH",
        help="With --output: also write the diagnostics to PATH as tables, in the "
        f"format given by its extension ({', '.join(STATUS_REPORT_WRITERS)}). "
        "Markdown renders in GitHub issues and pull requests; for Teams, which "
        "does not render pasted markdown, write HTML, open it in a browser, copy "
        "and paste. The console output is unchanged.",
    )
    parser.add_argument(
        "--check-json",
        action="store_true",
        help="After writing the taxonomy JSON, load it back with loadTaxonomyJSON() "
        "and run TaxonomyChecker over it, so load-time issues (unsupported base "
        "sets, inconsistent dimension domains, ...) are reported immediately.",
    )
    return parser


def printMessages(results: ArelleProcessingResult) -> None:
    console = get_console()
    for message in results.messages:
        style = SEVERITY_STYLES.get(message.severity, "")
        console.print(
            f"\t[{style}]{message.severity.value:7s}[/] {escape(message.messageText)}"
        )


def diagnosticsFromWarnings(
    caught: Sequence[warnings.WarningMessage],
) -> list[Diagnostic]:
    """Turn the warnings.warn(UserWarning(TaxonomyException(...))) issued by
    Taxonomy.__init__ for unsupported base sets into Diagnostics, using the
    exception's notes for the per-role detail."""
    diagnostics = []
    for caughtWarning in caught:
        message = caughtWarning.message
        exc = message.args[0] if isinstance(message, Warning) and message.args else None
        if isinstance(exc, TaxonomyException):
            notes = "\n".join(getattr(exc, "__notes__", ()))
            diagnostics.append(Diagnostic.warning(str(exc), hint=notes or None))
        else:
            diagnostics.append(Diagnostic.warning(str(message)))
    return diagnostics


DIAGNOSTICS_TITLE = "Taxonomy diagnostics"
CHECKER_TITLE = "Taxonomy checker findings"


def checkTaxonomyJson(taxonomy_json_path: Path) -> list[Diagnostic]:
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        taxonomy = loadTaxonomyJSON(taxonomy_json_path)
    diagnostics = [
        *diagnosticsFromWarnings(caught),
        *TaxonomyChecker(taxonomy).reportIssues(),
    ]
    printDiagnosticTable(CHECKER_TITLE, diagnostics)
    return diagnostics


Sections = Sequence[tuple[str, Sequence[AbstractDiagnostic[Any]]]]


def writeMarkdownStatusReport(
    path: Path, documents: Sequence[str], sections: Sections
) -> None:
    """Write the diagnostics as markdown, for GitHub issues and pull requests."""
    entryPoint = ", ".join(f"`{document}`" for document in documents)
    parts = [f"## Taxonomy update: {entryPoint}\n"]
    parts.extend(diagnosticMarkdown(title, found) for title, found in sections)
    path.write_text("\n".join(parts), encoding="utf-8")
    print(f"Diagnostics written as markdown to {path}")


def writeHtmlStatusReport(
    path: Path, documents: Sequence[str], sections: Sections
) -> None:
    """Write the diagnostics as HTML: open it in a browser, copy and paste into
    Teams, which keeps the table."""
    entryPoint = ", ".join(
        f"<code>{html.escape(document)}</code>" for document in documents
    )
    parts = [
        (
            '<!DOCTYPE html>\n<html lang="en">\n<head><meta charset="utf-8">'
            "<title>Taxonomy update status</title></head>\n<body>"
        ),
        f"<h2>Taxonomy update: {entryPoint}</h2>",
        *(diagnosticHtml(title, found) for title, found in sections),
        "</body>\n</html>\n",
    ]
    path.write_text("\n".join(parts), encoding="utf-8")
    print(f"Diagnostics written as HTML to {path}")


STATUS_REPORT_WRITERS = {
    ".md": writeMarkdownStatusReport,
    ".markdown": writeMarkdownStatusReport,
    ".html": writeHtmlStatusReport,
    ".htm": writeHtmlStatusReport,
}


def writeStatusReport(path: Path, documents: Sequence[str], sections: Sections) -> None:
    """Write the status report in the format the extension of path names."""
    STATUS_REPORT_WRITERS[path.suffix.lower()](path, documents, sections)


def regenerateOne(
    entryPointSet: EntryPointSet,
    taxonomy_zips: Sequence[Path],
    out_path: Path,
    utr_path: Path | None,
    *,
    checkJson: bool,
    targetPath: Path | None = None,
    statusReportPath: Path | None = None,
) -> ArelleProcessingResult:
    """targetPath: where the JSON ends up, when out_path is only a staging
    file (batch mode), so the banner names the file being regenerated.

    statusReportPath: where to also write the diagnostics, as markdown or HTML
    according to its extension (see writeStatusReport)."""
    documents = sorted(entryPointSet)
    print(
        "Using:",
        f"Taxonomy entry point: {documents[0]}"
        if len(documents) == 1
        else "Taxonomy entry point:\n\t\t{}".format("\n\t\t".join(documents)),
        f"Taxonomy JSON path: {targetPath or out_path}",
        f"UTR JSON path: {utr_path}" if utr_path else "No UTR processing requested",
        sep="\n\t",
    )

    start = time.perf_counter_ns()

    print("Calling into Arelle")
    # out_path is always a staging file (see publishIfSucceeded), so have the
    # plugin write it even when it found problems: they are all listed, and
    # the checker below can look at the data for more.
    results = callArelleForTaxonomyInfo(
        entryPointSet, taxonomy_zips, out_path, utr_path, writeDataDespiteErrors=True
    )
    printMessages(results)
    printDiagnosticTable(DIAGNOSTICS_TITLE, results.diagnostics)
    sections: list[tuple[str, Sequence[AbstractDiagnostic[Any]]]] = [
        (DIAGNOSTICS_TITLE, results.diagnostics)
    ]

    elapsed = (time.perf_counter_ns() - start) / 1_000_000_000
    print(f"Finished querying Arelle ({elapsed:,.2f} seconds elapsed).")

    if checkJson:
        if out_path.exists():
            print("Checking taxonomy JSON")
            sections.append((CHECKER_TITLE, checkTaxonomyJson(out_path)))
        else:
            print("Skipping --check-json: taxonomy JSON was not written.")

    if statusReportPath is not None:
        writeStatusReport(statusReportPath, documents, sections)

    return results


class RegenerationStatus(StrEnum):
    UNCHANGED = "unchanged"
    UPDATED = "updated"
    WOULD_CHANGE = "would change"
    FAILED = "failed"


STATUS_STYLES = {
    RegenerationStatus.UNCHANGED: "green",
    RegenerationStatus.UPDATED: "cyan",
    RegenerationStatus.WOULD_CHANGE: "yellow",
    RegenerationStatus.FAILED: "bold red",
}


@dataclass(frozen=True)
class RegenerationOutcome:
    path: Path
    entryPointSet: EntryPointSet
    status: RegenerationStatus
    seconds: float


def succeeded(results: ArelleProcessingResult, out_path: Path) -> bool:
    return (
        out_path.exists()
        and not results.has_exceptions
        and not any(m.severity == Severity.ERROR for m in results.messages)
        and not any(d.level >= logging.ERROR for d in results.diagnostics)
    )


def publishIfSucceeded(
    results: ArelleProcessingResult, staged: Path, target: Path
) -> bool:
    """Move the staged taxonomy JSON to target if the run had no problems.
    Otherwise target is left exactly as it was: a taxonomy with errors is
    reported on, but never persisted."""
    if not succeeded(results, staged):
        return False
    shutil.move(staged, target)
    return True


def regenerateBuiltIn(
    path: Path,
    entryPointSet: EntryPointSet,
    taxonomy_zips: Sequence[Path],
    utr_path: Path | None,
    *,
    checkJson: bool,
    dryRun: bool,
) -> RegenerationOutcome:
    start = time.perf_counter()
    # Same directory as the target so os.replace() is an atomic rename.
    with TemporaryDirectory(dir=path.parent, prefix=".regenerate-") as tmp:
        out_path = Path(tmp) / path.name
        results = regenerateOne(
            entryPointSet,
            taxonomy_zips,
            out_path,
            utr_path,
            checkJson=checkJson,
            targetPath=path,
        )
        if not succeeded(results, out_path):
            status = RegenerationStatus.FAILED
        elif json.loads(out_path.read_bytes()) == json.loads(path.read_bytes()):
            status = RegenerationStatus.UNCHANGED
        elif dryRun:
            status = RegenerationStatus.WOULD_CHANGE
        else:
            os.replace(out_path, path)
            status = RegenerationStatus.UPDATED
    return RegenerationOutcome(path, entryPointSet, status, time.perf_counter() - start)


def printRegenerationSummary(outcomes: Sequence[RegenerationOutcome]) -> None:
    table = Table(title="Built-in taxonomy JSON regeneration")
    table.add_column("File", no_wrap=True)
    table.add_column("Entry point")
    table.add_column("Status", no_wrap=True)
    table.add_column("Seconds", justify="right")
    for outcome in outcomes:
        style = STATUS_STYLES[outcome.status]
        # Text() not markup; one line per document of the set.
        entryPoint = Text("\n".join(sorted(outcome.entryPointSet)))
        table.add_row(
            outcome.path.name,
            entryPoint,
            f"[{style}]{outcome.status}[/]",
            f"{outcome.seconds:,.2f}",
        )
    get_console().print(table)


def regenerateAllBuiltIn(
    taxonomy_zips: Sequence[Path],
    utr_path: Path | None,
    *,
    checkJson: bool,
    dryRun: bool,
) -> int:
    outcomes = []
    for index, (path, entryPointSet) in enumerate(builtInTaxonomyJsonPaths()):
        print(f"Regenerating {path.name}")
        outcomes.append(
            regenerateBuiltIn(
                path,
                entryPointSet,
                taxonomy_zips,
                # The UTR is independent of the taxonomy: write it once.
                utr_path if index == 0 else None,
                checkJson=checkJson,
                dryRun=dryRun,
            )
        )
    printRegenerationSummary(outcomes)
    bad = {RegenerationStatus.FAILED, RegenerationStatus.WOULD_CHANGE}
    return 1 if any(o.status in bad for o in outcomes) else 0


def entryPointSetOrUsageError(
    documents: Sequence[str], cli: argparse.ArgumentParser
) -> EntryPointSet:
    """Where a single-file run's entry point becomes a set; an unusable one
    (e.g. not an absolute URI) is a usage error, not a traceback."""
    try:
        return entryPointSetOf(documents)
    except TaxonomyException as e:
        cli.error(str(e))


def main() -> None:
    cli = parser()
    args = cli.parse_args()
    taxonomy_json_path: Path | None = args.output
    utr_json_path: Path | None = args.utr_output
    # action="append" gives a list of documents.
    entry_point: Sequence[str] | None = args.entry_point

    if jsonPaths := [z for z in args.taxonomy_zips if z.lower().endswith(".json")]:
        cli.error(
            f"{jsonPaths[0]} is not a taxonomy package. The taxonomy JSON to "
            f"create is given with --output (e.g. --output {jsonPaths[0]}), not "
            "as the first positional argument."
        )
    if not (taxonomy_json_path or args.regenerate_builtin or args.list_entry_points):
        cli.error(
            "one of the arguments --output --regenerate-builtin "
            "--list-entry-points is required"
        )
    # Dependencies the mutually exclusive group cannot express.
    if entry_point is not None and taxonomy_json_path is None:
        cli.error("--entry-point is only supported with --output.")
    if args.status_report is not None:
        if taxonomy_json_path is None:
            cli.error("--status-report is only supported with --output.")
        if args.status_report.suffix.lower() not in STATUS_REPORT_WRITERS:
            cli.error(
                f"--status-report {args.status_report} must end in one of: "
                + ", ".join(STATUS_REPORT_WRITERS)
            )
    if args.dry_run and not args.regenerate_builtin:
        cli.error("--dry-run is only supported with --regenerate-builtin.")
    entryPointSet = (
        entryPointSetOrUsageError(entry_point, cli) if entry_point is not None else None
    )

    taxonomy_zips = validateTaxonomyPackages(args.taxonomy_zips, cli)

    if args.regenerate_builtin:
        raise SystemExit(
            regenerateAllBuiltIn(
                taxonomy_zips,
                utr_json_path,
                checkJson=args.check_json,
                dryRun=args.dry_run,
            )
        )

    if args.list_entry_points:
        printEntryPointTable(getEntryPointsFromPackages(taxonomy_zips, cli))
        raise SystemExit(0)

    # One mode is required, so --output is set once the other two are gone.
    assert taxonomy_json_path is not None
    if entryPointSet is None:
        if not sys.stdin.isatty():
            cli.error(
                "--entry-point is required when not running interactively. "
                "Use --list-entry-points to see the available entry points."
            )
        entryPointSet = entryPointSetOrUsageError(
            pickEntryPointFromPackages(taxonomy_zips, cli), cli
        )

    with TemporaryDirectory() as tmp:
        staged = Path(tmp) / taxonomy_json_path.name
        results = regenerateOne(
            entryPointSet,
            taxonomy_zips,
            staged,
            utr_json_path,
            checkJson=args.check_json,
            targetPath=taxonomy_json_path,
            statusReportPath=args.status_report,
        )
        if not publishIfSucceeded(results, staged, taxonomy_json_path):
            print(f"Taxonomy JSON not written to {taxonomy_json_path}: see above.")
            raise SystemExit(1)


if __name__ == "__main__":
    configure_rich_output()
    main()
