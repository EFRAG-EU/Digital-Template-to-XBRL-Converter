"""Low-level iteration over openpyxl cell ranges.

Callers holding a CellRangeMetadata should use its rows()/cells()/cellsWithCoords()
methods rather than these functions.
"""

from __future__ import annotations

from typing import Iterator, NamedTuple

from openpyxl.worksheet.cell_range import CellRange
from openpyxl.worksheet.worksheet import Worksheet

from mireport.exceptions import OpenPyXlRelatedException
from mireport.xlsx_template_reader._constants import CellType


def iterRows(
    ws: Worksheet, cr: CellRange
) -> Iterator[tuple[int, tuple[CellType, ...]]]:
    """Yield (row_number, cells) for each row of the range."""
    if cr.min_row is None or cr.min_col is None:
        raise OpenPyXlRelatedException(
            f"Cell range bounds expected to be int but actually None {cr=}"
        )
    yield from enumerate(
        ws.iter_rows(
            min_row=cr.min_row,
            min_col=cr.min_col,
            max_row=cr.max_row,
            max_col=cr.max_col,
        ),
        start=cr.min_row,
    )


def iterCells(ws: Worksheet, cr: CellRange) -> Iterator[CellType]:
    """Yield every cell of the range, row by row."""
    for _, row in iterRows(ws, cr):
        yield from row


def iterCellsWithCoords(
    ws: Worksheet, cr: CellRange
) -> Iterator[tuple[int, int, CellType]]:
    """Yield (row_number, column_number, cell) for every cell of the range."""
    for rnum, row in iterRows(ws, cr):
        for cnum, cell in enumerate(row, start=cr.min_col):
            yield rnum, cnum, cell


class _CellRangeDimensions(NamedTuple):
    cellsAccessed: set[tuple[str, int, int]]
    cellsPopulated: set[tuple[str, int, int]]
    populated_width: int
    populated_height: int
    populated_min_col: int
    populated_min_row: int

    @property
    def countAccessed(self) -> int:
        return len(self.cellsAccessed)

    @property
    def countPopulated(self) -> int:
        return len(self.cellsPopulated)


def getEffectiveCellRangeDimensions(
    ws: Worksheet, cell_range: CellRange
) -> _CellRangeDimensions:
    cols_not_empty: set[int] = set()
    cols_with_none: set[int] = set()
    populated_rows: set[int] = set()
    populatedCellCount: set[tuple[str, int, int]] = set()
    cellCount: set[tuple[str, int, int]] = set()

    last_rnum = None
    empty_row = True
    sheetName = ws.title
    for rnum, cnum, cell in iterCellsWithCoords(ws, cell_range):
        cellCount.add((sheetName, rnum, cnum))
        if last_rnum is None:
            last_rnum = rnum

        if rnum != last_rnum:
            if not empty_row:
                populated_rows.add(last_rnum)
            last_rnum = rnum
            empty_row = True

        if cell.value is not None:
            populatedCellCount.add((sheetName, rnum, cnum))
            empty_row = False
            cols_not_empty.add(cnum)
        else:
            cols_with_none.add(cnum)
    else:
        if last_rnum is not None and not empty_row:
            populated_rows.add(last_rnum)

    definitely_empty_cols = cols_with_none - cols_not_empty
    total_cols = len(cols_not_empty.union(cols_with_none))
    populated_width = max(1, total_cols - len(definitely_empty_cols))
    populated_height = max(1, len(populated_rows))
    populated_min_col = min(cols_not_empty, default=None) or cell_range.min_col
    populated_min_row = min(populated_rows, default=None) or cell_range.min_row
    return _CellRangeDimensions(
        cellsAccessed=cellCount,
        cellsPopulated=populatedCellCount,
        populated_width=populated_width,
        populated_height=populated_height,
        populated_min_col=populated_min_col,
        populated_min_row=populated_min_row,
    )
