from __future__ import annotations

import re
import warnings
from datetime import date, datetime
from pathlib import Path
from typing import (
    TYPE_CHECKING,
    BinaryIO,
    Iterable,
    Optional,
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
        raise Exception(f'"{path}" is not a supported (.xlsx) Excel file')


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


def excelCellOrCellRangeRef(
    worksheet: Worksheet, cellRange: CellRange, cell: CellType | None
) -> str:
    """Make an Excel cell reference such as 'Example sheet'!$A$5"""
    if cell is not None:
        return excelCellRef(worksheet, cell)
    elif cellRange is not None:
        return excelCellRangeRef(worksheet, cellRange)
    else:
        return None


def excelDefinedNameRef(
    definedName: Optional[DefinedName], cell: Optional[CellType] = None
) -> Optional[str]:
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


def get_decimal_places(cell: CellType) -> DecimalPlaces:
    """
    Returns the number of decimal places in the cell's number format. For
    example, a format of '0.00' would return 2.

    If no decimal places are specified, return Literal['INF'], meaning infinite
    precision, include all digits in display.
    """
    number_format = cell.number_format

    # Match typical decimal number formats like '0.00', '#,##0.000', etc.
    if match := re.search(r"\.(0+)", number_format):
        return len(match.group(1))

    # Handle cases like percentage formats '0.0%' or '0.000%'
    if match := re.search(r"\.(0+)%", number_format):
        return len(match.group(1))

    # Catch general cases like scientific notation '0.00E+00'
    if match := re.search(r"\.(0+)[eE]", number_format):
        return len(match.group(1))

    return "INF"


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
