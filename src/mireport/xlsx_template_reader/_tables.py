"""TableFactCreator: create facts from resolved hypercube table bindings.

Each table row of a primary-item range becomes at most one fact, with typed
and explicit dimension values read from the same row of the table's dimension
ranges, and units resolved from the table's unit ranges.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from mireport.report import InlineReport
    from mireport.report.factbuilder import FactBuilder
    from mireport.taxonomy import Concept, Taxonomy
    from mireport.xlsx_template_reader._bindings import TableBinding, WorkbookBindings
    from mireport.xlsx_template_reader._config import ConverterConfig
    from mireport.xlsx_template_reader._constants import CellType
    from mireport.xlsx_template_reader._messages import Messenger
    from mireport.xlsx_template_reader._ranges import XbrlConceptCellRangeMetadata
    from mireport.xlsx_template_reader._reader import WorkbookReader
    from mireport.xlsx_template_reader._units import UnitResolver

from mireport.conversionresults import MessageType
from mireport.exceptions import AmbiguousComponentException
from mireport.typealiases import FactValue
from mireport.xlsx_template_reader._constants import (
    EXCEL_VALUES_TO_BE_TREATED_AS_NONE_VALUE,
)
from mireport.xlsx_template_reader._enumerations import resolveMemberByLabel
from mireport.xlsx_template_reader._fact_support import (
    addFactToReport,
    processNumeric,
    resolveMemberWithMessages,
)

L = logging.getLogger(__name__)


class TableFactCreator:
    """Creates facts from the rows of resolved hypercube tables."""

    def __init__(
        self,
        report: InlineReport,
        reader: WorkbookReader,
        msg: Messenger,
        config: ConverterConfig,
        units: UnitResolver,
        bindings: WorkbookBindings,
    ) -> None:
        self._report = report
        self._reader = reader
        self._msg = msg
        self._config = config
        self._units = units
        self._bindings = bindings

    @property
    def taxonomy(self) -> Taxonomy:
        return self._report.taxonomy

    def createTableFacts(self) -> None:
        for table_binding in self._bindings.tables:
            table_range = table_binding.table
            if not table_binding.primaryItems:
                self._msg.error(
                    f"Table {table_range.definedName.name} has no primary items defined. Skipping.",
                    MessageType.ExcelParsing,
                    ref=table_range,
                )
                continue

            for priItem in table_binding.primaryItems:
                unitHolder, sharedRange = self._unitHolderFor(priItem, table_binding)
                for rnum, row in priItem.rows():
                    if not self._processRow(
                        table_binding, priItem, rnum, row, unitHolder, sharedRange
                    ):
                        break

    @staticmethod
    def _unitHolderFor(
        priItem: XbrlConceptCellRangeMetadata,
        table_binding: TableBinding,
    ) -> tuple[XbrlConceptCellRangeMetadata | None, bool]:
        """The table unit range for this primary item, and whether that range
        is shared with another primary item's unit range."""
        unitHolder = next(
            (u for u in table_binding.units if u.concept == priItem.concept), None
        )
        sharedRange = unitHolder is not None and any(
            u.cellRange == unitHolder.cellRange
            for u in table_binding.units
            if u is not unitHolder
        )
        return unitHolder, sharedRange

    def _processRow(
        self,
        table_binding: TableBinding,
        priItem: XbrlConceptCellRangeMetadata,
        rnum: int,
        row: tuple[CellType, ...],
        unitHolder: XbrlConceptCellRangeMetadata | None,
        sharedRange: bool,
    ) -> bool:
        """Create at most one fact from this row. Returns False when the
        primary item is unusable and its remaining rows should be skipped."""
        concept = priItem.concept
        broken = False
        cells = [cell for cell in row if cell.value is not None]
        match len(cells):
            case 0:
                return True
            case 1:
                cell = cells[0]
                value = cell.value
                values = [value]
            case _:
                values = [c.value for c in cells]
                cell = cells[0]
                if concept.isEnumerationSet:
                    value = " ".join(str(v) for v in values)
                else:
                    self._msg.error(
                        f"Primary item {priItem.definedName.name} spans multiple columns and has multiple values ({values}). Skipping.",
                        MessageType.ExcelParsing,
                        concept=priItem.concept,
                        ref=priItem.excelRef(cell),
                    )
                    return False

        if value is None or value in EXCEL_VALUES_TO_BE_TREATED_AS_NONE_VALUE:
            return True

        factBuilder = self._report.getFactBuilder()
        factBuilder.setValue(value).setConcept(concept)

        if (presetDimensions := self._bindings.preset_dims.get(priItem)) is not None:
            for dim, dimValue in presetDimensions.items():
                if (
                    defaultValue := self.taxonomy.getDimensionDefault(dim)
                ) is not None and dimValue != defaultValue:
                    factBuilder.setExplicitDimension(dim, dimValue)

        all_dims_set = True
        all_dims_set &= self._addTypedDimensions(
            table_binding.typedDimensions, rnum, factBuilder
        )
        all_dims_set &= self._addExplicitDimensions(
            table_binding.explicitDimensions, rnum, factBuilder
        )
        if not all_dims_set:
            if value:
                self._msg.warning(
                    f"Unable to add fact with value '{value}' due to missing dimension values.",
                    MessageType.Conversion,
                    concept=priItem.concept,
                    ref=priItem.excelRef(cell),
                )
            return True

        if concept.isNumeric:
            processNumeric(self._msg, priItem, cell, factBuilder, value)
            if not self._units.setUnitForName(
                priItem,
                factBuilder,
                row=rnum,
                specifiedUnitHolder=unitHolder,
                sharedRange=sharedRange,
            ):
                return True

        if concept.isEnumerationSingle:
            member = resolveMemberWithMessages(
                self._msg,
                self.taxonomy,
                self._config,
                str(value),
                concept,
                priItem,
                cell,
            )
            if member is not None:
                factBuilder.setHiddenValue(member.expandedName)
            else:
                broken = True
                self._msg.error(
                    f"Unable to find EE concept for cell value '{value}'",
                    MessageType.Conversion,
                    concept=priItem.concept,
                    ref=priItem.excelRef(cell),
                )
        elif concept.isEnumerationSet:
            eeValues: list[Concept] = []
            for v in values:
                member = resolveMemberWithMessages(
                    self._msg,
                    self.taxonomy,
                    self._config,
                    str(v),
                    concept,
                    priItem,
                    cell,
                )
                if member is not None:
                    eeValues.append(member)
                else:
                    broken = True
                    self._msg.error(
                        f"Unable to find EE concept for cell value '{v}'",
                        MessageType.Conversion,
                        concept=priItem.concept,
                        ref=priItem.excelRef(cell),
                    )
            factBuilder.setHiddenValue(
                " ".join(sorted(set(e.expandedName for e in eeValues)))
            )

        if broken:
            self._msg.warning(
                f"Unable to add fact with value '{value}'",
                MessageType.Conversion,
                concept=priItem.concept,
                ref=priItem.excelRef(cell),
            )
        else:
            addFactToReport(self._report, self._msg, factBuilder, priItem)
        return True

    def _addTypedDimensions(
        self,
        typed_dimensions: list[XbrlConceptCellRangeMetadata],
        rnum: int,
        factBuilder: FactBuilder,
    ) -> bool:
        if not typed_dimensions:
            return True

        dims_set = 0
        for td in typed_dimensions:
            tdConcept = td.concept
            tdCell = self._reader.getSingleCell(td, row=rnum)
            if not tdCell:
                continue
            elif (tdValue := tdCell.value) is not None:
                dims_set += 1
                if not isinstance(tdValue, FactValue):
                    tdValue = str(tdValue)
                factBuilder.setTypedDimension(tdConcept, tdValue)
            else:
                self._msg.error(
                    f"Required typed dimension {tdConcept.qname} not set",
                    MessageType.Conversion,
                    ref=td.excelRef(tdCell),
                )
        return dims_set == len(typed_dimensions)

    def _addExplicitDimensions(
        self,
        explicit_dimensions: list[XbrlConceptCellRangeMetadata],
        rnum: int,
        factBuilder: FactBuilder,
    ) -> bool:
        if not explicit_dimensions:
            return True

        dims_set = 0
        for ed in explicit_dimensions:
            edConcept = ed.concept
            edCell = self._reader.getSingleCell(ed, row=rnum)

            if not edCell:
                continue
            elif (edValue := edCell.value) is None:
                self._msg.error(
                    f"Required explicit dimension {edConcept.qname} not set. Cell value '{edValue}'",
                    MessageType.Conversion,
                    ref=ed.excelRef(edCell),
                )
                continue

            try:
                match = resolveMemberByLabel(
                    self.taxonomy, self._config, str(edValue), dimension=edConcept
                )
            except AmbiguousComponentException as exc:
                match = None
                self._msg.error(
                    f"Ambiguous value '{edValue}' for explicit dimension "
                    f"{edConcept.qname}. Candidate members: "
                    f"{', '.join(str(c.qname) for c in exc.candidates)}.",
                    MessageType.Conversion,
                    ref=ed.excelRef(edCell),
                )
            if match is not None:
                factBuilder.setExplicitDimension(edConcept, match.concept)
                dims_set += 1
            else:
                self._msg.error(
                    f"Required explicit dimension {edConcept.qname} not set. Cell value '{edValue}'",
                    MessageType.Conversion,
                    ref=ed.excelRef(edCell),
                )
        return dims_set == len(explicit_dimensions)
