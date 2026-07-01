"""Intermediate data classes that sit between workbook scraping and fact creation."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import TYPE_CHECKING, Iterator, NamedTuple, Optional

if TYPE_CHECKING:
    from typing import Self

    from mireport.xlsx_template_reader._constants import CellType

from openpyxl.workbook.defined_name import DefinedName
from openpyxl.worksheet.cell_range import CellRange
from openpyxl.worksheet.worksheet import Worksheet

from mireport.taxonomy import Concept, QName
from mireport.xlsx_template_reader._cell_iteration import (
    iterCells,
    iterCellsWithCoords,
    iterRows,
)
from mireport.xlsx_template_reader.util import excelCellRangeRef, excelCellRef


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

    def contains(self, other: CellRangeMetadata) -> bool:
        """True if other is on the same worksheet and fully within this range."""
        return self.worksheet is other.worksheet and self.cellRange.issuperset(
            other.cellRange
        )

    def overlaps(self, other: CellRangeMetadata) -> bool:
        """True if other is on the same worksheet and shares any cells with this range."""
        return self.worksheet is other.worksheet and not self.cellRange.isdisjoint(
            other.cellRange
        )

    def excelRef(self, cell: Optional[CellType] = None) -> str:
        """Excel reference for this range, or for a specific cell within it.

        e.g. 'Example sheet'!$A$5:$B$10 for the range, 'Example sheet'!$A$5
        when a cell is given.
        """
        if cell is not None:
            return excelCellRef(self.worksheet, cell)
        return excelCellRangeRef(self.worksheet, self.cellRange)

    def rows(self) -> Iterator[tuple[int, tuple[CellType, ...]]]:
        """Yield (row_number, cells) for each row of the range."""
        return iterRows(self.worksheet, self.cellRange)

    def cells(self) -> Iterator[CellType]:
        """Yield every cell of the range, row by row."""
        return iterCells(self.worksheet, self.cellRange)

    def cellsWithCoords(self) -> Iterator[tuple[int, int, CellType]]:
        """Yield (row_number, column_number, cell) for every cell of the range."""
        return iterCellsWithCoords(self.worksheet, self.cellRange)


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

    def conflictsWith(self, other: XbrlConceptCellRangeMetadata) -> bool:
        """True if the two ranges overlap in a way a hypercube table can't allow.

        Within a table, two concept ranges must be either disjoint or the exact
        same range shared by two reportable primary items. Any other overlap
        (partial, or a same range involving a dimension) is a conflict.
        """
        if not self.overlaps(other):
            return False
        shares_reportable_range = (
            self.concept.isReportable
            and other.concept.isReportable
            and self.cellRange.bounds == other.cellRange.bounds
        )
        return not shares_reportable_range


class TableBinding(NamedTuple):
    """A resolved hypercube table: its range plus the concept ranges within it."""

    table: XbrlConceptCellRangeMetadata
    primaryItems: list[XbrlConceptCellRangeMetadata]
    explicitDimensions: list[XbrlConceptCellRangeMetadata]
    typedDimensions: list[XbrlConceptCellRangeMetadata]
    units: list[XbrlConceptCellRangeMetadata]

    @property
    def conceptRanges(self) -> list[XbrlConceptCellRangeMetadata]:
        """The table range plus all its primary-item/dimension ranges — i.e. the
        entries this table occupies in the concept_map. Excludes units, which
        live in the unit_map keyed by concept rather than by defined name."""
        return [
            self.table,
            *self.primaryItems,
            *self.explicitDimensions,
            *self.typedDimensions,
        ]


class FootnoteBinding(NamedTuple):
    """The validated footnote named ranges: a container table plus its sub-ranges."""

    table: CellRangeMetadata
    text: CellRangeMetadata
    ref: CellRangeMetadata
    ref_dimension: Optional[CellRangeMetadata]


@dataclass
class WorkbookBindings:
    concept_map: dict[DefinedName, XbrlConceptCellRangeMetadata]
    tables: list[TableBinding]
    unit_map: dict[Concept, XbrlConceptCellRangeMetadata]
    preset_dims: defaultdict[XbrlConceptCellRangeMetadata, dict[Concept, Concept]]
    has_external_value: frozenset[Concept]
    footnote: Optional[FootnoteBinding]
