"""What the layout produces: sections, and the tables inside them."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import NamedTuple

from mireport.report.fact import Fact
from mireport.report.periods import DurationPeriodHolder, InstantPeriodHolder, _Period
from mireport.taxonomy import (
    Concept,
    PresentationGroup,
    PresentationStyle,
    Relationship,
)

HeadingValue = Concept | Relationship | _Period | str | None


class TableHeadingCell(NamedTuple):
    """A single cell in a table header row, carrying its value, span, and numeric flag."""

    value: HeadingValue
    colspan: int = 0
    rowspan: int = 0
    numeric: bool = False

    @property
    def isDuration(self) -> bool:
        return isinstance(self.value, DurationPeriodHolder)

    @property
    def isInstant(self) -> bool:
        return isinstance(self.value, InstantPeriodHolder)

    @property
    def isPeriod(self) -> bool:
        return self.isDuration or self.isInstant

    @property
    def isConcept(self) -> bool:
        return isinstance(self.value, Concept)

    @property
    def isRelationship(self) -> bool:
        return isinstance(self.value, Relationship)


class TableStyle(Enum):
    """How a table's rows and columns were derived."""

    SingleTypedDimensionColumn = auto()
    SingleExplicitDimensionColumn = auto()
    SingleExplicitDimensionRow = auto()
    Other = auto()
    """Derived from the facts' own dimensions, because the taxonomy's presentation gave no
    hypercube to follow."""


@dataclass(slots=True, frozen=True)
class TableCell:
    """A single data cell: the fact it holds (or None) and whether its unit is already shown elsewhere."""

    fact: Fact | None
    suppress_unit: bool


@dataclass(slots=True, frozen=True)
class TableRow:
    """One row of a table: a row-heading cell and the ordered data cells across all columns."""

    heading: TableHeadingCell
    cells: list[TableCell]


@dataclass(slots=True, frozen=True)
class Table:
    """The fully assembled table ready for the template: header rows, data rows, and display metadata."""

    style: TableStyle
    numeric: bool
    header_rows: list[list[TableHeadingCell]]
    rows: list[TableRow]

    @property
    def column_count(self) -> int:
        return len(self.rows[0].cells) if self.rows else 0


@dataclass(frozen=True, slots=True)
class FactGrid:
    """Facts organised into a row/column grid with labels, before the header rows, units and periods are worked out (see headers.assemble_table)."""

    style: TableStyle
    data: list[list[Fact | None]]
    row_labels: Sequence[Concept | str]
    row_heading_label: Concept | str | None
    col_labels: Sequence[Concept | str]


@dataclass(slots=True, frozen=True, eq=True)
class ReportSection:
    """A presentation group together with the facts assigned to each of its relationships."""

    relationshipToFact: dict[Relationship, list[Fact]]
    presentation: PresentationGroup
    # Set when one presentation group yields several sections, e.g. a table per dimension set.
    heading_suffix: str = field(default="", kw_only=True)

    def getLabel(self, language: str) -> str:
        label = self.presentation.getLabel(language)
        return f"{label} - {self.heading_suffix}" if self.heading_suffix else label

    @property
    def style(self) -> PresentationStyle:
        return self.presentation.style

    @property
    def hasFacts(self) -> bool:
        if self.presentation.style == PresentationStyle.Empty:
            return False
        return any(factList for factList in self.relationshipToFact.values())

    @property
    def tabular(self) -> bool:
        return False


@dataclass(slots=True, frozen=True, eq=True)
class TabularReportSection(ReportSection):
    table: Table

    @property
    def style(self) -> PresentationStyle:
        # A section that carries a table is rendered as one, whatever style its group has: a
        # group with no hypercube (style List) can still hold dimensionally qualified facts.
        return PresentationStyle.Table

    @property
    def tabular(self) -> bool:
        return True

    @property
    def hasFacts(self) -> bool:
        return any(
            cell.fact is not None for row in self.table.rows for cell in row.cells
        )
