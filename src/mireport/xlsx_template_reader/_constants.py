from __future__ import annotations

from datetime import date, datetime, time
from typing import (
    TypeAlias,
)

from openpyxl.cell import Cell, MergedCell, ReadOnlyCell

CellType: TypeAlias = ReadOnlyCell | MergedCell | Cell
CellValueType: TypeAlias = bool | float | int | str | datetime | date | time | None

EXCEL_PLACEHOLDER_VALUE = "#VALUE!"
EXCEL_VALUES_TO_BE_TREATED_AS_NONE_VALUE = frozenset({"-", EXCEL_PLACEHOLDER_VALUE})
IGNORED_DEFINED_NAME_PREFIXES = ("enum_", "template_")
EXTERNAL_VALUES_RANGE = "template_external_values"
