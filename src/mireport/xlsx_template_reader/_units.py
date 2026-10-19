"""UnitResolver: find and apply the XBRL unit for a numeric fact.

The resolution chain, in order: the unit named range next to the concept
(direct id, then parenthesised, then config-corrected text), a per-concept
config override when that text names no unit, the taxonomy's required units,
and finally the fallbacks: a divide unit for a type that requires a complex UTR
unit, the report's currency for a monetary concept, the configured unit for the
data type, the UTR's units for it, xbrli:pure.
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
    from mireport.utr import UTR
    from mireport.xlsx_template_reader._config import ConverterConfig
    from mireport.xlsx_template_reader._constants import CellType
    from mireport.xlsx_template_reader._messages import Messenger
    from mireport.xlsx_template_reader._ranges import XbrlConceptCellRangeMetadata
    from mireport.xlsx_template_reader._reader import WorkbookReader

from mireport.conversionresults import MessageType
from mireport.xml import XBRLI_NS

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

    @property
    def utr(self) -> UTR:
        return self._report.taxonomy.UTR

    def _unitsNamedBy(self, candidates: list[str]) -> list[QName]:
        return [
            unit
            for c in candidates
            if (unit := self.utr.getQNameForUnitId(c)) is not None
        ]

    def getSimpleUnit(
        self, unitHolder: XbrlConceptCellRangeMetadata, cell: CellType
    ) -> QName | None:
        if not cell.value:
            return None
        cellValue = str(cell.value).strip()
        candidates = [cellValue]
        candidates.extend(re.findall(r"\((.*?)\)", cellValue))
        possible_units = self._unitsNamedBy(candidates)
        if not possible_units:
            possible_units = self._unitsNamedBy(
                [
                    cleanUnitTextFromExcel(c, self._config.cellUnitReplacements)
                    for c in candidates
                ]
            )
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
            if cell is None:
                self._msg.warning(
                    f"No unit selected in {unitHolder.definedName.name} for {conceptHolder.definedName.name} ({conceptHolder.excelRef()}), so its facts have not been created.",
                    MessageType.Conversion,
                    concept=concept,
                    ref=unitHolder,
                )
                return False
            if (unit := self.getSimpleUnit(unitHolder, cell)) is not None:
                if self.utr.valid(concept.dataType, unit):
                    factBuilder.setSimpleUnit(unit)
                    return True
                elif self.utr.requiresComplexUnit(concept.dataType):
                    return self.setComplexUnitForName(
                        conceptHolder.definedName, concept, factBuilder, cellUnit=unit
                    )
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
                if self.utr.valid(concept.dataType, unitQname):
                    self._msg.info(
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

    def _defaultUnitFor(self, itemType: QName) -> QName | None:
        """The unit to use for ``itemType`` when the workbook gives none: the report's
        currency for a monetary type, else the configured unit for it, else the UTR's only
        unit for it."""
        if itemType.localName == "monetaryItemType":
            return self._report.defaultCurrency
        if (unit := self._config.unitForDataType(itemType)) is not None:
            return unit
        if len(units := self.utr.getUnitsForDataType(itemType)) == 1:
            return next(iter(units))
        return None

    def _noUnitReason(self, itemType: QName, unit: QName | None) -> str | None:
        """Why ``unit``, chosen for ``itemType``, cannot be used; None if it can."""
        if unit is None:
            if itemType.localName == "monetaryItemType":
                return "the report has no default currency"
            return f"no unit is configured for {itemType.localName}"
        if unit not in self.utr.getUnitsForDataType(itemType):
            return f"{unit} is not a unit for {itemType.localName}"
        return None

    def setComplexUnitForName(
        self,
        dn: DefinedName,
        concept: Concept,
        factBuilder: FactBuilder,
        *,
        cellUnit: QName | None = None,
    ) -> bool:
        """Report a concept whose type requires a complex UTR unit in a divide unit. The
        numerator is ``cellUnit`` when the unit cell names a unit for it (tCO2e for emissions
        per monetary unit, say), else each side takes its ``_defaultUnitFor``. Without both,
        the fact is not created, with a warning: a single unit, or xbrli:pure, is not valid
        for these types."""
        complexUnit = self.utr.complexUnitFor(concept.dataType)
        assert complexUnit is not None
        if cellUnit not in self.utr.getUnitsForDataType(complexUnit.numerator):
            cellUnit = None
        numerator = cellUnit or self._defaultUnitFor(complexUnit.numerator)
        denominator = self._defaultUnitFor(complexUnit.denominator)
        reasons = [
            reason
            for itemType, unit in (
                (complexUnit.numerator, numerator),
                (complexUnit.denominator, denominator),
            )
            if (reason := self._noUnitReason(itemType, unit)) is not None
        ]
        if reasons:
            self._msg.warning(
                f"No unit could be chosen for {dn.name}, so its facts have not been created: "
                f"the UTR gives {concept.dataType.localName} as "
                f"{complexUnit.numerator.localName} per {complexUnit.denominator.localName}, "
                f"and {' and '.join(reasons)}.",
                MessageType.Conversion,
                concept=concept,
            )
            return False
        assert numerator is not None and denominator is not None
        factBuilder.setComplexUnit([numerator], [denominator])
        return True

    def setFallbackUnitForName(
        self, dn: DefinedName, concept: Concept, factBuilder: FactBuilder
    ) -> bool:
        """The unit for a numeric concept the workbook gives no usable unit for."""
        if self.utr.requiresComplexUnit(concept.dataType):
            return self.setComplexUnitForName(dn, concept, factBuilder)

        if concept.isMonetary:
            if (currency := self._report.defaultCurrency) is None:
                self._msg.warning(
                    f"No currency could be chosen for {dn.name}, so its facts have not "
                    "been created: the report has no default currency.",
                    MessageType.Conversion,
                    concept=concept,
                )
                return False
            factBuilder.setSimpleUnit(currency)
            return True

        if (
            unit := self._config.unitForDataType(concept.dataType)
        ) is not None and self.utr.valid(concept.dataType, unit):
            factBuilder.setSimpleUnit(unit)
            return True

        if units := self.utr.getUnitsForDataType(concept.dataType):
            chosen = min(units)
            self._msg.warning(
                f"Picked fallback unit (from UTR) {chosen} for {dn.name}",
                MessageType.DevInfo,
            )
            factBuilder.setSimpleUnit(chosen)
        else:
            pure = self.taxonomy.QNameMaker.fromNamespaceAndLocalName(XBRLI_NS, "pure")
            self._msg.warning(
                f"Used ultimate fallback unit {pure} for {dn.name}",
                MessageType.DevInfo,
            )
            factBuilder.setSimpleUnit(pure)
        return True
