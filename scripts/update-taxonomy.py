import argparse
import json
import os
import sys
import time
import warnings
from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from tempfile import TemporaryDirectory

from rich.markup import escape
from rich.table import Table
from rich.text import Text

from mireport.arelle.support import ArelleProcessingResult
from mireport.arelle.taxonomy_info import callArelleForTaxonomyInfo
from mireport.cli import (
    configure_rich_output,
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
from mireport.diagnostics import Diagnostic
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


def checkTaxonomyJson(taxonomy_json_path: Path) -> None:
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        taxonomy = loadTaxonomyJSON(taxonomy_json_path)
    diagnostics = [
        *diagnosticsFromWarnings(caught),
        *TaxonomyChecker(taxonomy).reportIssues(),
    ]
    printDiagnosticTable("Taxonomy checker findings", diagnostics)


def regenerateOne(
    entryPointSet: EntryPointSet,
    taxonomy_zips: Sequence[Path],
    out_path: Path,
    utr_path: Path | None,
    *,
    checkJson: bool,
    targetPath: Path | None = None,
) -> ArelleProcessingResult:
    """targetPath: where the JSON ends up, when out_path is only a staging
    file (batch mode), so the banner names the file being regenerated."""
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
    results = callArelleForTaxonomyInfo(
        entryPointSet, taxonomy_zips, out_path, utr_path
    )
    printMessages(results)
    printDiagnosticTable("Taxonomy diagnostics", results.diagnostics)

    elapsed = (time.perf_counter_ns() - start) / 1_000_000_000
    print(f"Finished querying Arelle ({elapsed:,.2f} seconds elapsed).")

    if checkJson:
        if out_path.exists():
            print("Checking taxonomy JSON")
            checkTaxonomyJson(out_path)
        else:
            print("Skipping --check-json: taxonomy JSON was not written.")

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
    )


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

    regenerateOne(
        entryPointSet,
        taxonomy_zips,
        taxonomy_json_path,
        utr_json_path,
        checkJson=args.check_json,
    )


if __name__ == "__main__":
    configure_rich_output()
    main()
