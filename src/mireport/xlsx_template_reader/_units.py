"""UnitResolver: find and apply the XBRL unit for a numeric fact.

The resolution chain, in order: the unit named range next to the concept
(direct id, then parenthesised, then config-corrected text), a per-concept
config override, the taxonomy's required units, config complex units for the
data type, and finally the data-type/UTR fallbacks.
"""

from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING

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
    ) -> QName | None:
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
        specifiedUnitHolder: XbrlConceptCellRangeMetadata | None = None,
        sharedRange: bool | None = None,
    ) -> bool:
        concept = conceptHolder.concept
        unitHolder: XbrlConceptCellRangeMetadata | None
        if specifiedUnitHolder is not None:
            unitHolder = specifiedUnitHolder
        else:
            unitHolder = self._unit_map.get(concept)

        if unitHolder:
            cell = self._reader.getSingleCell(unitHolder, row=row)
            if cell is None or cell.value is None:
                self._msg.warning(
                    f"No unit selected in {unitHolder.definedName.name} for {conceptHolder.definedName.name} ({conceptHolder.excelRef()}), so its facts have not been created.",
                    MessageType.Conversion,
                    concept=concept,
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

        return self.setFallbackUnitForName(
            conceptHolder.definedName, concept, factBuilder
        )

    def _requiresComplexUnit(self, concept: Concept) -> bool:
        """Whether the UTR gives the concept's type a divide unit (emissions per monetary unit,
        say) rather than named units."""
        utr = self.taxonomy.UTR
        return utr.complexUnitFor(
            concept.dataType
        ) is not None and not utr.getUnitsForDataType(concept.dataType)

    def _unitForItemType(self, itemType: QName | str) -> QName | None:
        """The unit to use for one side of a complex unit: the report's currency for a
        monetary side, else the configured unit for that item type (matched by local name,
        since the config spans taxonomies and the UTR and DTR namespaces differ), else the
        UTR's only unit for it."""
        if isinstance(itemType, str):
            return None
        if itemType.localName == "monetaryItemType":
            return self._report.defaultCurrency
        for dataType, unit in self._config.dataTypesToUnits.items():
            if dataType.localName == itemType.localName:
                return unit
        if len(units := self.taxonomy.UTR.getUnitsForDataType(itemType)) == 1:
            return next(iter(units))
        return None

    def setComplexUnitForName(
        self, dn: DefinedName, concept: Concept, factBuilder: FactBuilder
    ) -> bool:
        """Report a concept whose type takes a complex UTR unit in a divide unit, each side's unit
        chosen as _unitForItemType() says. Without one the fact is not created, with a
        warning: a single unit, or xbrli:pure, is not valid for these types."""
        complexUnit = self.taxonomy.UTR.complexUnitFor(concept.dataType)
        assert complexUnit is not None
        numerator = self._unitForItemType(complexUnit.numerator)
        denominator = self._unitForItemType(complexUnit.denominator)
        if (
            numerator is None
            or denominator is None
            or not self.taxonomy.UTR.validComplexUnit(
                concept.dataType, numerator, denominator
            )
        ):
            self._msg.warning(
                f"No unit could be chosen for {dn.name}, so its facts have not been created: "
                f"the UTR gives {concept.dataType.localName} as {complexUnit.numerator} per "
                f"{complexUnit.denominator}"
                + (
                    " and the report has no default currency."
                    if denominator is None and numerator is not None
                    else "."
                ),
                MessageType.Conversion,
                concept=concept,
            )
            return False
        factBuilder.setComplexUnit([numerator], [denominator])
        return True

    def setFallbackUnitForName(
        self, dn: DefinedName, concept: Concept, factBuilder: FactBuilder
    ) -> bool:
        if not concept.isNumeric:
            return False

        if self._requiresComplexUnit(concept):
            return self.setComplexUnitForName(dn, concept, factBuilder)

        if (
            unit := self._config.dataTypesToUnits.get(concept.dataType)
        ) is not None and self.taxonomy.UTR.valid(concept.dataType, unit):
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
