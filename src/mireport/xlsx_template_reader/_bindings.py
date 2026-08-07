"""Intermediate data classes that sit between workbook scraping and fact creation."""

from __future__ import annotations

from dataclasses import dataclass
from typing import NamedTuple

from openpyxl.workbook.defined_name import DefinedName

from mireport.taxonomy import Concept
from mireport.xlsx_template_reader._ranges import (
    CellRangeMetadata,
    XbrlConceptCellRangeMetadata,
)


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
    ref_dimension: CellRangeMetadata | None


@dataclass
class WorkbookBindings:
    concept_map: dict[DefinedName, XbrlConceptCellRangeMetadata]
    tables: list[TableBinding]
    unit_map: dict[Concept, XbrlConceptCellRangeMetadata]
    preset_dims: dict[XbrlConceptCellRangeMetadata, dict[Concept, Concept]]
    has_external_value: frozenset[Concept]
    footnote: FootnoteBinding | None
