"""The Excel → Inline XBRL conversion pipeline and the files derived from it.

Kept Flask-free so the pipeline can be tested without a request context: the
caller supplies the Arelle processor and the PDF mode that app configuration
decides.
"""

from __future__ import annotations

import logging
from pathlib import PurePath
from typing import TYPE_CHECKING

import mammoth
from markupsafe import Markup

from mireport.arelle.report_info import (
    ARELLE_VERSION_INFORMATION,
    ArelleReportProcessor,
)
from mireport.comparatives import PriorReport
from mireport.conversionresults import (
    ConversionResults,
    ConversionResultsBuilder,
    MessageType,
    Severity,
)
from mireport.data.disclosures import VSME_DEFAULTS
from mireport.filesupport import FilelikeAndFileName, ImageFileLikeAndFileName
from mireport.localise import get_locale_from_str
from mireport.pdf_converter import (
    PdfSupplementaryMode,
    convertSupplementaryPdfs,
    mergeAnnexesIntoReport,
)
from mireport.report.reportpackage import buildReportPackage
from mireport.report.theme import ColourPalette, DisplayMode, ReportTheme
from mireport.xbrljson_writer import toXbrlJson
from mireport.xlsx_template_reader.processor import XlsxProcessor

if TYPE_CHECKING:
    from mireport.conversionresults import ProcessingContext
    from mireport.report.inlinereport import InlineReport

    from .conversion import Conversion

L = logging.getLogger(__name__)

# (session key, ReportTheme setter) for each optional image.
_IMAGE_THEME_SETTERS: tuple[tuple[str, str], ...] = (
    ("image_logo", "setLogoImage"),
    ("image_cover", "setCoverImage"),
    ("image_background", "setBackgroundImage"),
)


def doConversion(
    conv: Conversion,
    arelle: ArelleReportProcessor,
    pdfMode: PdfSupplementaryMode,
) -> ConversionResults:
    """Drive the full Excel → validated Inline XBRL report pipeline.

    Side effects on ``conv``: may populate ``partial_fact_concepts`` (which
    tells the caller to ask the user for Word documents; last year's report,
    ``priorReport``, is read only once they are in), and on success stores
    the generated report package as ``conv.zip`` and the report as xBRL-JSON,
    written from the report itself, as ``conv.json``; Arelle validates both.
    """
    builder = ConversionResultsBuilder(conversionId=conv.id)
    try:
        with builder.processingContext(f"Conversion {conv.id}") as pc:
            report = _loadReport(conv, builder, pc)

            if _pauseForPartialFacts(report, conv, builder):
                return builder.build()
            if _completePartialFacts(report, conv, builder):
                return builder.build()
            _mergePriorReport(report, conv, builder, pc, arelle)
            if not report.hasFacts:
                builder.addMessage(
                    "No facts found in InlineReport (likely due to earlier errors). Stopping here.",
                    Severity.ERROR,
                    MessageType.Conversion,
                )
                return builder.build()

            _applyTheme(report, conv, builder, pc)
            reportPackage = _buildReportPackage(report, conv, builder, pc, pdfMode)
            if not builder.conversionSuccessful:
                return builder.build()
            xbrlJson = _writeXbrlJson(report, reportPackage)
            _validate(reportPackage, xbrlJson, arelle, builder, pc)
            conv.json = xbrlJson
            conv.zip = reportPackage
    except Exception as e:
        message = next(iter(e.args), "")
        builder.addMessage(
            f"Exception encountered during processing. {message}",
            Severity.ERROR,
            MessageType.Conversion,
        )
        L.exception("Exception encountered", exc_info=e)

    return builder.build()


def _loadReport(
    conv: Conversion,
    builder: ConversionResultsBuilder,
    pc: ProcessingContext,
) -> InlineReport:
    upload = conv.excel
    assert upload is not None, "Conversion has no uploaded Excel file"
    pc.mark(
        "Extracting data from Excel",
        additionalInfo=f"Using file: {upload.filename}",
    )
    if locale_str := conv.locale_str:
        requestedOutputLocale = get_locale_from_str(locale_str)
    else:
        requestedOutputLocale = None

    processor = XlsxProcessor.from_file(
        upload.fileLike(),
        builder,
        VSME_DEFAULTS,
        outputLocale=requestedOutputLocale,
    )
    return processor.createReport()


def _pauseForPartialFacts(
    report: InlineReport,
    conv: Conversion,
    builder: ConversionResultsBuilder,
) -> bool:
    """If partial facts need user-supplied Word documents, record which and
    stop. Returns True if the conversion must pause here."""
    if not (report.hasPartialFacts and not conv.has_external_values):
        return False

    label_lang = report.taxonomy.getBestSupportedLanguage(report.language)
    concept_infos = [
        {
            "qname": str(c.qname),
            "label": c.getStandardLabel(
                lang=label_lang,
                fallbackToAnyLang=True,
                fallbackToQName=True,
            )
            or str(c.qname),
        }
        for c in report.partialFactsByConcept
    ]
    conv.partial_fact_concepts = concept_infos
    labels = ", ".join(info["label"] for info in concept_infos)
    builder.addMessage(
        f"Conversion paused: {len(concept_infos)} disclosure field(s) require supplementary Word documents ({labels}).",
        Severity.INFO,
        MessageType.Conversion,
    )
    return True


def _completePartialFacts(
    report: InlineReport,
    conv: Conversion,
    builder: ConversionResultsBuilder,
) -> bool:
    """Apply the user's uploaded Word documents to the outstanding partial
    facts. Returns True if the conversion must stop because some are still
    missing a document."""
    if not (report.hasPartialFacts and conv.has_external_values):
        return False

    external = conv.external_values
    for concept in list(report.partialFactsByConcept):
        if (docx := external.get(str(concept.qname))) is None:
            continue
        mammothResult = mammoth.convert_to_html(docx.fileLike())
        report.completePartialFact(concept, Markup(mammothResult.value))

    if not report.hasPartialFacts:
        return False

    missing = ", ".join(str(c.qname) for c in report.partialFactsByConcept)
    builder.addMessage(
        f"Conversion failed: no Word document was provided for the following disclosure field(s): {missing}.",
        Severity.ERROR,
        MessageType.Conversion,
    )
    return True


def _mergePriorReport(
    report: InlineReport,
    conv: Conversion,
    builder: ConversionResultsBuilder,
    pc: ProcessingContext,
    arelle: ArelleReportProcessor,
) -> None:
    """Take the comparatives from last year's report, if one was uploaded. One
    that can't be read or merged stops the conversion, as a bad workbook does:
    the EarlyAbortException ends the processing context."""
    if (prior := conv.priorReport) is None:
        return
    pc.mark(
        "Reading last year's report", additionalInfo=f"Using file: {prior.filename}"
    )
    PriorReport.fromFile(prior, builder, arelle=arelle).mergeInto(report, builder)


def _applyTheme(
    report: InlineReport,
    conv: Conversion,
    builder: ConversionResultsBuilder,
    pc: ProcessingContext,
) -> None:
    colour = ColourPalette.parse(
        conv.style_palette or ReportTheme.DEFAULT_COLOUR.label,
        default=ReportTheme.DEFAULT_COLOUR,
    )
    mode = DisplayMode.parse(conv.style_mode)
    report.theme.setColour(colour).setDisplayMode(mode)

    for key, setterName in _IMAGE_THEME_SETTERS:
        if (stored := conv.image(key)) is None:
            continue
        image, err = ImageFileLikeAndFileName.prepare(stored)
        if err:
            builder.addMessage(err, Severity.WARNING, MessageType.Conversion)
        elif image:
            pc.addDevInfoMessage(f"Adding {key} to report {image}")
            getattr(report.theme, setterName)(image)


def _buildReportPackage(
    report: InlineReport,
    conv: Conversion,
    builder: ConversionResultsBuilder,
    pc: ProcessingContext,
    pdfMode: PdfSupplementaryMode,
) -> FilelikeAndFileName:
    pdfs = convertSupplementaryPdfs(conv.pdfs, builder, pc, mode=pdfMode)

    pc.mark(
        "Generating Inline Report",
        additionalInfo=f"({report.factCount} facts to include)",
    )
    reportFile = report.getInlineReport()
    if pdfs.mergedAnnexes:
        reportFile = mergeAnnexesIntoReport(reportFile, pdfs.mergedAnnexes)
    reportPackage = buildReportPackage(
        reportFile,
        topLevel=report.packageTopLevelName,
        docsetMembers=pdfs.docsetMembers,
        attachments=pdfs.attachments,
    )
    builder.addMessage(
        f"Inline XBRL report {reportPackage} created (containing {report.factCount} facts)",
        Severity.INFO,
        MessageType.Conversion,
    )
    return reportPackage


def _writeXbrlJson(
    report: InlineReport, reportPackage: FilelikeAndFileName
) -> FilelikeAndFileName:
    """The report as xBRL-JSON, named after its package."""
    return FilelikeAndFileName(
        fileContent=toXbrlJson(report).toJson().encode("utf-8"),
        filename=PurePath(reportPackage.filename).with_suffix(".json").name,
    )


def _validate(
    reportPackage: FilelikeAndFileName,
    xbrlJson: FilelikeAndFileName,
    arelle: ArelleReportProcessor,
    builder: ConversionResultsBuilder,
    pc: ProcessingContext,
) -> None:
    """Validate the report package, then the xBRL-JSON written alongside it: each
    is a separate Arelle session, and both sets of messages are the user's."""
    pc.mark(
        "Validating Inline Report",
        additionalInfo=f"Using Arelle (XBRL Certified Software™) [{ARELLE_VERSION_INFORMATION}]",
    )
    builder.addMessages(arelle.validateReportPackage(reportPackage).messages)
    pc.mark("Validating xBRL-JSON", additionalInfo=xbrlJson.filename)
    builder.addMessages(arelle.validateXbrlJson(xbrlJson).messages)


def ensureViewer(
    conv: Conversion, arelle: ArelleReportProcessor
) -> tuple[FilelikeAndFileName, dict[str, FilelikeAndFileName]]:
    """Generate (once) and return the viewer plus any document set members."""
    if (viewer := conv.viewer) is None:
        zip_ = conv.zip
        assert zip_ is not None, "No report package to build a viewer from"
        arelleResult = arelle.generateInlineViewer(zip_)
        conv.viewer = viewer = arelleResult.viewer
        conv.viewer_members = {
            member.filename: member
            for member in arelleResult.viewer_document_set_members
        }

    return viewer, conv.viewer_members
