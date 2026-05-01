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
    effectiveWidth: int
    effectiveHeight: int
    cellsPopulated: int


@dataclass(slots=True, eq=True, frozen=True)
class CellAndXBRLMetadataHolder(CellRangeMetadata):
    concept: Concept

    @classmethod
    def fromCellRangeMetadata(cls, holder: CellRangeMetadata, concept: Concept) -> Self:
        return cls(
            definedName=holder.definedName,
            worksheet=holder.worksheet,
            cellRange=holder.cellRange,
            effectiveWidth=holder.effectiveWidth,
            effectiveHeight=holder.effectiveHeight,
            concept=concept,
            cellsPopulated=holder.cellsPopulated,
        )


class TableXBRLContents(NamedTuple):
    primaryItems: list[CellAndXBRLMetadataHolder]
    explicitDimensions: list[CellAndXBRLMetadataHolder]
    typedDimensions: list[CellAndXBRLMetadataHolder]
    units: list[CellAndXBRLMetadataHolder]


@dataclass
class WorkbookBindings:
    concept_map: dict[DefinedName, CellAndXBRLMetadataHolder]
    table_map: dict[CellAndXBRLMetadataHolder, TableXBRLContents]
    unit_map: dict[Concept, CellAndXBRLMetadataHolder]
    preset_dims: defaultdict[CellAndXBRLMetadataHolder, dict[Concept, Concept]]
    unused: set[DefinedName]
