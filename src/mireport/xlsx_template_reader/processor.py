from __future__ import annotations

import logging
import re
from collections.abc import Mapping
from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING, Any, NamedTuple, assert_never

if TYPE_CHECKING:
    from collections.abc import Callable
    from datetime import date
    from typing import BinaryIO, Self

from babel import Locale
from openpyxl import Workbook

from mireport.conversionresults import (
    ConversionResultsBuilder,
    MessageType,
)
from mireport.data.disclosures import VSME_DEFAULTS
from mireport.entrypoints import (
    EntryPointSet,
    describeEntryPointSet,
    entryPointSetOf,
    isAbsoluteUri,
)
from mireport.exceptions import EarlyAbortException, InlineReportException
from mireport.localise import as_xmllang, get_locale_from_str
from mireport.report import InlineReport
from mireport.taxonomy import (
    Taxonomy,
    getTaxonomy,
    listTaxonomies,
)
from mireport.template_versions import (
    SupportedVersionSource,
    resolveSupportedTemplate,
)
from mireport.version import VersionHolder
from mireport.xlsx_template_reader._binder import WorkbookBinder
from mireport.xlsx_template_reader._bindings import WorkbookBindings
from mireport.xlsx_template_reader._constants import (
    EXCEL_VALUES_TO_BE_TREATED_AS_NONE_VALUE,
)
from mireport.xlsx_template_reader._fact_creator import FactCreator
from mireport.xlsx_template_reader._messages import Messenger
from mireport.xlsx_template_reader._reader import WorkbookReader
from mireport.xlsx_template_reader.util import (
    DATE_FORMS_HINT,
    excelDefinedNameRef,
    loadExcelFromPathOrFileLike,
)

# The defaults a disclosure configuration may name (the names are aoix's).
_KNOWN_DEFAULTS = frozenset({"entity-identifier", "entity-scheme", "monetary-units"})

L = logging.getLogger(__name__)


class VersionRelation(StrEnum):
    """How the report's Digital Template version relates to the supported one."""

    MISSING = "missing"  # blank
    INVALID = "invalid"  # not a version at all
    SAME = "same"
    NEWER = "newer"
    PATCH_BEHIND = "patch_behind"  # older, but the same major.minor
    OLDER = "older"  # a different (earlier) major.minor


class TemplateCheckResult(NamedTuple):
    report_template_version: VersionHolder
    supported_template_version: VersionHolder
    supported_template_version_source: SupportedVersionSource
    migration_enabled: bool
    validation_is_incomplete: bool
    migration_status: bool | None

    @property
    def report_template_version_relation(self) -> VersionRelation:
        report, supported = (
            self.report_template_version,
            self.supported_template_version,
        )
        if not report.is_valid:
            return (
                VersionRelation.INVALID
                if str(report).strip()
                else VersionRelation.MISSING
            )
        if report == supported:
            return VersionRelation.SAME
        if report > supported:
            return VersionRelation.NEWER
        if report.same_major_minor(supported):
            return VersionRelation.PATCH_BEHIND
        return VersionRelation.OLDER


class XlsxProcessor:
    def __init__(
        self,
        workbook: Workbook,
        results: ConversionResultsBuilder,
        defaults: Mapping[str, Any],
        /,
        outputLocale: Locale | None = None,
    ):
        if not isinstance(workbook, Workbook):
            raise TypeError(
                f"workbook must be an openpyxl Workbook, got {type(workbook).__name__}. "
                "Use XlsxProcessor.from_file() or XlsxProcessor.from_bytes() to load from a file."
            )
        self._results = results
        self._msg = Messenger(results)
        self._defaults = defaults

        # For passing through to inline report
        self._outputLocale: Locale | None = outputLocale
        self._coverImage: bytes | None = None

        self._report: InlineReport
        self._reader = WorkbookReader(workbook, results)

    @classmethod
    def from_bytes(
        cls,
        data: bytes,
        results: ConversionResultsBuilder,
        defaults: Mapping[str, Any],
        /,
        outputLocale: Locale | None = None,
    ) -> Self:
        from io import BytesIO

        wb = loadExcelFromPathOrFileLike(BytesIO(data))
        return cls(wb, results, defaults, outputLocale=outputLocale)

    @classmethod
    def from_file(
        cls,
        path_or_filelike: Path | BinaryIO,
        results: ConversionResultsBuilder,
        defaults: Mapping[str, Any],
        /,
        outputLocale: Locale | None = None,
    ) -> Self:
        wb = loadExcelFromPathOrFileLike(path_or_filelike)
        return cls(wb, results, defaults, outputLocale=outputLocale)

    @property
    def unusedNames(self) -> list[str]:
        return sorted(dn.name for dn in self._reader.unused_defined_names if dn.name)

    def createReport(self) -> InlineReport:
        """
        Add facts to InlineReport from the provided Excel workbook.
        The workbook is close()d before this method returns
        """
        try:
            self._verifyEntryPoint()
            self.abortEarlyIfErrors()
            assert self._report

            self.getAndValidateRequiredMetadata()
            self.checkTemplate()
            self.abortEarlyIfErrors()

            bindings: WorkbookBindings = WorkbookBinder(
                self._reader, self._report.taxonomy, self._results
            ).bind()
            FactCreator(
                bindings, self._reader, self._report, self._results, self._defaults
            ).create_all_facts()
            return self._report
        except EarlyAbortException as eae:
            self._msg.error(
                f"Excel conversion aborted early. {eae}",
                MessageType.ExcelParsing,
            )
            raise
        except Exception as e:
            self._msg.error(
                f"Exception encountered during processing. {e}",
                MessageType.ExcelParsing,
            )
            L.exception("Exception encountered", exc_info=e)
            raise
        finally:
            self._reader.close()

    def _determineOutputLocale(self, taxonomy: Taxonomy) -> None:
        if not taxonomy.defaultLanguage:
            return

        if self._outputLocale:
            self._msg.info(
                f"Chosen output locale: '{as_xmllang(self._outputLocale)}'. Ignoring any language specified in Excel.",
                MessageType.Conversion,
            )
            return

        # No one specified a locale ... let's see if Excel has one.
        name = "template_reporting_language"
        excelOutputLanguage = self._reader.value(name).as_str_stripped()
        if not excelOutputLanguage:
            name = "template_selected_display_language"
            excelOutputLanguage = self._reader.value(name).as_str_stripped()
        if not excelOutputLanguage:
            return

        languageCellReference = excelDefinedNameRef(self._reader.getDefinedName(name))

        if codeMatch := re.search(
            r"\[([a-zA-Z]+(?:-[a-zA-Z])*?)\]$", excelOutputLanguage
        ):
            excelOutputLocale = codeMatch.group(1)
        else:
            self._msg.error(
                f"Unable to determine desired report output language from value '{excelOutputLanguage}'",
                MessageType.ExcelParsing,
                ref=languageCellReference,
            )
            return

        bestOutputLocale = (
            taxonomy.getBestSupportedLanguage(excelOutputLocale)
            or taxonomy.defaultLanguage
        )

        if excelOutputLocale != bestOutputLocale:
            self._msg.info(
                f"Excel language specified as '{excelOutputLocale}'. Using closest match supported by the taxonomy, '{bestOutputLocale}'",
                MessageType.Conversion,
                ref=languageCellReference,
            )
        else:
            self._msg.info(
                f"Using output language specified in Excel and supported by the taxonomy: '{bestOutputLocale}'",
                MessageType.DevInfo,
                ref=languageCellReference,
            )

        self._outputLocale = get_locale_from_str(bestOutputLocale)

    def _verifyEntryPoint(self) -> None:
        name = self._defaults.get("entryPoint", "")
        # The workbook names a single URL: a one-document entry-point set.
        entryPoint = self._reader.value(name).as_str()
        entryPointSet: EntryPointSet | None = None
        validEntryPoints = listTaxonomies()
        if not entryPoint.strip():
            self._msg.error(
                "Excel template does not specify taxonomy entry point. Please use a supported template.",
                MessageType.ExcelParsing,
                ref=excelDefinedNameRef(self._reader.getDefinedName(name)),
            )
        elif not isAbsoluteUri(entryPoint):
            self._msg.error(
                f"Excel template's taxonomy entry point {entryPoint!r} is not an absolute URI. Please use a supported template.",
                MessageType.ExcelParsing,
                ref=excelDefinedNameRef(self._reader.getDefinedName(name)),
            )
        elif (entryPointSet := entryPointSetOf(entryPoint)) not in validEntryPoints:
            supported = [describeEntryPointSet(eps) for eps in validEntryPoints]
            self._msg.error(
                f"Excel report is for an unsupported taxonomy. Excel wants: {entryPoint=}. We support: {supported}",
                MessageType.ExcelParsing,
                ref=excelDefinedNameRef(self._reader.getDefinedName(name)),
            )

        self.abortEarlyIfErrors()
        # abortEarlyIfErrors() raised for every case that left this None.
        assert entryPointSet is not None
        taxonomy = getTaxonomy(entryPointSet)
        self._determineOutputLocale(taxonomy)
        self._report = InlineReport(taxonomy, self._outputLocale)
        for schemaRef in taxonomy.entryPointSet:
            self._report.addSchemaRef(schemaRef)

    def getAndValidateRequiredMetadata(self) -> None:
        self._setDefaultAspectsFromExcel()
        self._addReportingPeriods()
        self._setReportMetadata()

    def _setDefaultAspectsFromExcel(self) -> None:
        """Read the report's defaults (entity identifier and scheme, currency) from their
        named ranges into the report. The config names them as aoix does."""
        schemeLabelToURI: dict[str, str] = dict(
            self._defaults["entityIdentifierLabelsToSchemes"]
        )
        found: dict[str, str] = {}
        for aoixName, namedRangeName in self._defaults.get("aoix", {}).items():
            if self._reader.getDefinedName(namedRangeName) is None:
                self._msg.error(
                    f"Excel report must have a value for named range {namedRangeName}.",
                    MessageType.ExcelParsing,
                )
                continue
            if aoixName == "entity-scheme":
                lookup_key = (
                    self._reader.value(namedRangeName)
                    .as_str_stripped()
                    .replace(" ", "")
                    .lower()
                )
                aoixValue = schemeLabelToURI.get(lookup_key)
            else:
                aoixValue = self._reader.value(namedRangeName).as_str_stripped()

            if not aoixValue or aoixValue in EXCEL_VALUES_TO_BE_TREATED_AS_NONE_VALUE:
                self._msg.error(
                    f"Excel report must have a valid value for named range {namedRangeName}.",
                    MessageType.ExcelParsing,
                    ref=excelDefinedNameRef(
                        self._reader.getDefinedName(namedRangeName)
                    ),
                )
                continue
            found[aoixName] = aoixValue

        if "entity-identifier" in found and "entity-scheme" in found:
            self._report.setEntity(found["entity-scheme"], found["entity-identifier"])
        if "monetary-units" in found:
            self._report.setDefaultCurrency(found["monetary-units"])
        if unknown := sorted(found.keys() - _KNOWN_DEFAULTS):
            raise InlineReportException(
                f"Unsupported default(s) {unknown} in the disclosure configuration."
            )

    def _addReportingPeriods(self) -> None:
        """Declare the periods the disclosure configuration names.

        Each has a `role`: "current" (the default if omitted) or "prior". The first current period
        is the report's own; a prior one is for comparatives. A prior period whose cells are not in
        the workbook (the VSME template reports one period only) is left out, not an error."""
        for period in self._defaults.get("periods", []):
            role = period.get("role", "current")
            if role == "prior" and not all(
                self._reader.getDefinedName(period[key]) for key in ("start", "end")
            ):
                continue

            startDate = self._readPeriodDate(period["start"])
            endDate = self._readPeriodDate(period["end"])
            if startDate is None or endDate is None:
                continue

            if startDate > endDate:
                self._msg.error(
                    f"Start date {startDate} is after end date {endDate}.",
                    MessageType.ExcelParsing,
                    ref=excelDefinedNameRef(
                        self._reader.getDefinedName(period["start"])
                    ),
                )

            name = period["name"]
            if not self._report.addDurationPeriod(name, startDate, endDate):
                continue
            match role:
                case "prior":
                    self._report.setPriorPeriodName(name)
                case "current" if not self._report.hasDefaultPeriod:
                    self._report.setDefaultPeriodName(name)
                case "current":
                    pass  # a later current period does not displace the first
                case _:
                    raise InlineReportException(
                        f"Period '{name}' has an unknown role '{role}' in the disclosure configuration."
                    )

    def _readPeriodDate(self, name: str) -> date | None:
        try:
            return self._reader.value(name).as_date()
        except Exception as e:
            self._msg.error(
                f"Excel report must have a valid date for named range {name}. {DATE_FORMS_HINT} Exception: {e}",
                MessageType.ExcelParsing,
                ref=excelDefinedNameRef(self._reader.getDefinedName(name)),
            )
            return None

    def _setReportMetadata(self) -> None:
        report_defaults = self._defaults.get("report")
        if report_defaults is None:
            return
        for key, method in (
            ("entity-name", self._report.setEntityName),
            ("report-title", self._report.setReportTitle),
            ("report-subtitle", self._report.setReportSubtitle),
        ):
            self.setReportMetadata(report_defaults, key, method)

    def setReportMetadata(
        self,
        report_defaults: Mapping[str, Any],
        key: str,
        method: Callable[[str], None],
    ) -> None:
        config = report_defaults.get(key)
        if not isinstance(config, dict) or "named-range" not in config:
            self._msg.error(
                f"Missing or invalid named range for report metadata key '{key}'.",
                MessageType.ExcelParsing,
            )
            return

        named_range = config["named-range"]
        fallback = config.get("fallback")

        if (crm := self._reader.resolveRange(named_range)) is not None:
            method(self._reader.value(crm).as_str())
        elif fallback is not None:
            method(fallback)
        else:
            self._msg.error(
                f"Excel report must have a value for named range '{named_range}'.",
                MessageType.ExcelParsing,
            )

    def checkTemplate(self) -> TemplateCheckResult:
        # warn if template thinks it is incomplete
        template_validation_name = "template_overall_validation_status"
        template_validation_fail_name = "template_label_incomplete"

        validation_failed_expected_value = self._reader.value(
            template_validation_fail_name
        ).as_str(fallback="INCOMPLETE")
        validation_status = self._reader.value(template_validation_name).as_str()
        is_incomplete = bool(
            validation_failed_expected_value
            and validation_status
            and validation_status == validation_failed_expected_value
        )
        if is_incomplete:
            self._msg.warning(
                "The Digital Template reports that it is incomplete (missing mandatory items).",
                MessageType.ExcelParsing,
                ref=excelDefinedNameRef(
                    self._reader.getDefinedName(template_validation_name)
                ),
            )

        # compare the report's template version with the supported one
        template_version_name = "template_reporting_template_version"
        report_template_version = VersionHolder.parse_or_invalid(
            self._reader.value(template_version_name).as_str()
        )
        supported = resolveSupportedTemplate(
            self._reader.value(self._defaults.get("entryPoint", "")).as_str()
        )
        supported_template_version = supported.supported_template_version
        latest = (
            f"{supported_template_version} "
            f"(from {supported.supported_template_version_source})"
        )
        result = TemplateCheckResult(
            report_template_version=report_template_version,
            supported_template_version=supported_template_version,
            supported_template_version_source=supported.supported_template_version_source,
            migration_enabled=supported.migration_enabled,
            validation_is_incomplete=is_incomplete,
            migration_status=self.checkMigrationStatus(),
        )
        versionRef = excelDefinedNameRef(
            self._reader.getDefinedName(template_version_name)
        )
        relation = result.report_template_version_relation
        match relation:
            case VersionRelation.MISSING:
                self._msg.error(
                    f"The Digital Template has no version recorded. Please use a supported template; the latest version is {latest}.",
                    MessageType.ExcelParsing,
                    ref=versionRef,
                )
            case VersionRelation.INVALID:
                self._msg.error(
                    f"The Digital Template does not have a valid version identifier: '{report_template_version}'. Please use a supported template; the latest version is {latest}.",
                    MessageType.ExcelParsing,
                    ref=versionRef,
                )
            case VersionRelation.SAME:
                self._msg.info(
                    f"The Digital Template is the latest version, {latest}.",
                    MessageType.DevInfo,
                    ref=versionRef,
                )
            case VersionRelation.NEWER:
                self._msg.error(
                    f"The Digital Template is based on version {report_template_version}, which is newer than the latest version this converter supports for this taxonomy, {latest}. Please use a more recent converter that supports it.",
                    MessageType.ExcelParsing,
                    ref=versionRef,
                )
            case VersionRelation.PATCH_BEHIND:
                self._msg.info(
                    f"The Digital Template is based on version {report_template_version}. The latest version available is {latest}, consider updating the template to the latest version.",
                    MessageType.ExcelParsing,
                    ref=versionRef,
                )
            case VersionRelation.OLDER:
                self._msg.warning(
                    f"The Digital Template is based on version {report_template_version}. The latest version available is {latest}, please update/migrate to the latest version of the Digital Template, in order to avoid any error message and data loss.",
                    MessageType.ExcelParsing,
                    ref=versionRef,
                )
            case _:
                assert_never(relation)
        return result

    @classmethod
    def checkReport(cls, excelBlob: BinaryIO) -> TemplateCheckResult | None:
        """
        Check the report template for internal validation and version information.
        """
        try:
            wb = loadExcelFromPathOrFileLike(excelBlob, read_only=True)
        except Exception:
            return None
        try:
            return cls.checkWorkbook(wb)
        finally:
            wb.close()

    @classmethod
    def checkWorkbook(cls, workbook: Workbook) -> TemplateCheckResult | None:
        """
        checkReport for a workbook already loaded, which is left open to be
        converted.
        """
        try:
            processor = cls(workbook, ConversionResultsBuilder(), VSME_DEFAULTS)
            return processor.checkTemplate()
        except Exception:
            return None

    def checkMigrationStatus(self) -> bool | None:
        """
        Check the report template for internal validation and version information.
        If report has not been opened and saved (so, refreshed), formula cells return None.
        template_migration_status is a formula cell.
        """
        if self._reader.getDefinedName("template_migration_status") is None:
            return None
        return self._reader.value("template_migration_status").hasValue

    def abortEarlyIfErrors(self) -> None:
        if self._results.hasErrors():
            raise EarlyAbortException(
                "Excel report is missing required named ranges or data. Please check the report and try again."
            )
