import argparse
import sys
import time
import warnings
from collections.abc import Sequence
from pathlib import Path

from rich.markup import escape

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
from mireport.exceptions import TaxonomyException
from mireport.taxonomy import loadTaxonomyJSON
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
        "taxonomy_json_path",
        type=Path,
        help="Path to the taxonomy JSON file to be created.",
    )
    parser.add_argument(
        "taxonomy_zips",
        type=str,
        nargs="+",
        help="Path to the taxonomy zip files to be used (globs, *.zip, are permitted).",
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
        help="Entry point to the taxonomy. Repeat it for an entry point that names "
        "several documents. If omitted, you are prompted to pick one of the entry "
        "points declared by the taxonomy packages.",
    )
    parser.add_argument(
        "--list-entry-points",
        action="store_true",
        help="List the entry points declared by the taxonomy packages and exit.",
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


def main() -> None:
    cli = parser()
    args = cli.parse_args()
    taxonomy_json_path: str = args.taxonomy_json_path
    taxonomy_zips: list[str] = args.taxonomy_zips
    utr_json_path: str | None = args.utr_output
    # action="append" gives a list; pickEntryPointFromPackages() gives a tuple.
    entry_point: Sequence[str] | None = args.entry_point

    taxonomy_zips = validateTaxonomyPackages(taxonomy_zips, cli)

    if args.list_entry_points:
        printEntryPointTable(getEntryPointsFromPackages(taxonomy_zips, cli))
        raise SystemExit(0)

    if entry_point is None:
        if not sys.stdin.isatty():
            cli.error(
                "--entry-point is required when not running interactively. "
                "Use --list-entry-points to see the available entry points."
            )
        entry_point = pickEntryPointFromPackages(taxonomy_zips, cli)

    print(
        "Using:",
        "Taxonomy entry point:\n\t\t{}".format("\n\t\t".join(entry_point)),
        f"Taxonomy JSON path: {taxonomy_json_path}",
        f"Taxonomy packages:\n\t\t{' '.join(taxonomy_zips)}",
        f"UTR JSON path: {utr_json_path}"
        if utr_json_path
        else "No UTR processing requested",
        sep="\n\t",
    )

    start = time.perf_counter_ns()

    print("Calling into Arelle")
    results = callArelleForTaxonomyInfo(
        entry_point, taxonomy_zips, taxonomy_json_path, utr_json_path
    )
    printMessages(results)
    printDiagnosticTable("Taxonomy diagnostics", results.diagnostics)

    elapsed = (time.perf_counter_ns() - start) / 1_000_000_000
    print(f"Finished querying Arelle ({elapsed:,.2f} seconds elapsed).")

    if args.check_json:
        if Path(taxonomy_json_path).exists():
            print("Checking taxonomy JSON")
            checkTaxonomyJson(Path(taxonomy_json_path))
        else:
            print("Skipping --check-json: taxonomy JSON was not written.")


if __name__ == "__main__":
    configure_rich_output()
    main()
