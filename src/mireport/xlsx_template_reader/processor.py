from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import TYPE_CHECKING, Any, Mapping, NamedTuple

if TYPE_CHECKING:
    from datetime import date
    from typing import BinaryIO, Callable, Optional, Self

from babel import Locale
from openpyxl import Workbook

from mireport.conversionresults import (
    ConversionResultsBuilder,
    MessageType,
)
from mireport.data.disclosures import VSME_DEFAULTS
from mireport.exceptions import EarlyAbortException
from mireport.localise import as_xmllang, get_locale_from_str
from mireport.report import InlineReport
from mireport.taxonomy import (
    Taxonomy,
    getTaxonomy,
    listTaxonomies,
)
from mireport.version import OUR_VERSION_HOLDER, VersionHolder
from mireport.xlsx_template_reader._binder import WorkbookBinder
from mireport.xlsx_template_reader._bindings import WorkbookBindings
from mireport.xlsx_template_reader._constants import (
    EXCEL_VALUES_TO_BE_TREATED_AS_NONE_VALUE,
    is_error_value,
)
from mireport.xlsx_template_reader._fact_creator import FactCreator
from mireport.xlsx_template_reader._messages import Messenger
from mireport.xlsx_template_reader._reader import WorkbookReader
from mireport.xlsx_template_reader.util import (
    excelDefinedNameRef,
    loadExcelFromPathOrFileLike,
)

L = logging.getLogger(__name__)


class TemplateCheckResult(NamedTuple):
    validation_is_incomplete: bool
    version_is_same: bool
    version_major_minor_same: bool
    reported_version: VersionHolder
    migration_status: bool | None


class XlsxProcessor:
    def __init__(
        self,
        workbook: Workbook,
        results: ConversionResultsBuilder,
        defaults: Mapping[str, Any],
        /,
        outputLocale: Optional[Locale] = None,
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
        self._outputLocale: Optional[Locale] = outputLocale
        self._coverImage: Optional[bytes] = None

        self._report: InlineReport
        self._reader = WorkbookReader(workbook, results)

    @classmethod
    def from_bytes(
        cls,
        data: bytes,
        results: ConversionResultsBuilder,
        defaults: Mapping[str, Any],
        /,
        outputLocale: Optional[Locale] = None,
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
        outputLocale: Optional[Locale] = None,
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
        excelOutputLanguage = self._reader.value(name).asString().strip()
        if not excelOutputLanguage:
            name = "template_selected_display_language"
            excelOutputLanguage = self._reader.value(name).asString().strip()
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
        entryPoint = self._reader.value(name).asString()
        validEntryPoints = set(listTaxonomies())
        if not entryPoint:
            self._msg.error(
                "Excel template does not specify taxonomy entry point. Please use a supported template.",
                MessageType.ExcelParsing,
                ref=excelDefinedNameRef(self._reader.getDefinedName(name)),
            )
        elif entryPoint not in validEntryPoints:
            self._msg.error(
                f"Excel report is for an unsupported taxonomy. Excel wants: {entryPoint=}. We support: {sorted(validEntryPoints)}",
                MessageType.ExcelParsing,
                ref=excelDefinedNameRef(self._reader.getDefinedName(name)),
            )

        self.abortEarlyIfErrors()
        taxonomy = getTaxonomy(entryPoint)
        self._determineOutputLocale(taxonomy)
        self._report = InlineReport(taxonomy, self._outputLocale)
        self._report.addSchemaRef(entryPoint)

    def getAndValidateRequiredMetadata(self) -> None:
        self._setDefaultAspectsFromExcel()
        self._addReportingPeriods()
        self._setReportMetadata()

    def _setDefaultAspectsFromExcel(self) -> None:
        """Read the aoix default aspects (entity id, currency, ...) from their
        named ranges into the report."""
        schemeLabelToURI: dict[str, str] = dict(
            self._defaults["entityIdentifierLabelsToSchemes"]
        )
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
                    .asString()
                    .strip()
                    .replace(" ", "")
                    .lower()
                )
                aoixValue = schemeLabelToURI.get(lookup_key)
            else:
                aoixValue = self._reader.value(namedRangeName).asString().strip()

            if (
                not aoixValue
                or aoixValue in EXCEL_VALUES_TO_BE_TREATED_AS_NONE_VALUE
                or is_error_value(aoixValue)
            ):
                self._msg.error(
                    f"Excel report must have a valid value for named range {namedRangeName}.",
                    MessageType.ExcelParsing,
                    ref=excelDefinedNameRef(
                        self._reader.getDefinedName(namedRangeName)
                    ),
                )
                continue
            self._report.setDefaultAspect(aoixName, aoixValue)

    def _addReportingPeriods(self) -> None:
        for period in self._defaults.get("periods", []):
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
            if self._report.addDurationPeriod(name, startDate, endDate):
                self._report.setDefaultPeriodName(name)

    def _readPeriodDate(self, name: str) -> Optional[date]:
        try:
            return self._reader.value(name).asDate()
        except Exception as e:
            self._msg.error(
                f"Excel report must have a valid date for named range {name}. Exception: {e}",
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

        if self._reader.getDefinedName(named_range) is not None:
            value = self._reader.value(named_range).asString()
            method(value)
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
        ).asString(fallback="INCOMPLETE")
        validation_status = self._reader.value(template_validation_name).asString()
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

        # warn if template version is not the current version
        template_version_name = "template_reporting_template_version"
        template_version_string = self._reader.value(template_version_name).asString()
        excel_version = VersionHolder.parse_safe(template_version_string)
        converter_version = OUR_VERSION_HOLDER.strip_build_metadata

        major_minor_match = (
            excel_version is not None
            and converter_version.major == excel_version.major
            and converter_version.minor == excel_version.minor
        )

        if not template_version_string.strip():
            self._msg.error(
                "The Digital Template has no version recorded. Please use a supported template (the latest version is {converter_version}).",
                MessageType.ExcelParsing,
                ref=excelDefinedNameRef(
                    self._reader.getDefinedName(template_version_name)
                ),
            )
        elif not excel_version:
            self._msg.error(
                f"The Digital Template does not have a valid version identifier: '{template_version_string}'. Please use a supported template (the latest version is {converter_version}).",
                MessageType.ExcelParsing,
                ref=excelDefinedNameRef(
                    self._reader.getDefinedName(template_version_name)
                ),
            )
        elif excel_version == converter_version:
            self._msg.info(
                f"The Digital Template is the same version as the converter {converter_version}.",
                MessageType.DevInfo,
                ref=excelDefinedNameRef(
                    self._reader.getDefinedName(template_version_name)
                ),
            )
        elif excel_version != converter_version:
            if major_minor_match:
                self._msg.info(
                    f"The Digital Template is based on version {excel_version}. The latest version available is {converter_version}, consider updating the template to the latest version.",
                    MessageType.ExcelParsing,
                    ref=excelDefinedNameRef(
                        self._reader.getDefinedName(template_version_name)
                    ),
                )
            else:
                self._msg.warning(
                    f"The Digital Template is based on version {excel_version}. The latest version available is {converter_version}, please update/migrate to the latest version of the Digital Template, in order to avoid any error message and data loss.",
                    MessageType.ExcelParsing,
                    ref=excelDefinedNameRef(
                        self._reader.getDefinedName(template_version_name)
                    ),
                )
        return TemplateCheckResult(
            validation_is_incomplete=is_incomplete,
            version_is_same=excel_version == converter_version
            if excel_version
            else False,
            version_major_minor_same=major_minor_match,
            reported_version=excel_version
            if excel_version
            else VersionHolder(0, 0, 0, template_version_string),
            migration_status=self.checkMigrationStatus(),
        )

    @classmethod
    def checkReport(cls, excelBlob: BinaryIO) -> Optional[TemplateCheckResult]:
        """
        Check the report template for internal validation and version information.
        """
        wb = None
        try:
            wb = loadExcelFromPathOrFileLike(excelBlob, read_only=True)
            processor = cls(wb, ConversionResultsBuilder(), VSME_DEFAULTS)
            return processor.checkTemplate()
        except Exception:
            return None
        finally:
            if wb is not None:
                wb.close()

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
