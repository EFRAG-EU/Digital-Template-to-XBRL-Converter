import argparse
import logging
import time
from collections import Counter
from pathlib import Path

from rich.markup import escape
from rich.table import Table

from mireport.arelle.diagnostics import Diagnostic
from mireport.arelle.support import ArelleProcessingResult
from mireport.arelle.taxonomy_info import callArelleForTaxonomyInfo
from mireport.cli import (
    configure_rich_output,
    get_console,
    validateTaxonomyPackages,
)
from mireport.cli import (
    console_print as print,
)
from mireport.conversionresults import Severity

SEVERITY_STYLES = {
    Severity.ERROR: "bold red",
    Severity.WARNING: "yellow",
    Severity.INFO: "dim",
}

LEVEL_STYLES = {
    logging.ERROR: "bold red",
    logging.WARNING: "yellow",
    logging.INFO: "dim",
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
        required=True,
        help="Entry point to the taxonomy.",
    )
    return parser


def printMessages(results: ArelleProcessingResult) -> None:
    console = get_console()
    for message in results.messages:
        style = SEVERITY_STYLES.get(message.severity, "")
        console.print(
            f"\t[{style}]{message.severity.value:7s}[/] {escape(message.messageText)}"
        )


def levelName(level: int) -> str:
    return logging.getLevelName(level).title()


def diagnosticDetails(diagnostic: Diagnostic) -> str:
    lines = [f"{key}: {value}" for key, value in diagnostic.details.items()]
    if diagnostic.hint is not None:
        lines.append(f"hint: {diagnostic.hint}")
    return "\n".join(lines)


def printDiagnostics(results: ArelleProcessingResult) -> None:
    diagnostics = results.diagnostics
    if not diagnostics:
        print("No diagnostics from taxonomy extraction.")
        return

    table = Table(title="Taxonomy diagnostics", show_lines=True)
    table.add_column("Level", no_wrap=True)
    table.add_column("Message", max_width=40)
    table.add_column("ELR", overflow="fold")
    table.add_column("Concepts", overflow="fold")
    table.add_column("Details", overflow="fold")

    for diagnostic in sorted(diagnostics, key=lambda d: -d.level):
        table.add_row(
            f"[{LEVEL_STYLES.get(diagnostic.level, '')}]{levelName(diagnostic.level)}[/]",
            escape(diagnostic.text),
            escape(diagnostic.elr or ""),
            escape("\n".join(str(qname) for qname in diagnostic.concepts)),
            escape(diagnosticDetails(diagnostic)),
        )
    get_console().print(table)

    counts = Counter(levelName(diagnostic.level) for diagnostic in diagnostics)
    summary = ", ".join(
        f"{count} {name.lower()}{'s' if count != 1 else ''}"
        for name, count in counts.most_common()
    )
    print(f"{summary} from taxonomy extraction.")


def main() -> None:
    cli = parser()
    args = cli.parse_args()
    taxonomy_json_path = args.taxonomy_json_path
    taxonomy_zips = args.taxonomy_zips
    utr_json_path = args.utr_output
    entry_point = args.entry_point

    taxonomy_zips = validateTaxonomyPackages(taxonomy_zips, cli)
    print(
        "Using:",
        f"Taxonomy entry point: {entry_point}",
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
    printDiagnostics(results)

    elapsed = (time.perf_counter_ns() - start) / 1_000_000_000
    print(f"Finished querying Arelle ({elapsed:,.2f} seconds elapsed).")


if __name__ == "__main__":
    configure_rich_output()
    main()
