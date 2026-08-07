from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import TYPE_CHECKING, NamedTuple

if TYPE_CHECKING:
    from typing import BinaryIO, Callable, Optional, Self

from babel import Locale
from openpyxl import Workbook

from mireport.conversionresults import (
    ConversionResultsBuilder,
    MessageType,
    Severity,
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
from mireport.xlsx_template_reader._bindings import WorkbookBindings
from mireport.xlsx_template_reader._constants import (
    EXCEL_VALUES_TO_BE_TREATED_AS_NONE_VALUE,
)
from mireport.xlsx_template_reader._fact_creator import FactCreator
from mireport.xlsx_template_reader._reader import WorkbookReader
from mireport.xlsx_template_reader.util import (
    excelDefinedNameRef,
    loadExcelFromPathOrFileLike,
)

L = logging.getLogger(__name__)

# Re-exported for callers/tests that import VSME_DEFAULTS from this module.
__all__ = ["VSME_DEFAULTS", "XlsxProcessor", "TemplateCheckResult"]


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
        defaults: dict,
        /,
        outputLocale: Optional[Locale] = None,
    ):
        if not isinstance(workbook, Workbook):
            raise TypeError(
                f"workbook must be an openpyxl Workbook, got {type(workbook).__name__}. "
                "Use XlsxProcessor.from_file() or XlsxProcessor.from_bytes() to load from a file."
            )
        self._results = results
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
        defaults: dict,
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
        defaults: dict,
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

            bindings: WorkbookBindings = self._reader.build_bindings(
                self._report.taxonomy, self._defaults
            )
            FactCreator(
                bindings, self._reader, self._report, self._results, self._defaults
            ).create_all_facts()
            return self._report
        except EarlyAbortException as eae:
            self._results.addMessage(
                f"Excel conversion aborted early. {eae}",
                Severity.ERROR,
                MessageType.ExcelParsing,
            )
            raise
        except Exception as e:
            self._results.addMessage(
                f"Exception encountered during processing. {e}",
                Severity.ERROR,
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
            self._results.addMessage(
                f"Chosen output locale: '{as_xmllang(self._outputLocale)}'. Ignoring any language specified in Excel.",
                Severity.INFO,
                MessageType.Conversion,
            )
            return

        # No one specified a locale ... let's see if Excel has one.
        name = "template_reporting_language"
        excelOutputLanguage = self._reader.getSingleStringValue(name).strip()
        if not excelOutputLanguage:
            name = "template_selected_display_language"
            excelOutputLanguage = self._reader.getSingleStringValue(name).strip()
        if not excelOutputLanguage:
            return

        languageCellReference = excelDefinedNameRef(self._reader.getDefinedName(name))

        if codeMatch := re.search(
            r"\[([a-zA-Z]+(?:-[a-zA-Z])*?)\]$", excelOutputLanguage
        ):
            excelOutputLocale = codeMatch.group(1)
        else:
            self._results.addMessage(
                f"Unable to determine desired report output language from value '{excelOutputLanguage}'",
                Severity.ERROR,
                MessageType.ExcelParsing,
                excel_reference=languageCellReference,
            )
            return

        bestOutputLocale = (
            taxonomy.getBestSupportedLanguage(excelOutputLocale)
            or taxonomy.defaultLanguage
        )

        if excelOutputLocale != bestOutputLocale:
            self._results.addMessage(
                f"Excel language specified as '{excelOutputLocale}'. Using closest match supported by the taxonomy, '{bestOutputLocale}'",
                Severity.INFO,
                MessageType.Conversion,
                excel_reference=languageCellReference,
            )
        else:
            self._results.addMessage(
                f"Using output language specified in Excel and supported by the taxonomy: '{bestOutputLocale}'",
                Severity.INFO,
                MessageType.DevInfo,
                excel_reference=languageCellReference,
            )

        self._outputLocale = get_locale_from_str(bestOutputLocale)

    def _verifyEntryPoint(self) -> None:
        name = self._defaults.get("entryPoint", "")
        entryPoint = self._reader.getSingleStringValue(name)
        validEntryPoints = set(listTaxonomies())
        if not entryPoint:
            self._results.addMessage(
                "Excel template does not specify taxonomy entry point. Please use a supported template.",
                Severity.ERROR,
                MessageType.ExcelParsing,
                excel_reference=excelDefinedNameRef(self._reader.getDefinedName(name)),
            )
        elif entryPoint not in validEntryPoints:
            self._results.addMessage(
                f"Excel report is for an unsupported taxonomy. Excel wants: {entryPoint=}. We support: {sorted(validEntryPoints)}",
                Severity.ERROR,
                MessageType.ExcelParsing,
                excel_reference=excelDefinedNameRef(self._reader.getDefinedName(name)),
            )

        self.abortEarlyIfErrors()
        taxonomy = getTaxonomy(entryPoint)
        self._determineOutputLocale(taxonomy)
        self._report = InlineReport(taxonomy, self._outputLocale)
        self._report.addSchemaRef(entryPoint)

    def getAndValidateRequiredMetadata(self) -> None:
        defaults = self._defaults
        entityIdentifierSchemeLabelToURIs: dict[str, str] = {
            k: v for k, v in defaults["entityIdentifierLabelsToSchemes"].items()
        }
        if "aoix" in defaults:
            for aoixName, namedRangeName in defaults["aoix"].items():
                if self._reader.getDefinedName(namedRangeName) is None:
                    self._results.addMessage(
                        f"Excel report must have a value for named range {namedRangeName}.",
                        Severity.ERROR,
                        MessageType.ExcelParsing,
                    )
                    continue
                if aoixName == "entity-scheme":
                    lookup_key = (
                        self._reader.getSingleStringValue(namedRangeName)
                        .strip()
                        .replace(" ", "")
                        .lower()
                    )
                    aoixValue = entityIdentifierSchemeLabelToURIs.get(lookup_key)
                else:
                    aoixValue = self._reader.getSingleStringValue(
                        namedRangeName
                    ).strip()

                if (
                    not aoixValue
                    or aoixValue in EXCEL_VALUES_TO_BE_TREATED_AS_NONE_VALUE
                ):
                    self._results.addMessage(
                        f"Excel report must have a valid value for named range {namedRangeName}.",
                        Severity.ERROR,
                        MessageType.ExcelParsing,
                        excel_reference=excelDefinedNameRef(
                            self._reader.getDefinedName(namedRangeName)
                        ),
                    )
                    continue
                self._report.setDefaultAspect(aoixName, aoixValue)

        if "periods" in defaults:
            for period in defaults["periods"]:
                startName = period["start"]
                startDate = None
                try:
                    startDate = self._reader.getSingleDateValue(startName)
                except Exception as e:
                    self._results.addMessage(
                        f"Excel report must have a valid date for named range {startName}. Exception: {e}",
                        Severity.ERROR,
                        MessageType.ExcelParsing,
                        excel_reference=excelDefinedNameRef(
                            self._reader.getDefinedName(startName)
                        ),
                    )

                endName = period["end"]
                endDate = None
                try:
                    endDate = self._reader.getSingleDateValue(endName)
                except Exception as e:
                    self._results.addMessage(
                        f"Excel report must have a valid date for named range {endName}. Exception: {e}",
                        Severity.ERROR,
                        MessageType.ExcelParsing,
                        excel_reference=excelDefinedNameRef(
                            self._reader.getDefinedName(endName)
                        ),
                    )

                if startDate is None or endDate is None:
                    continue

                if startDate > endDate:
                    self._results.addMessage(
                        f"Start date {startDate} is after end date {endDate}.",
                        Severity.ERROR,
                        MessageType.ExcelParsing,
                        excel_reference=excelDefinedNameRef(
                            self._reader.getDefinedName(period["start"])
                        ),
                    )

                name = period["name"]
                if self._report.addDurationPeriod(
                    name,
                    startDate,
                    endDate,
                ):
                    self._report.setDefaultPeriodName(name)

        if "report" in defaults:
            report_defaults = defaults["report"]
            self.setReportMetadata(
                report_defaults, "entity-name", self._report.setEntityName
            )
            self.setReportMetadata(
                report_defaults, "report-title", self._report.setReportTitle
            )
            self.setReportMetadata(
                report_defaults, "report-subtitle", self._report.setReportSubtitle
            )

    def setReportMetadata(
        self, report_defaults: dict, key: str, method: Callable[[str], None]
    ) -> None:
        config = report_defaults.get(key)
        if not isinstance(config, dict) or "named-range" not in config:
            self._results.addMessage(
                f"Missing or invalid named range for report metadata key '{key}'.",
                Severity.ERROR,
                MessageType.ExcelParsing,
            )
            return

        named_range = config["named-range"]
        fallback = config.get("fallback")

        if self._reader.getDefinedName(named_range) is not None:
            value = self._reader.getSingleStringValue(named_range)
            method(value)
        elif fallback is not None:
            method(fallback)
        else:
            self._results.addMessage(
                f"Excel report must have a value for named range '{named_range}'.",
                Severity.ERROR,
                MessageType.ExcelParsing,
            )

    def checkTemplate(self) -> TemplateCheckResult:
        # warn if template thinks it is incomplete
        template_validation_name = "template_overall_validation_status"
        template_validation_fail_name = "template_label_incomplete"

        validation_failed_expected_value = self._reader.getSingleStringValue(
            template_validation_fail_name, fallbackValue="INCOMPLETE"
        )
        validation_status = self._reader.getSingleStringValue(template_validation_name)
        is_incomplete = bool(
            validation_failed_expected_value
            and validation_status
            and validation_status == validation_failed_expected_value
        )
        if is_incomplete:
            self._results.addMessage(
                "The Digital Template reports that it is incomplete (missing mandatory items).",
                Severity.WARNING,
                MessageType.ExcelParsing,
                excel_reference=excelDefinedNameRef(
                    self._reader.getDefinedName(template_validation_name)
                ),
            )

        # warn if template version is not the current version
        template_version_name = "template_reporting_template_version"
        template_version_string = self._reader.getSingleStringValue(
            template_version_name
        )
        excel_version = VersionHolder.parse_safe(template_version_string)
        converter_version = OUR_VERSION_HOLDER

        major_minor_match = (
            excel_version is not None
            and converter_version.major == excel_version.major
            and converter_version.minor == excel_version.minor
        )

        if not template_version_string.strip():
            self._results.addMessage(
                "The Digital Template has no version recorded. Please use a supported template (the latest version is {converter_version}).",
                Severity.ERROR,
                MessageType.ExcelParsing,
                excel_reference=excelDefinedNameRef(
                    self._reader.getDefinedName(template_version_name)
                ),
            )
        elif not excel_version:
            self._results.addMessage(
                f"The Digital Template does not have a valid version identifier: '{template_version_string}'. Please use a supported template (the latest version is {converter_version}).",
                Severity.ERROR,
                MessageType.ExcelParsing,
                excel_reference=excelDefinedNameRef(
                    self._reader.getDefinedName(template_version_name)
                ),
            )
        elif excel_version == converter_version:
            self._results.addMessage(
                f"The Digital Template is the same version as the converter {converter_version}.",
                Severity.INFO,
                MessageType.DevInfo,
                excel_reference=excelDefinedNameRef(
                    self._reader.getDefinedName(template_version_name)
                ),
            )
        elif excel_version != converter_version:
            if major_minor_match:
                self._results.addMessage(
                    f"The Digital Template is based on version {excel_version}. The latest version available is {converter_version}, consider updating the template to the latest version.",
                    Severity.INFO,
                    MessageType.ExcelParsing,
                    excel_reference=excelDefinedNameRef(
                        self._reader.getDefinedName(template_version_name)
                    ),
                )
            else:
                self._results.addMessage(
                    f"The Digital Template is based on version {excel_version}. The latest version available is {converter_version}, please update/migrate to the latest version of the Digital Template, in order to avoid any error message and data loss.",
                    Severity.WARNING,
                    MessageType.ExcelParsing,
                    excel_reference=excelDefinedNameRef(
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
        if self._reader.getDefinedName("template_migration_status") is not None:
            if self._reader.getSingleValue("template_migration_status") is None:
                return False  # not refreshed
            else:
                return True  # okay
        else:
            return None

    def abortEarlyIfErrors(self) -> None:
        if self._results.hasErrors():
            raise EarlyAbortException(
                "Excel report is missing required named ranges or data. Please check the report and try again."
            )
