from __future__ import annotations

from datetime import date, datetime, time
from typing import (
    TypeAlias,
)

from openpyxl.cell import Cell, MergedCell, ReadOnlyCell
from openpyxl.cell.cell import ERROR_CODES

CellType: TypeAlias = ReadOnlyCell | MergedCell | Cell
CellValueType: TypeAlias = bool | float | int | str | datetime | date | time | None

# Placeholders a user types to mean "nothing to report here". Excel error
# values are deliberately not in this set: they mean a broken formula, and are
# reported (see is_error_value) rather than skipped.
EXCEL_VALUES_TO_BE_TREATED_AS_NONE_VALUE = frozenset({"-"})
IGNORED_DEFINED_NAME_PREFIXES = ("enum_", "template_")

# TODO FIXME Temporary workarounds for the VSME taxonomy.
# Template named ranges whose name doesn't match the taxonomy concept local
# name; the value is the concept local name to bind them to instead.
TAXONOMY_NAME_ALIASES: dict[str, str] = {
    "IdentifierOfSitesInBiodiversitySensitiveAreasTypedAxis": "IdentifierOfSiteTypedAxis",
}
# Named ranges that are expected to go unhandled without warranting a message.
UNHANDLED_NAMES_TO_IGNORE: frozenset[str] = frozenset(
    {"BreakdownOfEnergyConsumptionAxis"}
)

# openpyxl's ERROR_CODES covers standard Excel errors; #ERROR! is a Google Sheets addition
EXCEL_ERROR_VALUES: frozenset[str] = frozenset(ERROR_CODES)
GOOGLE_SHEET_ERROR_VALUES: frozenset[str] = frozenset({"#ERROR!"})
ALL_ERROR_VALUES: frozenset[str] = EXCEL_ERROR_VALUES.union(GOOGLE_SHEET_ERROR_VALUES)


def is_error_value(v: str) -> bool:
    return v in ALL_ERROR_VALUES
