import argparse
import time

from mireport.arelle.taxonomy_info import callArelleForTaxonomyInfo
from mireport.cli import validateTaxonomyPackages
from mireport.taxonomy import VSME_ENTRY_POINT


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
        default=VSME_ENTRY_POINT,
        help=f"Entry point to the taxonomy. Default: {VSME_ENTRY_POINT}",
    )
    return parser


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
    if results.logLines:
        print("\t", end="")
        print(*results.logLines, sep="\n\t")

    elapsed = (time.perf_counter_ns() - start) / 1_000_000_000
    print(f"Finished querying Arelle ({elapsed:,.2f} seconds elapsed).")


if __name__ == "__main__":
    main()
