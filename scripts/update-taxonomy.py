import argparse
import sys
import time
from collections.abc import Sequence

from mireport.arelle.taxonomy_info import callArelleForTaxonomyInfo
from mireport.cli import (
    configure_rich_output,
    getEntryPointsFromPackages,
    pickEntryPointFromPackages,
    printEntryPointTable,
    validateTaxonomyPackages,
)
from mireport.cli import (
    console_print as print,
)


def parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Extract taxonomy information from zip files and save to JSON file."
    )
    parser.add_argument(
        "taxonomy_json_path",
        type=str,
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
        type=str,
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
    return parser


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
    if results.logLines:
        print("\t", end="")
        print(*results.logLines, sep="\n\t")

    elapsed = (time.perf_counter_ns() - start) / 1_000_000_000
    print(f"Finished querying Arelle ({elapsed:,.2f} seconds elapsed).")


if __name__ == "__main__":
    configure_rich_output()
    main()
