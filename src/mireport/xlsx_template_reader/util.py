from __future__ import annotations

import re
import warnings
from collections.abc import Iterable
from datetime import date, datetime
from pathlib import Path
from typing import (
    TYPE_CHECKING,
    BinaryIO,
)

if TYPE_CHECKING:
    from mireport.taxonomy import Concept

from dateutil.parser import parse as parse_datetime
from openpyxl import Workbook, load_workbook
from openpyxl.utils.cell import absolute_coordinate, quote_sheetname
from openpyxl.workbook.defined_name import DefinedName
from openpyxl.worksheet.cell_range import CellRange
from openpyxl.worksheet.worksheet import Worksheet

from mireport.typealiases import DecimalPlaces
from mireport.xlsx_template_reader._constants import CellType


def conceptsToText(concepts: Iterable[Concept]) -> str:
    return ", ".join(sorted(str(c.qname) for c in concepts))


def checkExcelFilePath(path: Path) -> None:
    if not path.is_file():
        raise FileNotFoundError(f'"{path}" is not a file.')
    elif path.suffix != ".xlsx":
        raise ValueError(f'"{path}" is not a supported (.xlsx) Excel file')


def loadExcelFromPathOrFileLike(
    pathOrFile: Path | BinaryIO, read_only: bool = False
) -> Workbook:
    # We can safely suppress these warnings as our use-case is **just**
    # extracting data from the Excel file.
    with warnings.catch_warnings():
        warnings.filterwarnings(
            "ignore",
            message=".*? extension is not supported and will be removed",
            category=UserWarning,
            module=r"openpyxl\.worksheet\._reader",
        )
        wb = load_workbook(
            filename=pathOrFile, read_only=read_only, data_only=True, rich_text=True
        )
    return wb


def excelCellRef(worksheet: Worksheet, cell: CellType) -> str:
    """Make an Excel cell reference such as 'Example sheet'!$A$5"""
    ref = f"{quote_sheetname(worksheet.title)}!{absolute_coordinate(cell.coordinate)}"
    return ref


def excelCellRangeRef(worksheet: Worksheet, cellRange: CellRange) -> str:
    """Make an Excel cell reference such as 'Example sheet'!$A$5"""
    ref = f"{quote_sheetname(worksheet.title)}!{absolute_coordinate(cellRange.coord)}"
    return ref


def excelDefinedNameRef(
    definedName: DefinedName | None, cell: CellType | None = None
) -> str | None:
    """Make an Excel cell reference such as 'Example sheet'!$A$5"""
    if definedName is None:
        return None

    destinations = list(definedName.destinations)
    match len(destinations):
        case 1:
            sheet_name, cell_range = destinations[0]
            if cell is not None:
                coord = cell.coordinate
            else:
                coord = cell_range
            ref = f"{quote_sheetname(sheet_name)}!{absolute_coordinate(coord)}"
            return ref
        case _:
            return None


# What a number format shows literally rather than as digits: quoted text, an escaped
# character, a [colour], [condition] or [$currency], and the character after _ (padding) or *
# (fill).
_FORMAT_LITERALS = re.compile(r'"[^"]*"|\\.|\[[^\]]*\]|[_*].')


def _numberFormatSection(cell: CellType) -> str:
    """The first section (for positive numbers) of the cell's number format, without literals."""
    return _FORMAT_LITERALS.sub("", cell.number_format).split(";", 1)[0]


def get_decimal_places(cell: CellType) -> DecimalPlaces:
    """
    Returns the number of decimal places in the cell's number format. For
    example, a format of '0.00' would return 2, and '0', '#,##0' and '0%' 0.
    Only the 0s after the point count, the places always shown: '0.0#' is 1.

    A format that shows no digits as such ('General', '@'), or a fraction, has
    no decimal places: return Literal['INF'], meaning infinite precision,
    include all digits in display.
    """
    section = _numberFormatSection(cell)
    if not re.search(r"[0#?]", section) or "/" in section:
        return "INF"
    if match := re.search(r"\.([0#?]*)", section):
        return match.group(1).count("0")
    return 0


# What getDateFromValue accepts, for messages about cells it refuses.
DATE_FORMS_HINT = (
    "Enter it as an Excel date, or as text in the form YYYY-MM-DD or DD/MM/YYYY."
)


def getDateFromValue(value: object) -> date:
    if isinstance(value, datetime):
        return value.date()
    elif isinstance(value, date):
        return value
    elif isinstance(value, str):
        if "-" in value:
            return date.fromisoformat(value)
        elif "/" in value:
            return parse_datetime(value, yearfirst=False, dayfirst=True).date()
        raise ValueError(f"Unsupported date string: '{value}'")
    else:
        raise TypeError(f"Unsupported type for date conversion: {type(value).__name__}")
