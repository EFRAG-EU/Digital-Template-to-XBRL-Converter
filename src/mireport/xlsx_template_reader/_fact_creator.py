"""Create XBRL facts from a WorkbookBindings + InlineReport."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from openpyxl.workbook.defined_name import DefinedName

    from mireport.taxonomy import Taxonomy
    from mireport.xlsx_template_reader._reader import WorkbookReader

from dateutil.relativedelta import relativedelta

from mireport.conversionresults import ConversionResultsBuilder, MessageType
from mireport.report import InlineReport
from mireport.report.factbuilder import FactBuilder
from mireport.typealiases import FactValue
from mireport.xlsx_template_reader._bindings import WorkbookBindings
from mireport.xlsx_template_reader._config import ConverterConfig
from mireport.xlsx_template_reader._constants import (
    EXCEL_VALUES_TO_BE_TREATED_AS_NONE_VALUE,
    UNHANDLED_NAMES_TO_IGNORE,
)
from mireport.xlsx_template_reader._enumerations import resolveMemberByLabel
from mireport.xlsx_template_reader._fact_support import (
    addFactToReport,
    processNumeric,
)
from mireport.xlsx_template_reader._footnotes import FootnoteFactCreator
from mireport.xlsx_template_reader._messages import Messenger
from mireport.xlsx_template_reader._ranges import (
    XbrlConceptCellRangeMetadata,
)
from mireport.xlsx_template_reader._tables import TableFactCreator
from mireport.xlsx_template_reader._units import UnitResolver
from mireport.xlsx_template_reader.util import (
    conceptsToText,
    getDateFromValue,
)

L = logging.getLogger(__name__)

EE_SET_DESIRED_EMPTY_PLACEHOLDER_VALUE = "None"


# ---------------------------------------------------------------------------
# Module-level helpers (moved from processor.py)
# ---------------------------------------------------------------------------


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
        self._config = ConverterConfig.fromDefaults(defaults, report.taxonomy)
        # Work ledger: entries are claimed (popped) as they are turned into
        # facts; leftovers are reported by checkForUnhandledItems. The
        # bindings themselves are never mutated.
        self._pending: dict[DefinedName, XbrlConceptCellRangeMetadata] = dict(
            bindings.concept_map
        )
        self._units = UnitResolver(
            report, self._config, self._msg, reader, bindings.unit_map
        )

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
        preset_dims = self._bindings.preset_dims

        potentialPeriodHolders = [
            holder for holder in self._pending.values() if holder.concept.isAbstract
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
                self._pending.pop(dimValueDN)
                continue
            year = yearValue.raw

            if isinstance(year, bool) or not isinstance(year, float | int | str):
                self._msg.error(
                    f"Unable to extract year for {dimValueDN.name}. Cell value '{year}'",
                    MessageType.ExcelParsing,
                    concept=periodHolder.concept,
                    ref=periodHolder,
                )
                self._pending.pop(dimValueDN)
                continue

            try:
                yearInt = int(year)
                self.getOrAddNamedPeriodForYear(namedPeriod, yearInt)
                self._pending.pop(dimValueDN)
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
        TableFactCreator(
            self._report,
            self._reader,
            self._msg,
            self._config,
            self._units,
            self._bindings,
        ).createTableFacts()

    def createSimpleFacts(self) -> None:
        preset_dims = self._bindings.preset_dims

        reportable = {
            dn: stuff
            for dn, stuff in self._pending.items()
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
                self._pending.pop(dn)
                continue

            cell = self._reader.getSingleCell(dn)
            if cell is None:
                self._pending.pop(dn)
                continue

            value = cell.value
            external_value = concept in self._bindings.has_external_value
            if not external_value and (
                value is None or value in EXCEL_VALUES_TO_BE_TREATED_AS_NONE_VALUE
            ):
                self._pending.pop(dn)
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
                    self._pending.pop(dn)
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
                processNumeric(self._msg, stuff, cell, fb, value)

            if concept.isNumeric and not concept.isMonetary:
                self._units.setUnitForName(stuff, fb)
            elif concept.isMonetary:
                pass
            elif concept.isEnumerationSingle:
                match = resolveMemberByLabel(
                    self.taxonomy, self._config, str(value), ee_concept=concept
                )
                if match is None:
                    self._msg.error(
                        f"Unable to find EE concept when reporting {concept.qname}. Cell value '{value}'.",
                        MessageType.Conversion,
                    )
                else:
                    fb.setHiddenValue(match.concept.expandedName)
                    if match.viaConfiguredAlias:
                        self._msg.warning(
                            f"Workaround performed for EE member label mismatch when reporting {concept.qname}. Cell value '{value}'. Concept label '{match.concept.getStandardLabel()}'",
                            MessageType.DevInfo,
                            concept=concept,
                            ref=stuff.excelRef(cell),
                        )
                    elif match.closestLabel is not None:
                        self._msg.warning(
                            f"Using closest match EE concept when reporting {concept.qname}. Cell value '{value}'. Chosen EE domain member: {match.concept.qname} with label: '{match.closestLabel}'",
                            MessageType.Conversion,
                            concept=concept,
                            ref=stuff.excelRef(cell),
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

            self._pending.pop(dn)
            if external_value:
                self._report.addPartialFact(concept, fb)
            else:
                addFactToReport(self._report, self._msg, fb, stuff)

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
                nace_stripped = v.startswith("NACE ")
                e_label = v.replace("NACE ", "") if nace_stripped else v
                match = resolveMemberByLabel(
                    self.taxonomy, self._config, e_label, ee_concept=concept
                )
                if match is None:
                    self._msg.error(
                        f"Unable to find EE member when reporting {concept.qname}. Cell value '{v}'.",
                        MessageType.ExcelParsing,
                        concept=concept,
                        ref=stuff.excelRef(cell),
                    )
                elif match.closestLabel is not None:
                    value.append(v)
                    eeSetValue.add(match.concept)
                    self._msg.warning(
                        f"Using closest match EE concept when reporting {concept.qname}. Cell value '{v}'. Chosen EE domain member: {match.concept.qname} with label: '{match.closestLabel}'",
                        MessageType.Conversion,
                        concept=concept,
                        ref=stuff.excelRef(cell),
                    )
                else:
                    value.append(v)
                    eeSetValue.add(match.concept)
                    if nace_stripped or match.viaConfiguredAlias:
                        self._msg.warning(
                            f"Workaround performed for EE member label mismatch when reporting {concept.qname}. Cell value '{v}'. Concept label '{match.concept.getStandardLabel()}'",
                            MessageType.DevInfo,
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
                addFactToReport(self._report, self._msg, fb, stuff)
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
            addFactToReport(self._report, self._msg, fb, stuff)

    def _createFootnotes(self) -> None:
        if (binding := self._bindings.footnote) is None:
            return
        FootnoteFactCreator(self._report, self._msg, binding).createFootnotes()

    def checkForUnhandledItems(self) -> None:
        unHandled = list(self._pending.values())
        for stuff in unHandled:
            if stuff.definedName.name in UNHANDLED_NAMES_TO_IGNORE:
                continue
            self._msg.error(
                f"Failed to handle XBRL related Excel named range {stuff.definedName.name}.",
                MessageType.Conversion,
            )
