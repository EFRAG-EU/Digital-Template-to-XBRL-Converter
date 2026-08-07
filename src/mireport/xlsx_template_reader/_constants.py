from __future__ import annotations

from datetime import date, datetime, time
from typing import (
    TypeAlias,
)

from openpyxl.cell import Cell, MergedCell, ReadOnlyCell
from openpyxl.cell.cell import ERROR_CODES

CellType: TypeAlias = ReadOnlyCell | MergedCell | Cell
CellValueType: TypeAlias = bool | float | int | str | datetime | date | time | None

EXCEL_PLACEHOLDER_VALUE = "#VALUE!"
EXCEL_VALUES_TO_BE_TREATED_AS_NONE_VALUE = frozenset({"-", EXCEL_PLACEHOLDER_VALUE})
IGNORED_DEFINED_NAME_PREFIXES = ("enum_", "template_")
EXTERNAL_VALUES_RANGE = "template_external_values"

# openpyxl's ERROR_CODES covers standard Excel errors; #ERROR! is a Google Sheets addition
EXCEL_ERROR_VALUES: frozenset[str] = frozenset(ERROR_CODES)
GOOGLE_SHEET_ERROR_VALUES: frozenset[str] = frozenset({"#ERROR!"})
ALL_ERROR_VALUES: frozenset[str] = EXCEL_ERROR_VALUES.union(GOOGLE_SHEET_ERROR_VALUES)


def is_error_value(v: str) -> bool:
    return v in ALL_ERROR_VALUES
