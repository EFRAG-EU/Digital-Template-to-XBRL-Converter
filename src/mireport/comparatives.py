"""Last year's report, read from a file for its comparatives (see mireport.report.comparatives).

The file is read as a whole report on its own. Its messages are about last year's report, so they
go in a section of their own. One that can't be read or merged is an error, and stops the conversion
(EarlyAbortException).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from functools import partial
from pathlib import PurePath
from typing import TYPE_CHECKING, NoReturn
from zipfile import BadZipFile

from openpyxl import Workbook
from openpyxl.utils.exceptions import InvalidFileException

from mireport.arelle.report_info import ArelleReportProcessor, packageReportDocument
from mireport.arelle.support import ArelleRelatedException
from mireport.conversionresults import (
    ConversionResultsBuilder,
    Message,
    MessageType,
    Severity,
    Stage,
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
from mireport.xlsx_template_reader.util import loadExcelFromPathOrFileLike

if TYPE_CHECKING:
    from typing import Self

# A report package (.xbri for Inline XBRL, .xbr for xBRL-JSON or xBRL-XML, or a plain .zip) goes
# to Arelle as it is; a single Inline XBRL document is packaged first.
REPORT_PACKAGE_EXTENSIONS = (".xbri", ".xbr", ".zip")
PRIOR_REPORT_EXTENSIONS = (
    ".json",
    ".xlsx",
    ".xhtml",
    ".html",
    *REPORT_PACKAGE_EXTENSIONS,
)


# Parses a workbook: one already parsed may be handed back rather than parsed again.
WorkbookLoader = Callable[[FilelikeAndFileName], Workbook]


def loadWorkbook(source: FilelikeAndFileName) -> Workbook:
    """``source`` parsed as a workbook."""
    return loadExcelFromPathOrFileLike(source.fileLike())


@dataclass(frozen=True, slots=True)
class PriorReport:
    """Last year's report, read on its own, to take this year's comparatives from."""

    report: Report
    fileName: str

    @classmethod
    def fromFile(
        cls,
        source: FilelikeAndFileName,
        results: ConversionResultsBuilder,
        *,
        arelle: ArelleReportProcessor,
        loadWorkbook: WorkbookLoader = loadWorkbook,
    ) -> Self:
        """Last year's report in ``source``, read as its extension says."""
        with results.section(Stage.PRIOR, source=source.filename):
            match PurePath(source.filename).suffix.lower():
                case ".json":
                    return cls.fromXbrlJson(source, results)
                case ".xlsx":
                    return cls.fromWorkbook(source, results, loadWorkbook=loadWorkbook)
                case ".xhtml" | ".html" | ".xbri" | ".xbr" | ".zip":
                    return cls.fromInlineXbrl(source, results, arelle=arelle)
                case other:
                    _abort(
                        results,
                        f"A {other or 'file without an extension'} can't be read; "
                        f"give one of {', '.join(PRIOR_REPORT_EXTENSIONS)}.",
                    )

    @classmethod
    def fromXbrlJson(
        cls, source: FilelikeAndFileName, results: ConversionResultsBuilder
    ) -> Self:
        return cls._read(source, results, _readXbrlJson)

    @classmethod
    def fromWorkbook(
        cls,
        source: FilelikeAndFileName,
        results: ConversionResultsBuilder,
        *,
        loadWorkbook: WorkbookLoader = loadWorkbook,
    ) -> Self:
        """A filled-in Digital Template, in its own template version and taxonomy, loaded by
        ``loadWorkbook``. Its partial facts (Word text blocks) are left unfinished: only numbers
        are taken."""
        return cls._read(
            source, results, partial(_readWorkbook, loadWorkbook=loadWorkbook)
        )

    @classmethod
    def fromInlineXbrl(
        cls,
        source: FilelikeAndFileName,
        results: ConversionResultsBuilder,
        *,
        arelle: ArelleReportProcessor,
    ) -> Self:
        """Inline XBRL, or any report package, which ``arelle`` writes as xBRL-JSON. What Arelle
        finds wrong with last year's report is a warning here, not an error."""
        return cls._read(source, results, partial(_readInlineXbrl, arelle=arelle))

    @classmethod
    def _read(
        cls,
        source: FilelikeAndFileName,
        results: ConversionResultsBuilder,
        reader: Callable[[FilelikeAndFileName, ConversionResultsBuilder], Report],
    ) -> Self:
        """``reader``'s report, with its messages passed on to ``results``, in the
        section for last year's."""
        read = ConversionResultsBuilder(conversionId=results.conversionId)
        with results.section(Stage.PRIOR, source=source.filename):
            try:
                report = reader(source, read)
            except (InlineReportException, XbrlJsonException, EarlyAbortException) as e:
                results.addMessages(read.messages)
                _abort(results, str(e), e)
            results.addMessages(read.messages)
        return cls(report, source.filename)

    def mergeInto(
        self, report: Report, results: ConversionResultsBuilder
    ) -> PriorMerge:
        """Add this report's numbers and booleans for the year before ``report``'s as its
        prior period (mergePriorReport)."""
        with results.section(Stage.PRIOR, source=self.fileName):
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
    source: FilelikeAndFileName,
    read: ConversionResultsBuilder,
    *,
    loadWorkbook: WorkbookLoader,
) -> Report:
    try:
        processor = XlsxProcessor(loadWorkbook(source), read, VSME_DEFAULTS)
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


def _readInlineXbrl(
    source: FilelikeAndFileName,
    read: ConversionResultsBuilder,
    *,
    arelle: ArelleReportProcessor,
) -> Report:
    name = source.filename
    if PurePath(name).suffix.lower() not in REPORT_PACKAGE_EXTENSIONS:
        source = packageReportDocument(source)
    try:
        result = arelle.generateXbrlJson(source)
    except ArelleRelatedException as e:
        raise InlineReportException(f"{name}: {e}") from e
    read.addMessages(
        Message(
            m.messageText,
            Severity.WARNING if m.severity is Severity.ERROR else m.severity,
            m.messageType,
            m.conceptQName,
        )
        for m in result.messages
    )
    if not result.has_json:
        raise InlineReportException(f"Arelle could not read {name} as an XBRL report.")
    xbrlJson = result.xbrl_json
    document = XbrlJsonDocument.fromJson(xbrlJson.fileContent, xbrlJson.filename)
    if not document.taxonomy:
        raise InlineReportException(
            f"Arelle could not load the taxonomy of {name}."
            + (
                " Working offline, it needs last year's taxonomy package too."
                if arelle.workOffline
                else ""
            )
        )
    return XbrlJsonProcessor(document, read).createReport()


def _abort(
    results: ConversionResultsBuilder, message: str, cause: Exception | None = None
) -> NoReturn:
    results.addMessage(message, Severity.ERROR, MessageType.Conversion)
    raise EarlyAbortException(message) from cause
