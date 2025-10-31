#!/usr/bin/env python

import time
from argparse import ArgumentParser, BooleanOptionalAction
from contextlib import closing
from pathlib import Path

from mireport.excelutil import (
    checkExcelFilePath,
    getNamedRanges,
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
    return parser


def main() -> None:
    exitCode = 0
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
        facts, errors = getNamedRanges(wb)
        elapsed = (time.perf_counter_ns() - start) / 1_000_000

    if args.verbose:
        print(
            f"Queried all named ranges and found {len(facts)} non-empty ranges in {elapsed:,.2f} ms."
        )

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

    if args.verbose:
        max_interesting_cells = None
    else:
        max_interesting_cells = DEFAULT_MAX_INTERESTING_CELLS

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

    if errors:
        exitCode = 1
        print()
        print(f"Detected {len(errors)} named ranges with issues", end="")
        if args.verbose:
            print(":")
            print()
            width = len(str(len(errors)))
            for i, error in enumerate(errors, start=1):
                print(f"Issue {i:0{width}}:", error)
                print()
        else:
            print(".")

    elapsed = (time.perf_counter_ns() - start) / 1_000_000_000
    print(f"Finished dumping Excel named ranges ({elapsed:,.2f} seconds elapsed).")
    raise SystemExit(exitCode)


if __name__ == "__main__":
    main()
