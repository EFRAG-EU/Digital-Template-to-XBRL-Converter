"""Create XBRL facts from a WorkbookBindings + InlineReport."""

from __future__ import annotations

import difflib
import logging
import re
from functools import lru_cache
from typing import TYPE_CHECKING, Callable, Iterator, Optional

if TYPE_CHECKING:
    from openpyxl.workbook.defined_name import DefinedName

    from mireport.report.fact import Fact
    from mireport.taxonomy import Concept, Taxonomy
    from mireport.xlsx_template_reader._reader import CellType, WorkbookReader

from dateutil.relativedelta import relativedelta
from openpyxl.cell import MergedCell

from mireport.conversionresults import ConversionResultsBuilder, MessageType
from mireport.exceptions import AmbiguousComponentException, InlineReportException
from mireport.report import InlineReport
from mireport.report.factbuilder import FactBuilder
from mireport.stringutil import str_to_markupsafe, stripLabelSuffix
from mireport.taxonomy import QName
from mireport.typealiases import FactValue
from mireport.xlsx_template_reader._bindings import (
    ComplexUnit,
    WorkbookBindings,
)
from mireport.xlsx_template_reader._constants import (
    EXCEL_VALUES_TO_BE_TREATED_AS_NONE_VALUE,
    UNHANDLED_NAMES_TO_IGNORE,
)
from mireport.xlsx_template_reader._messages import Messenger
from mireport.xlsx_template_reader._ranges import (
    CellRangeMetadata,
    XbrlConceptCellRangeMetadata,
)
from mireport.xlsx_template_reader.util import (
    conceptsToText,
    get_decimal_places,
    getDateFromValue,
)

L = logging.getLogger(__name__)

EE_SET_DESIRED_EMPTY_PLACEHOLDER_VALUE = "None"


# ---------------------------------------------------------------------------
# Module-level helpers (moved from processor.py)
# ---------------------------------------------------------------------------


def cleanUnitTextFromExcel(unitTest: str, replacements: dict[str, str]) -> str:
    new = unitTest
    for original, replacement in replacements.items():
        new = new.replace(original, replacement)
    return new


@lru_cache(maxsize=100)
def eeDomainByLabel(eeConcept: Concept) -> dict[str, tuple[Concept, str]]:
    if not (eeConcept.isEnumerationSet or eeConcept.isEnumerationSingle):
        raise ValueError(
            f"Concept {eeConcept} with data-type {eeConcept.dataType} is not of enumeration type."
        )

    eeDomainLabels: dict[str, tuple[Concept, str]] = dict()
    for eeMember in eeConcept.getEEDomain():
        all_labels = eeMember.getAllStandardLabels()
        for actual_label in all_labels:
            result = (eeMember, actual_label)
            eeDomainLabels[actual_label] = result
            label_no_suffix = stripLabelSuffix(actual_label)
            eeDomainLabels[label_no_suffix] = result
    return eeDomainLabels


def getClosestEEMemberMatch(
    eeConcept: Concept, text: str
) -> Optional[tuple[Concept, str]]:
    eeDomainLabels = eeDomainByLabel(eeConcept)
    closest_matches = difflib.get_close_matches(
        text, eeDomainLabels.keys(), n=1, cutoff=0.6
    )
    if closest_matches:
        return eeDomainLabels[closest_matches[0]]
    return None


# ---------------------------------------------------------------------------
# FactCreator
# ---------------------------------------------------------------------------


class FactCreator:
    def __init__(
        self,
        bindings: WorkbookBindings,
        reader: WorkbookReader,
        report: InlineReport,
        results: ConversionResultsBuilder,
        defaults: dict,
    ) -> None:
        self._bindings = bindings
        self._reader = reader
        self._report = report
        self._msg = Messenger(results)

        taxonomy = report.taxonomy
        self._configDataTypeToUnitMap: dict = {}
        self._configUnitIdsToMeasures: dict[str, ComplexUnit] = {}
        self._configCellValuesToTaxonomyLabels: dict[str, str] = {}
        self._configConceptToUnitMap: dict = {}
        self._configCellUnitReplacements: dict[str, str] = {}

        if "dataTypesToUnits" in defaults:
            for dataType, unitType in defaults["dataTypesToUnits"].items():
                self._configDataTypeToUnitMap[
                    taxonomy.QNameMaker.fromString(dataType)
                ] = taxonomy.QNameMaker.fromString(unitType)

        if "unitIdsToMeasures" in defaults:
            for unitId, unitDict in defaults["unitIdsToMeasures"].items():
                numerators = [
                    qname
                    for m in unitDict.get("numerator", [])
                    if (qname := taxonomy.UTR.getQNameForUnitId(m)) is not None
                ]
                denominators = [
                    qname
                    for m in unitDict.get("denominator", [])
                    if (qname := taxonomy.UTR.getQNameForUnitId(m)) is not None
                ]
                self._configUnitIdsToMeasures[unitId] = ComplexUnit(
                    numerator=numerators, denominator=denominators
                )

        if "conceptsToUnits" in defaults:
            for conceptQname, unitQname in defaults["conceptsToUnits"].items():
                self._configConceptToUnitMap[taxonomy.getConcept(conceptQname)] = (
                    taxonomy.QNameMaker.fromString(unitQname)
                )

        if "cellValuesToTaxonomyLabels" in defaults:
            self._configCellValuesToTaxonomyLabels.update(
                defaults["cellValuesToTaxonomyLabels"]
            )

        if "cellUnitReplacements" in defaults:
            self._configCellUnitReplacements.update(defaults["cellUnitReplacements"])

    @property
    def taxonomy(self) -> Taxonomy:
        return self._report.taxonomy

    def create_all_facts(self) -> None:
        self._createNamedPeriods()
        self.createSimpleFacts()
        self.createTableFacts()
        self._createFootnotes()
        self.checkForUnhandledItems()

    def _createNamedPeriods(self) -> None:
        concept_map = self._bindings.concept_map
        preset_dims = self._bindings.preset_dims

        potentialPeriodHolders = [
            holder for holder in concept_map.values() if holder.concept.isAbstract
        ]
        membersWithPotentialPeriods = {
            dimValue
            for dimPair in preset_dims.values()
            for dimValue in dimPair.values()
        }
        periodHolders = [
            p
            for p in potentialPeriodHolders
            if p.concept in membersWithPotentialPeriods
        ]
        for periodHolder in periodHolders:
            dimValueDN = periodHolder.definedName
            namedPeriod = dimValueDN.name or ""
            yearValue = self._reader.value(dimValueDN)
            if yearValue.isBlank:
                concept_map.pop(dimValueDN)
                continue
            year = yearValue.raw

            if isinstance(year, bool) or not isinstance(year, float | int | str):
                self._msg.error(
                    f"Unable to extract year for {dimValueDN.name}. Cell value '{year}'",
                    MessageType.ExcelParsing,
                    concept=periodHolder.concept,
                    ref=periodHolder,
                )
                concept_map.pop(dimValueDN)
                continue

            try:
                yearInt = int(year)
                self.getOrAddNamedPeriodForYear(namedPeriod, yearInt)
                concept_map.pop(dimValueDN)
            except ValueError:
                self._msg.error(
                    f"Unable to convert value '{year}' to an integer.",
                    MessageType.ExcelParsing,
                    concept=periodHolder.concept,
                    ref=periodHolder,
                )

    def getOrAddNamedPeriodForYear(self, name: str, year: int) -> str:
        if self._report.hasNamedPeriod(name):
            return name
        endOfDefault = self._report.defaultPeriod.end
        end = endOfDefault + relativedelta(year=year)
        start = end + relativedelta(years=-1, days=+1)
        self._report.addDurationPeriod(name, start, end)
        return name

    def createTableFacts(self) -> None:
        for table_binding in self._bindings.tables:
            tableStuff = table_binding.table
            tableDn = tableStuff.definedName
            primary_items = table_binding.primaryItems
            explicit_dimensions = table_binding.explicitDimensions
            typed_dimensions = table_binding.typedDimensions
            if not primary_items:
                self._msg.error(
                    f"Table {tableDn.name} has no primary items defined. Skipping.",
                    MessageType.ExcelParsing,
                    ref=tableStuff,
                )
                continue

            for priItem in primary_items:
                concept = priItem.concept
                for rnum, row in priItem.rows():
                    broken = False
                    cells = [cell for cell in row if cell.value is not None]
                    match len(cells):
                        case 0:
                            continue
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
                                broken = True
                                break

                    if (
                        value is None
                        or value in EXCEL_VALUES_TO_BE_TREATED_AS_NONE_VALUE
                    ):
                        continue

                    factBuilder = self._report.getFactBuilder()
                    factBuilder.setValue(value).setConcept(concept)

                    if (
                        presetDimensions := self._bindings.preset_dims.get(priItem)
                    ) is not None:
                        for dim, dimValue in presetDimensions.items():
                            if (
                                defaultValue := self.taxonomy.getDimensionDefault(dim)
                            ) is not None and dimValue != defaultValue:
                                factBuilder.setExplicitDimension(dim, dimValue)

                    all_dims_set = True
                    all_dims_set &= self.addTableFactTypedDimensions(
                        typed_dimensions, rnum, factBuilder
                    )
                    all_dims_set &= self.addTableFactExplicitDimensions(
                        explicit_dimensions, rnum, factBuilder
                    )
                    if not all_dims_set:
                        if value:
                            self._msg.warning(
                                f"Unable to add fact with value '{value}' due to missing dimension values.",
                                MessageType.Conversion,
                                concept=priItem.concept,
                                ref=priItem.excelRef(cell),
                            )
                        continue

                    if concept.isNumeric:
                        unitHolder = None
                        sharedRange = False
                        for candidate in table_binding.units:
                            if candidate.concept == concept:
                                unitHolder = candidate
                                break

                        if unitHolder:
                            sharedRange = any(
                                u.cellRange == unitHolder.cellRange
                                for u in table_binding.units
                                if u is not unitHolder
                            )

                        self.processNumeric(priItem, cell, factBuilder, value)
                        if not self.setUnitForName(
                            priItem,
                            factBuilder,
                            row=rnum,
                            specifiedUnitHolder=unitHolder,
                            sharedRange=sharedRange,
                        ):
                            continue

                    if concept.isEnumerationSingle:
                        if (
                            eeValue := self._report.taxonomy.getConceptForLabel(
                                str(value)
                            )
                        ) is not None:
                            factBuilder.setHiddenValue(eeValue.expandedName)
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
                            if (
                                eeValue := self._report.taxonomy.getConceptForLabel(
                                    str(v)
                                )
                            ) is not None:
                                eeValues.append(eeValue)
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
                        continue
                    else:
                        self.addFactToReport(factBuilder, priItem)

    def addTableFactTypedDimensions(
        self,
        typed_dimensions: list[XbrlConceptCellRangeMetadata],
        rnum: int,
        factBuilder: FactBuilder,
    ) -> bool:
        if not typed_dimensions:
            return True

        success: list[bool] = []
        for td in typed_dimensions:
            tdConcept = td.concept
            tdCell = self._reader.getSingleCell(td, row=rnum)
            if not tdCell:
                continue
            elif (tdValue := tdCell.value) is not None:
                success.append(True)
                if not isinstance(tdValue, FactValue):
                    tdValue = str(tdValue)
                factBuilder.setTypedDimension(tdConcept, tdValue)
            else:
                self._msg.error(
                    f"Required typed dimension {tdConcept.qname} not set",
                    MessageType.Conversion,
                    ref=td.excelRef(tdCell),
                )
        return all(success) and len(success) == len(typed_dimensions)

    def addTableFactExplicitDimensions(
        self,
        explicit_dimensions: list[XbrlConceptCellRangeMetadata],
        rnum: int,
        factBuilder: FactBuilder,
    ) -> bool:
        if not explicit_dimensions:
            return True

        success: list[bool] = []
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

            memberConcept = self.taxonomy.getConceptForLabel(str(edValue))
            if (
                memberConcept is None
                and (
                    fake_value := self._configCellValuesToTaxonomyLabels.get(
                        str(edValue)
                    )
                )
                is not None
            ):
                memberConcept = self._report.taxonomy.getConceptForLabel(fake_value)

            if memberConcept is not None:
                factBuilder.setExplicitDimension(edConcept, memberConcept)
                success.append(True)
            else:
                self._msg.error(
                    f"Required explicit dimension {edConcept.qname} not set. Cell value '{edValue}'",
                    MessageType.Conversion,
                    ref=ed.excelRef(edCell),
                )
        return all(success) and len(success) == len(explicit_dimensions)

    def addFactToReport(
        self, factBuilder: FactBuilder, holder: XbrlConceptCellRangeMetadata
    ) -> bool:
        try:
            self._report.addFact(factBuilder.buildFact())
            return True
        except InlineReportException as i:
            self._msg.warning(
                f"Unable to add fact. Encountered error: {i}",
                MessageType.Conversion,
                ref=holder,
            )
        return False

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
                cleanUnitTextFromExcel(c, self._configCellUnitReplacements)
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
            unitHolder = self._bindings.unit_map.get(concept)

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
            elif (unitQname := self._configConceptToUnitMap.get(concept)) is not None:
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
            complex_unit = self._configUnitIdsToMeasures.get(c)
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

        if (unit := self._configDataTypeToUnitMap.get(concept.dataType)) is not None:
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

    def processNumeric(
        self,
        stuff: XbrlConceptCellRangeMetadata,
        cell: CellType,
        fb: FactBuilder,
        value: Optional[object] = None,
    ) -> None:
        if value is None:
            if cell.value is None:
                self._msg.error(
                    f"Cell value is None for {stuff.definedName.name}. Unable to process numeric value.",
                    MessageType.DevInfo,
                    ref=stuff.excelRef(cell),
                )
                return
            else:
                value = cell.value

        if isinstance(value, bool) or not isinstance(value, int | float):
            self._msg.error(
                f"Cell value {value=} {type(value)} is not numeric for {stuff.definedName.name}. Unable to process numeric value.",
                MessageType.DevInfo,
                ref=stuff.excelRef(cell),
            )
            return

        decimals = get_decimal_places(cell)

        cell_is_percentage = "%" in cell.number_format
        if fb.concept is not None:
            concept_is_percentage = "percentItemType" == fb.concept.dataType.localName
            if cell_is_percentage != concept_is_percentage:
                self._msg.warning(
                    f"Cell number format and XBRL Taxonomy data type disagree about percentages. Cell number format '{cell.number_format}'. Concept data type {fb.concept.dataType}.",
                    MessageType.DevInfo,
                    concept=fb.concept,
                    ref=stuff.excelRef(cell),
                )

        if cell_is_percentage:
            fb.setPercentageValue(value, decimals, inputIsDecimalForm=True)
        else:
            fb.setDecimals(decimals)

    def createSimpleFacts(self) -> None:
        concept_map = self._bindings.concept_map
        preset_dims = self._bindings.preset_dims

        reportable = {
            dn: stuff
            for dn, stuff in concept_map.items()
            if (c := stuff.concept) and c.isReportable
        }

        for dn, stuff in reportable.copy().items():
            required_dims = self.taxonomy.getExplicitDimensionsForPrimaryItem(
                stuff.concept
            )
            preset = frozenset(preset_dims.get(stuff, {}).keys())
            unset_dims = required_dims.difference(
                self.taxonomy.defaultedDimensions, preset
            )
            if unset_dims:
                self._msg.error(
                    f"The named range {dn.name} has required dimensions that have not been set.\n The required dimensions {conceptsToText(required_dims)}.\n Missing: {conceptsToText(unset_dims)}.",
                    MessageType.DevInfo,
                )
                reportable.pop(dn)

        for dn, stuff in reportable.items():
            concept = stuff.concept
            assert concept.isReportable

            fb = self._report.getFactBuilder()

            if concept.isEnumerationSet:
                self.createEESetFact(stuff, fb)
                concept_map.pop(dn)
                continue

            cell = self._reader.getSingleCell(dn)
            if cell is None:
                concept_map.pop(dn)
                continue

            value = cell.value
            external_value = concept in self._bindings.has_external_value
            if not external_value and (
                value is None or value in EXCEL_VALUES_TO_BE_TREATED_AS_NONE_VALUE
            ):
                concept_map.pop(dn)
                continue

            if concept.isDate:
                try:
                    value = getDateFromValue(value)
                except Exception:
                    self._msg.error(
                        f"Unable to parse date from cell value '{value}' for {concept.qname}.",
                        MessageType.ExcelParsing,
                        concept=concept,
                        ref=stuff.excelRef(cell),
                    )
                    concept_map.pop(dn)
                    continue

            fb.setConcept(concept)
            if not external_value:
                if isinstance(value, FactValue):
                    fb.setValue(value)
                else:
                    self._msg.warning(
                        f"Rich object '{value}' {type(value).__name__} encountered as fact value for {concept}. Converting to string.",
                        MessageType.ExcelParsing,
                        concept=concept,
                        ref=stuff.excelRef(cell),
                    )
                    fb.setValue(str(value))

            if concept.isNumeric:
                self.processNumeric(stuff, cell, fb, value)

            if concept.isNumeric and not concept.isMonetary:
                self.setUnitForName(stuff, fb)
            elif concept.isMonetary:
                pass
            elif concept.isEnumerationSingle:
                s_value = str(value)
                eeValue = self._report.taxonomy.getConceptForLabel(s_value)
                warn = False
                if (
                    eeValue is None
                    and (
                        fake_value := self._configCellValuesToTaxonomyLabels.get(
                            s_value
                        )
                    )
                    is not None
                ):
                    eeValue = self._report.taxonomy.getConceptForLabel(fake_value)
                    warn = True
                if eeValue is not None:
                    fb.setHiddenValue(eeValue.expandedName)
                    if warn:
                        self._msg.warning(
                            f"Workaround performed for EE member label mismatch when reporting {concept.qname}. Cell value '{value}'. Concept label '{eeValue.getStandardLabel()}'",
                            MessageType.DevInfo,
                            concept=concept,
                            ref=stuff.excelRef(cell),
                        )
                elif result := getClosestEEMemberMatch(concept, s_value):
                    eeMember, label_matched = result
                    fb.setHiddenValue(eeMember.expandedName)
                    self._msg.warning(
                        f"Using closest match EE concept when reporting {concept.qname}. Cell value '{value}'. Chosen EE domain member: {eeMember.qname} with label: '{label_matched}'",
                        MessageType.Conversion,
                        concept=concept,
                        ref=stuff.excelRef(cell),
                    )
                else:
                    self._msg.error(
                        f"Unable to find EE concept when reporting {concept.qname}. Cell value '{value}'.",
                        MessageType.Conversion,
                    )

            if (presetDimensions := preset_dims.get(stuff)) is not None:
                for dim, dimValue in presetDimensions.items():
                    defaultValue = self.taxonomy.getDimensionDefault(dim)
                    if defaultValue is None or dimValue != defaultValue:
                        fb.setExplicitDimension(dim, dimValue)

                    dimValueDN = None
                    if (
                        dimValueDN := self._reader._workbook.defined_names.get(
                            dimValue.qname.localName
                        )
                    ) is None:
                        continue

                    namedPeriod: str = dimValueDN.name
                    if self._report.hasNamedPeriod(namedPeriod):
                        fb.setNamedPeriod(namedPeriod)

            concept_map.pop(dn)
            if external_value:
                self._report.addPartialFact(concept, fb)
            else:
                self.addFactToReport(fb, stuff)

    def createEESetFact(
        self, stuff: XbrlConceptCellRangeMetadata, fb: FactBuilder
    ) -> None:
        concept = stuff.concept
        assert concept.isEnumerationSet
        eeSetValue: set = set()
        value: list[str] = []
        eeDomain = concept.getEEDomain()
        cell = None

        for rnum, cnum, cell in stuff.cellsWithCoords():
            v = cell.value
            if v is None or v is False:
                continue
            if v is True:
                rindex = rnum - int(stuff.cellRange.min_row or 0)
                cindex = cnum - int(stuff.cellRange.min_col or 0)
                if 1 == stuff.populated_height:
                    index = cindex
                elif 1 == stuff.populated_width:
                    index = rindex
                elif stuff.populated_height < stuff.populated_width:
                    index = cindex
                else:
                    index = rindex

                if 0 <= index < len(eeDomain):
                    eeMember = eeDomain[index]
                else:
                    self._msg.error(
                        "Failed to process enumeration value",
                        MessageType.ExcelParsing,
                        concept=stuff.concept,
                        ref=stuff.excelRef(cell),
                    )
                    L.error(
                        f"Trying to access cell in named range {stuff.definedName.name} {rnum=} {cnum=} {stuff.cellRange.bounds=} {index=} {len(eeDomain)}"
                    )
                    continue
                eeSetValue.add(eeMember)
                value.append(
                    eeMember.getStandardLabel(
                        self._report.language,
                        fallbackIfMissing=str(eeMember.qname),
                        removeSuffix=True,
                        fallbackToAnyLang=True,
                    )
                )
            elif isinstance(v, str) and v == EE_SET_DESIRED_EMPTY_PLACEHOLDER_VALUE:
                value.append(v)
            elif isinstance(v, str):
                warn = False
                e_label = v
                if v.startswith("NACE "):
                    e_label = v.replace("NACE ", "")
                    warn = True
                eeConcept = self._report.taxonomy.getConceptForLabel(e_label)
                if (
                    eeConcept is None
                    and (
                        fake_value := self._configCellValuesToTaxonomyLabels.get(
                            e_label
                        )
                    )
                    is not None
                ):
                    warn = True
                    eeConcept = self._report.taxonomy.getConceptForLabel(fake_value)
                if eeConcept is not None:
                    value.append(v)
                    eeSetValue.add(eeConcept)
                    if warn:
                        self._msg.warning(
                            f"Workaround performed for EE member label mismatch when reporting {concept.qname}. Cell value '{v}'. Concept label '{eeConcept.getStandardLabel()}'",
                            MessageType.DevInfo,
                            concept=concept,
                            ref=stuff.excelRef(cell),
                        )
                elif result := getClosestEEMemberMatch(concept, v):
                    eeConcept, label_matched = result
                    value.append(v)
                    eeSetValue.add(eeConcept)
                    self._msg.warning(
                        f"Using closest match EE concept when reporting {concept.qname}. Cell value '{v}'. Chosen EE domain member: {eeConcept.qname} with label: '{label_matched}'",
                        MessageType.Conversion,
                        concept=concept,
                        ref=stuff.excelRef(cell),
                    )
                else:
                    self._msg.error(
                        f"Unable to find EE member when reporting {concept.qname}. Cell value '{v}'.",
                        MessageType.ExcelParsing,
                        concept=concept,
                        ref=stuff.excelRef(cell),
                    )
            else:
                self._msg.error(
                    f"Unable to find EE domain member when reporting {concept.qname}. Cell value '{v}'",
                    MessageType.Conversion,
                    concept=concept,
                    ref=stuff.excelRef(cell),
                )
        if EE_SET_DESIRED_EMPTY_PLACEHOLDER_VALUE in value:
            onlyPlaceholder = {EE_SET_DESIRED_EMPTY_PLACEHOLDER_VALUE}
            otherValues = {x for x in value if x is not None}.difference(
                onlyPlaceholder
            )
            if otherValues:
                self._msg.error(
                    f"Inconsistent values found for EE set {concept.qname}. Not creating an XBRL fact. Cell values '{value}'",
                    MessageType.Conversion,
                    concept=concept,
                    ref=stuff,
                )
            else:
                fb.setConcept(concept).setHiddenValue("").setValue(
                    EE_SET_DESIRED_EMPTY_PLACEHOLDER_VALUE
                )
                self.addFactToReport(fb, stuff)
        elif not eeSetValue:
            self._msg.info(
                f"No values found for {concept.qname} so not creating an empty XBRL fact. Cell value '{value}'",
                MessageType.DevInfo,
                concept=concept,
                ref=stuff.excelRef(cell),
            )
        else:
            fb.setConcept(concept).setHiddenValue(
                " ".join(sorted(e.expandedName for e in eeSetValue))
            ).setValue("\n".join(value))
            self.addFactToReport(fb, stuff)

    def _iter_footnote_rows(
        self,
        table_crm: CellRangeMetadata,
        text_col_indices: range,
        ref_col_indices: range,
        dim_col_indices: range | None = None,
    ) -> Iterator[tuple[str, list[tuple[str, str | None, CellType]]]]:
        """Yields (footnote_text, [(label, dim_text_or_None, cell), ...]) for each footnote."""
        current_text: str | None = None
        current_label_cells: list[tuple[str, str | None, CellType]] = []

        for _, row_cells in table_crm.rows():
            for ci in text_col_indices:
                cell = row_cells[ci]
                if isinstance(cell, MergedCell):
                    continue
                # Non-MergedCell in text column = boundary between footnotes
                if current_text is not None:
                    yield current_text, current_label_cells
                    current_label_cells = []
                if cell.value is not None:
                    raw = str(cell.value).strip()
                    current_text = raw or None
                else:
                    current_text = None
                break

            dim_text: str | None = None
            if dim_col_indices is not None:
                for ci in dim_col_indices:
                    cell = row_cells[ci]
                    if not isinstance(cell, MergedCell) and cell.value is not None:
                        raw = str(cell.value).strip()
                        if raw:
                            dim_text = raw
                            break

            for ci in ref_col_indices:
                cell = row_cells[ci]
                if not isinstance(cell, MergedCell) and cell.value is not None:
                    label = str(cell.value).strip()
                    if label:
                        current_label_cells.append((label, dim_text, cell))

        if current_text is not None:
            yield current_text, current_label_cells

    def _resolve_footnote_refs(
        self,
        label_cells: list[tuple[str, str | None, CellType]],
        warn: Callable[[str, CellType | None], object],
    ) -> list[tuple[Concept, Concept | None]]:
        """Resolve (label, dim_text, cell) rows to (concept, optional member concept) pairs."""
        resolved: list[tuple[Concept, Concept | None]] = []
        for label, dim_text, cell in label_cells:
            try:
                concept = self.taxonomy.resolveConcept(
                    label, by_label=True, by_name=True, by_qname=True
                )
            except AmbiguousComponentException as exc:
                warn(f"Footnote reference '{label}' is ambiguous: {exc}", cell)
                continue
            if concept is None:
                warn(
                    f"Footnote reference '{label}' could not be matched to a reportable taxonomy concept.",
                    cell,
                )
                continue

            member: Concept | None = None
            if dim_text:
                try:
                    member = self.taxonomy.resolveConcept(
                        dim_text,
                        by_label=True,
                        by_name=True,
                        by_qname=True,
                        only_reportable=False,
                    )
                except AmbiguousComponentException as exc:
                    warn(f"Footnote dimension '{dim_text}' is ambiguous: {exc}", cell)
                else:
                    if member is None:
                        warn(
                            f"Footnote dimension '{dim_text}' could not be resolved; "
                            f"attaching to all facts for '{label}'.",
                            cell,
                        )

            resolved.append((concept, member))
        return resolved

    def _createFootnotes(self) -> None:
        if (binding := self._bindings.footnote) is None:
            return

        table_crm = binding.table
        text_crm = binding.text
        ref_crm = binding.ref
        ref_dim_crm = binding.ref_dimension

        origin = table_crm.cellRange.min_col
        text_col_indices = range(
            text_crm.cellRange.min_col - origin, text_crm.cellRange.max_col - origin + 1
        )
        ref_col_indices = range(
            ref_crm.cellRange.min_col - origin, ref_crm.cellRange.max_col - origin + 1
        )
        dim_col_indices: range | None = None
        if ref_dim_crm is not None:
            dim_col_indices = range(
                ref_dim_crm.cellRange.min_col - origin,
                ref_dim_crm.cellRange.max_col - origin + 1,
            )

        def warn_ref(msg: str, cell: CellType | None = None) -> None:
            self._msg.warning(
                msg,
                MessageType.ExcelParsing,
                ref=ref_crm.excelRef(cell),
            )

        for text_value, label_cells in self._iter_footnote_rows(
            table_crm, text_col_indices, ref_col_indices, dim_col_indices
        ):
            if not label_cells:
                self._msg.warning(
                    f"Footnote ('{text_value[:60]}') has no concept references; skipping.",
                    MessageType.ExcelParsing,
                    ref=table_crm,
                )
                continue
            resolved_refs = self._resolve_footnote_refs(label_cells, warn_ref)
            if not resolved_refs:
                self._msg.warning(
                    f"Footnote ('{text_value[:60]}') has no resolvable concept references; skipping.",
                    MessageType.ExcelParsing,
                    ref=table_crm,
                )
                continue
            target_facts: list[Fact] = []
            for concept, member in resolved_refs:
                facts = self._report.getFacts(concept)
                if not facts:
                    warn_ref(
                        f"No facts found for concept '{concept.qname}'; footnote will not be attached.",
                    )
                elif member is not None:
                    # TODO: typed dimensions store a string value under "typed {axis_qname}"
                    # rather than a QName member — if typed domain filtering is ever needed, extend here.
                    facts = [f for f in facts if member.qname in f.aspects.values()]
                    if not facts:
                        warn_ref(
                            f"Dimension member '{member.qname}' not found among facts for "
                            f"'{concept.qname}'; footnote will not be attached.",
                        )
                else:
                    # No member specified — restrict to facts that carry no taxonomy-defined
                    # dimension context (no explicit QName key, no typed-dimension string key).
                    facts = [
                        f
                        for f in facts
                        if not any(
                            isinstance(k, QName)
                            or (isinstance(k, str) and k.startswith("typed "))
                            for k in f.aspects
                        )
                    ]
                    if not facts:
                        warn_ref(
                            f"All facts for concept '{concept.qname}' have dimensional context; "
                            f"footnote will not be attached.",
                        )
                target_facts.extend(facts)
            if not target_facts:
                continue
            self._report.addFootnoteToFacts(str_to_markupsafe(text_value), target_facts)

    def checkForUnhandledItems(self) -> None:
        unHandled = list(self._bindings.concept_map.values())
        for stuff in unHandled:
            if stuff.definedName.name in UNHANDLED_NAMES_TO_IGNORE:
                continue
            self._msg.error(
                f"Failed to handle XBRL related Excel named range {stuff.definedName.name}.",
                MessageType.Conversion,
            )
