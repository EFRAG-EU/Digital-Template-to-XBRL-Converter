"""Last year's report, read from a file for its comparatives (see mireport.report.comparatives).

The file is read as a whole report on its own. Its messages are about last year's report, so they
say so. One that can't be read or merged is an error, and stops the conversion
(EarlyAbortException).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import PurePath
from typing import TYPE_CHECKING, NoReturn
from zipfile import BadZipFile

from openpyxl.utils.exceptions import InvalidFileException

from mireport.conversionresults import (
    ConversionResultsBuilder,
    Message,
    MessageType,
    Severity,
)
from mireport.data.disclosures import VSME_DEFAULTS
from mireport.exceptions import EarlyAbortException, InlineReportException
from mireport.filesupport import FilelikeAndFileName
from mireport.report.comparatives import PriorMerge, mergePriorReport
from mireport.report.report import Report
from mireport.xbrljson_reader import (
    XbrlJsonDocument,
    XbrlJsonException,
    XbrlJsonProcessor,
)
from mireport.xlsx_template_reader.processor import XlsxProcessor

if TYPE_CHECKING:
    from typing import Self

MESSAGE_PREFIX = "Prior-year report: "
PRIOR_REPORT_EXTENSIONS = (".json", ".xlsx")


@dataclass(frozen=True, slots=True)
class PriorReport:
    """Last year's report, read on its own, to take this year's comparatives from."""

    report: Report
    fileName: str

    @classmethod
    def fromFile(
        cls, source: FilelikeAndFileName, results: ConversionResultsBuilder
    ) -> Self:
        """Last year's report in ``source``, read as its extension says."""
        match PurePath(source.filename).suffix.lower():
            case ".json":
                return cls.fromXbrlJson(source, results)
            case ".xlsx":
                return cls.fromWorkbook(source, results)
            case other:
                _abort(
                    results,
                    f"{MESSAGE_PREFIX}{source.filename}: a "
                    f"{other or 'file without an extension'} can't be read; give one of "
                    f"{', '.join(PRIOR_REPORT_EXTENSIONS)}.",
                )

    @classmethod
    def fromXbrlJson(
        cls, source: FilelikeAndFileName, results: ConversionResultsBuilder
    ) -> Self:
        return cls._read(source, results, _readXbrlJson)

    @classmethod
    def fromWorkbook(
        cls, source: FilelikeAndFileName, results: ConversionResultsBuilder
    ) -> Self:
        """A filled-in Digital Template, in its own template version and taxonomy. Its partial
        facts (Word text blocks) are left unfinished: only numbers are taken."""
        return cls._read(source, results, _readWorkbook)

    @classmethod
    def _read(
        cls,
        source: FilelikeAndFileName,
        results: ConversionResultsBuilder,
        reader: Callable[[FilelikeAndFileName, ConversionResultsBuilder], Report],
    ) -> Self:
        """``reader``'s report, with its messages passed on to ``results`` as last year's."""
        read = ConversionResultsBuilder(conversionId=results.conversionId)
        try:
            report = reader(source, read)
        except (InlineReportException, XbrlJsonException, EarlyAbortException) as e:
            _passOn(read, results)
            _abort(results, f"{MESSAGE_PREFIX}{e}", e)
        _passOn(read, results)
        return cls(report, source.filename)

    def mergeInto(
        self, report: Report, results: ConversionResultsBuilder
    ) -> PriorMerge:
        """Add this report's numbers and booleans for the year before ``report``'s as its
        prior period (mergePriorReport)."""
        try:
            return mergePriorReport(
                report, self.report, results, fileName=self.fileName
            )
        except InlineReportException as e:
            _abort(results, str(e), e)


def _readXbrlJson(
    source: FilelikeAndFileName, read: ConversionResultsBuilder
) -> Report:
    document = XbrlJsonDocument.fromJson(source.fileContent, source.filename)
    return XbrlJsonProcessor(document, read).createReport()


def _readWorkbook(
    source: FilelikeAndFileName, read: ConversionResultsBuilder
) -> Report:
    try:
        processor = XlsxProcessor.from_file(source.fileLike(), read, VSME_DEFAULTS)
    except (BadZipFile, InvalidFileException) as e:
        raise InlineReportException(
            f"{source.filename} is not an Excel workbook: {e}"
        ) from e
    if processor.checkMigrationStatus() is False:
        raise InlineReportException(
            f"{source.filename} has been migrated but not yet saved in Excel. "
            "Open it in Excel, save it, and give it again."
        )
    return processor.createReport()


def _passOn(read: ConversionResultsBuilder, results: ConversionResultsBuilder) -> None:
    results.addMessages(
        Message(
            f"{MESSAGE_PREFIX}{m.messageText}",
            m.severity,
            m.messageType,
            m.conceptQName,
            m.excelReference,
        )
        for m in read.messages
    )


def _abort(
    results: ConversionResultsBuilder, message: str, cause: Exception | None = None
) -> NoReturn:
    results.addMessage(message, Severity.ERROR, MessageType.Conversion)
    raise EarlyAbortException(message) from cause
