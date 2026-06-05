#!/usr/bin/env python

import time
from argparse import ArgumentParser, BooleanOptionalAction
from contextlib import closing
from pathlib import Path

from mireport.cli import configure_rich_output
from mireport.cli import console_print as print
from mireport.xlsx_template_reader._util import (
    checkExcelFilePath,
    getNamedRanges,
    list_named_ranges,
    loadExcelFromPathOrFileLike,
)

DEFAULT_MAX_INTERESTING_CELLS = 10


def createArgParser() -> ArgumentParser:
    parser = ArgumentParser()
    parser.add_argument("excel", help="Excel input file")
    parser.add_argument(
        "-q",
        "--query",
        help="only output named ranges whose names match this pattern",
        default=None,
    )
    parser.add_argument(
        "-v",
        "--verbose",
        help="Toggle verbose output",
        default=False,
        action=BooleanOptionalAction,
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "-e",
        "--errors-only",
        help="Only output errors; suppress named range values",
        default=False,
        action="store_true",
    )
    mode.add_argument(
        "-d",
        "--data-only",
        help="Only output named range values; suppress error summary",
        default=False,
        action="store_true",
    )
    mode.add_argument(
        "-n",
        "--named-ranges-only",
        help="List all named range names and their raw values, flagging broken ones",
        default=False,
        action="store_true",
    )
    return parser


def dump_data(candidates: list, verbose: bool) -> None:
    max_interesting_cells = None if verbose else DEFAULT_MAX_INTERESTING_CELLS
    for name, cells in candidates:
        num = len(cells)
        print(f"{name}: ({num} cells in range)")
        print("\t", end="")

        if all([x is None for x in cells]):
            print("(all cells empty)")
            continue

        if max_interesting_cells and (total := len(cells)) > max_interesting_cells:
            size = int(max_interesting_cells / 2)
            cells = (
                cells[:size]
                + [f"… supressed {total - max_interesting_cells} cell values …"]
                + cells[-size:]
            )
        print(*cells, sep="\n\t")


def dump_errors(errors: list, verbose: bool, errors_only: bool = False) -> None:
    if not errors:
        if errors_only:
            print("No errors detected.")
        return
    print(f"Detected {len(errors)} named ranges with issues", end="")
    if verbose or errors_only:
        print(":")
        print()
        width = len(str(len(errors)))
        for i, error in enumerate(errors, start=1):
            print(f"Issue {i:0{width}}:", error)
            print()
    else:
        print(".")


def dump_named_ranges(raw_names: list[tuple[str, str]], errors: list) -> None:
    borked = {e.defined_name.name: e.message for e in errors}
    for name, value in sorted(raw_names):
        if name in borked:
            print(f"  ✗ {name}: {value}")
            print(f"      {borked[name]}")
        else:
            print(f"  ✓ {name}: {value}")
    total = len(raw_names)
    invalid = len(borked)
    w = len(str(total))
    print()
    print("Summary:")
    print(f"  {total:{w}} named ranges total")
    print(f"  {total - invalid:{w}} valid")
    print(f"  {invalid:{w}} with invalid ranges specified")


def main() -> None:
    parser = createArgParser()
    args = parser.parse_args()

    start = time.perf_counter_ns()
    excel_file = Path(args.excel)
    checkExcelFilePath(excel_file)
    with closing(loadExcelFromPathOrFileLike(excel_file)) as wb:
        if args.verbose:
            print(f"Opened {excel_file}")
            print("Found sheets:", *wb.sheetnames, sep="\n\t")
            print(f"Found {len(wb.defined_names)} named ranges to query for data.")
        start = time.perf_counter_ns()
        raw_names = list_named_ranges(wb)
        facts, errors = getNamedRanges(wb)
        elapsed = (time.perf_counter_ns() - start) / 1_000_000

    exitCode = 1 if errors else 0

    if args.verbose:
        print(
            f"Queried all named ranges and found {len(facts)} non-empty ranges in {elapsed:,.2f} ms."
        )

    if args.named_ranges_only:
        dump_named_ranges(raw_names, errors)
    else:
        if args.query:
            query = args.query.lower()

            def filter_fn(name_cells: tuple[str, list]) -> bool:
                name, _ = name_cells
                return query in name.lower()

            candidates = sorted(filter(filter_fn, facts.items()))
            if not candidates:
                raise SystemExit(f"No named ranges matched --query term {args.query}")
            else:
                print(
                    f"{len(candidates)} named range names case-insensitively match {args.query}"
                )
        else:
            candidates = sorted(facts.items())

        if not args.errors_only:
            dump_data(candidates, args.verbose)

        if not args.data_only:
            dump_errors(errors, verbose=args.verbose, errors_only=args.errors_only)

    elapsed = (time.perf_counter_ns() - start) / 1_000_000_000
    print(f"Finished dumping Excel named ranges ({elapsed:,.2f} seconds elapsed).")
    raise SystemExit(exitCode)


if __name__ == "__main__":
    configure_rich_output()
    main()
