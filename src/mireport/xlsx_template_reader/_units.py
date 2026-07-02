"""UnitResolver: find and apply the XBRL unit for a numeric fact.

The resolution chain, in order: the unit named range next to the concept
(direct id, then parenthesised, then config-corrected text), a per-concept
config override, the taxonomy's required units, config complex units for the
data type, and finally the data-type/UTR fallbacks.
"""

from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from openpyxl.workbook.defined_name import DefinedName

    from mireport.report import InlineReport
    from mireport.report.factbuilder import FactBuilder
    from mireport.taxonomy import Concept, QName, Taxonomy
    from mireport.xlsx_template_reader._config import ConverterConfig
    from mireport.xlsx_template_reader._constants import CellType
    from mireport.xlsx_template_reader._messages import Messenger
    from mireport.xlsx_template_reader._ranges import XbrlConceptCellRangeMetadata
    from mireport.xlsx_template_reader._reader import WorkbookReader

from mireport.conversionresults import MessageType

L = logging.getLogger(__name__)


def cleanUnitTextFromExcel(unitText: str, replacements: dict[str, str]) -> str:
    new = unitText
    for original, replacement in replacements.items():
        new = new.replace(original, replacement)
    return new


class UnitResolver:
    """Resolves the unit for numeric facts, from workbook cells, config
    overrides and taxonomy/UTR fallbacks."""

    def __init__(
        self,
        report: InlineReport,
        config: ConverterConfig,
        msg: Messenger,
        reader: WorkbookReader,
        unit_map: dict[Concept, XbrlConceptCellRangeMetadata],
    ) -> None:
        self._report = report
        self._config = config
        self._msg = msg
        self._reader = reader
        self._unit_map = unit_map

    @property
    def taxonomy(self) -> Taxonomy:
        return self._report.taxonomy

    def getSimpleUnit(
        self, unitHolder: XbrlConceptCellRangeMetadata, cell: CellType
    ) -> Optional[QName]:
        if not cell.value:
            return None
        cellValue = str(cell.value).strip()
        candidates = [cellValue]
        candidates.extend(re.findall(r"\((.*?)\)", cellValue))
        possible_units = [
            unit
            for c in candidates
            if (unit := self.taxonomy.UTR.getQNameForUnitId(c)) is not None
        ]
        if not possible_units:
            candidates = [
                cleanUnitTextFromExcel(c, self._config.cellUnitReplacements)
                for c in candidates
            ]
            possible_units = [
                unit
                for c in candidates
                if (unit := self.taxonomy.UTR.getQNameForUnitId(c)) is not None
            ]
            if possible_units:
                self._msg.warning(
                    f"Workaround performed for mislabelled unit for {unitHolder.concept.qname}. Cell value '{cellValue}'. Unit ids now guessed: [{', '.join(str(qname) for qname in possible_units)}]",
                    MessageType.DevInfo,
                    concept=unitHolder.concept,
                    ref=unitHolder.excelRef(cell),
                )
        match len(possible_units):
            case 1:
                return possible_units[0]
            case 0:
                return None
            case _:
                self._msg.error(
                    f"Ambiguous unit specified in cell '{cellValue}'. Identified possible units: {possible_units}",
                    MessageType.ExcelParsing,
                    ref=unitHolder.excelRef(cell),
                )
                return None

    def setUnitForName(
        self,
        conceptHolder: XbrlConceptCellRangeMetadata,
        factBuilder: FactBuilder,
        *,
        row: int = -1,
        specifiedUnitHolder: Optional[XbrlConceptCellRangeMetadata] = None,
        sharedRange: Optional[bool] = None,
    ) -> bool:
        concept = conceptHolder.concept
        unitHolder: Optional[XbrlConceptCellRangeMetadata]
        if specifiedUnitHolder is not None:
            unitHolder = specifiedUnitHolder
        else:
            unitHolder = self._unit_map.get(concept)

        if unitHolder:
            cell = self._reader.getSingleCell(unitHolder, row=row)
            if cell is None or cell.value is None:
                self._msg.error(
                    f"Unable to find unit in expected part of {unitHolder.definedName.name}. Related concept {conceptHolder.definedName.name} has coordinates {conceptHolder.excelRef()}.",
                    MessageType.DevInfo,
                    ref=unitHolder,
                )
                return False
            if (unit := self.getSimpleUnit(unitHolder, cell)) is not None:
                if self.taxonomy.UTR.valid(concept.dataType, unit):
                    factBuilder.setSimpleUnit(unit)
                    return True
                elif specifiedUnitHolder:
                    if not sharedRange:
                        self._msg.warning(
                            f"Unable to create fact due to specified cell value '{cell.value}' not matching data type '{concept.dataType}'.",
                            MessageType.Conversion,
                            concept=concept,
                            ref=unitHolder.excelRef(cell),
                        )
                    return False
                else:
                    self._msg.error(
                        f"Found unit {unit} for {unitHolder.definedName.name} but it is not valid for {concept.qname} with dataType {concept.dataType}. Attempting fallback unit. Cell value '{cell.value}'.",
                        MessageType.DevInfo,
                        ref=unitHolder.excelRef(cell),
                    )
                    return self.setFallbackUnitForName(
                        conceptHolder.definedName, concept, factBuilder
                    )
            elif (unitQname := self._config.conceptsToUnits.get(concept)) is not None:
                if self.taxonomy.UTR.valid(concept.dataType, unitQname):
                    self._msg.error(
                        f"Using configured unit {unitQname} for {concept} as unit cell value could not be translated in to a unit. Cell value '{cell.value}'.",
                        MessageType.DevInfo,
                        ref=unitHolder.excelRef(cell),
                    )
                    factBuilder.setSimpleUnit(unitQname)
                    return True
                else:
                    self._msg.error(
                        f"Unit override in config is broken. Unit {unitQname} is not valid for {concept} with dataType {concept.dataType}.",
                        MessageType.DevInfo,
                        ref=conceptHolder,
                    )
            else:
                self._msg.error(
                    f"Unable to find unit for {unitHolder.definedName.name} using named range. Attempting to find unit via taxonomy. Cell value '{cell.value}'.",
                    MessageType.DevInfo,
                    ref=unitHolder.excelRef(cell),
                )

        if (units := concept.getRequiredUnitQNames()) is not None:
            if 1 == len(units):
                factBuilder.setSimpleUnit(next(iter(units)))
                return True
            else:
                self._msg.warning(
                    f"No unit found in Excel for {conceptHolder.definedName.name}. More than one unit specified as possible in the taxonomy. {units=}",
                    MessageType.Conversion,
                    concept=concept,
                    ref=conceptHolder,
                )
                return False

        candidateUnitIds = list(
            self.taxonomy.UTR.getUnitIdsForDataType(concept.dataType)
        )
        for c in candidateUnitIds:
            complex_unit = self._config.unitIdsToMeasures.get(c)
            if complex_unit is not None:
                denominator: list
                if c.endswith("_per_Monetary") and (
                    currency := self.taxonomy.UTR.getQNameForUnitId(
                        self._report.defaultAspects.get("monetary-units")
                    )
                ):
                    denominator = [currency]
                else:
                    denominator = complex_unit.denominator
                factBuilder.setComplexUnit(complex_unit.numerator, denominator)
                return True

        return self.setFallbackUnitForName(
            conceptHolder.definedName, concept, factBuilder
        )

    def setFallbackUnitForName(
        self, dn: DefinedName, concept: Concept, factBuilder: FactBuilder
    ) -> bool:
        if not concept.isNumeric:
            return False

        if (unit := self._config.dataTypesToUnits.get(concept.dataType)) is not None:
            if self.taxonomy.UTR.valid(concept.dataType, unit):
                factBuilder.setSimpleUnit(unit)
                return True

        if units := self.taxonomy.UTR.getUnitsForDataType(concept.dataType):
            chosen = next(iter(units))
            self._msg.warning(
                f"Picked fallback unit (from UTR) {chosen} for {dn.name}",
                MessageType.DevInfo,
            )
            factBuilder.setSimpleUnit(chosen)
        else:
            ultimateFallback = self.taxonomy.QNameMaker.fromString("xbrli:pure")
            self._msg.warning(
                f"Used ultimate fallback unit {ultimateFallback} for {dn.name}",
                MessageType.DevInfo,
            )
            factBuilder.setSimpleUnit(ultimateFallback)
        return True
