"""Intermediate data classes that sit between workbook scraping and fact creation."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import TYPE_CHECKING, NamedTuple

if TYPE_CHECKING:
    from typing import Self

from openpyxl.workbook.defined_name import DefinedName
from openpyxl.worksheet.cell_range import CellRange
from openpyxl.worksheet.worksheet import Worksheet

from mireport.taxonomy import Concept, QName


class ComplexUnit(NamedTuple):
    numerator: list[QName]
    denominator: list[QName]


@dataclass(slots=True, eq=True, frozen=True)
class CellRangeMetadata:
    definedName: DefinedName
    worksheet: Worksheet
    cellRange: CellRange
    populated_width: int
    populated_height: int
    populated_min_col: int
    populated_min_row: int

    @property
    def maximum_width(self) -> int:
        return self.cellRange.max_col - self.cellRange.min_col + 1

    @property
    def maximum_height(self) -> int:
        return self.cellRange.max_row - self.cellRange.min_row + 1


@dataclass(slots=True, eq=True, frozen=True)
class XbrlConceptCellRangeMetadata(CellRangeMetadata):
    concept: Concept

    @classmethod
    def fromCellRangeMetadata(cls, holder: CellRangeMetadata, concept: Concept) -> Self:
        return cls(
            definedName=holder.definedName,
            worksheet=holder.worksheet,
            cellRange=holder.cellRange,
            populated_width=holder.populated_width,
            populated_height=holder.populated_height,
            populated_min_col=holder.populated_min_col,
            populated_min_row=holder.populated_min_row,
            concept=concept,
        )


class XbrlTableCellRangeMetadataHolder(NamedTuple):
    primaryItems: list[XbrlConceptCellRangeMetadata]
    explicitDimensions: list[XbrlConceptCellRangeMetadata]
    typedDimensions: list[XbrlConceptCellRangeMetadata]
    units: list[XbrlConceptCellRangeMetadata]


@dataclass
class WorkbookBindings:
    concept_map: dict[DefinedName, XbrlConceptCellRangeMetadata]
    table_map: dict[XbrlConceptCellRangeMetadata, XbrlTableCellRangeMetadataHolder]
    unit_map: dict[Concept, XbrlConceptCellRangeMetadata]
    preset_dims: defaultdict[XbrlConceptCellRangeMetadata, dict[Concept, Concept]]
    has_external_value: frozenset[Concept]
