"""The Excel → Inline XBRL conversion pipeline.

Kept Flask-free so the pipeline can be unit-tested without a request context.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import TYPE_CHECKING

import mammoth
from markupsafe import Markup

from mireport.arelle.report_info import ARELLE_VERSION_INFORMATION
from mireport.conversionresults import (
    ConversionResults,
    ConversionResultsBuilder,
    MessageType,
    Severity,
)
from mireport.filesupport import ImageFileLikeAndFileName
from mireport.localise import get_locale_from_str
from mireport.report.theme import ColourPalette, DisplayMode, ReportTheme
from mireport.xlsx_template_reader.processor import VSME_DEFAULTS, XlsxProcessor

from .conversion import Conversion

if TYPE_CHECKING:
    from mireport.arelle.report_info import ArelleReportProcessor
    from mireport.conversionresults import ProcessingContext
    from mireport.report.inline_report import InlineReport

L = logging.getLogger(__name__)

_IMAGE_THEME_SETTERS: tuple[tuple[str, str], ...] = (
    ("image_logo", "setLogoImage"),
    ("image_cover", "setCoverImage"),
    ("image_background", "setBackgroundImage"),
)


def do_conversion(
    conv: Conversion, arelle: "ArelleReportProcessor"
) -> ConversionResults:
    """Drive the full Excel → validated Inline XBRL report pipeline.

    Side effects on ``conv``: may populate ``partial_fact_concepts`` (signals
    that the caller should redirect the user to upload Word documents), and on
    success stores the generated report package on ``conv.zip``.
    """
    builder = ConversionResultsBuilder(conversionId=conv.id)
    try:
        with builder.processingContext(f"Conversion {conv.id}") as pc:
            report = _load_report(conv, builder, pc)

            if _pause_for_partial_facts(report, conv, builder):
                return builder.build()
            if _complete_partial_facts(report, conv, builder):
                return builder.build()
            if not report.hasFacts:
                builder.addMessage(
                    "No facts found in InlineReport (likely due to earlier errors). "
                    "Stopping here.",
                    Severity.ERROR,
                    MessageType.Conversion,
                )
                return builder.build()

            _apply_theme(report, conv, builder, pc)
            report_package = _build_report_package(report, builder, pc)
            if report_package is None:
                return builder.build()
            _validate(report_package, arelle, builder, pc)
            conv.zip = report_package
    except Exception as e:
        message = next(iter(e.args), "")
        builder.addMessage(
            f"Exception encountered during processing. {message}",
            Severity.ERROR,
            MessageType.Conversion,
        )
        L.exception("Exception encountered", exc_info=e)

    return builder.build()


def _load_report(
    conv: Conversion,
    builder: ConversionResultsBuilder,
    pc: "ProcessingContext",
) -> "InlineReport":
    upload = conv.excel
    assert upload is not None, "Conversion has no uploaded Excel file"
    pc.mark(
        "Extracting data from Excel",
        additionalInfo=f"Using file: {upload.filename}",
    )
    requested_locale = (
        get_locale_from_str(loc) if (loc := conv.locale_str) else None
    )
    processor = XlsxProcessor.from_file(
        upload.fileLike(),
        builder,
        VSME_DEFAULTS,
        outputLocale=requested_locale,
    )
    return processor.createReport()


def _pause_for_partial_facts(
    report: "InlineReport",
    conv: Conversion,
    builder: ConversionResultsBuilder,
) -> bool:
    """If partial facts need user-supplied Word documents, record them and stop."""
    if not (report.hasPartialFacts and not conv.has_external_values):
        return False

    label_lang = report.taxonomy.getBestSupportedLanguage(report.language)
    concept_infos = [
        {
            "qname": str(c.qname),
            "label": (
                c.getStandardLabel(
                    lang=label_lang,
                    fallbackToAnyLang=True,
                    fallbackToQName=True,
                )
                or str(c.qname)
            ),
        }
        for c in report.partialFactsByConcept
    ]
    conv.partial_fact_concepts = concept_infos
    labels = ", ".join(info["label"] for info in concept_infos)
    builder.addMessage(
        f"Conversion paused: {len(concept_infos)} disclosure field(s) require "
        f"supplementary Word documents ({labels}).",
        Severity.INFO,
        MessageType.Conversion,
    )
    return True


def _complete_partial_facts(
    report: "InlineReport",
    conv: Conversion,
    builder: ConversionResultsBuilder,
) -> bool:
    """Apply user-uploaded Word documents to outstanding partial facts.

    Returns ``True`` if the conversion must stop (still-missing documents).
    """
    if not (report.hasPartialFacts and conv.has_external_values):
        return False

    external = conv.external_values
    for concept in list(report.partialFactsByConcept):
        if (docx := external.get(str(concept.qname))) is None:
            continue
        html = mammoth.convert_to_html(docx.fileLike()).value
        report.completePartialFact(concept, Markup(html))

    if not report.hasPartialFacts:
        return False

    missing = ", ".join(str(c.qname) for c in report.partialFactsByConcept)
    builder.addMessage(
        "Conversion failed: no Word document was provided for the following "
        f"disclosure field(s): {missing}.",
        Severity.ERROR,
        MessageType.Conversion,
    )
    return True


def _apply_theme(
    report: "InlineReport",
    conv: Conversion,
    builder: ConversionResultsBuilder,
    pc: "ProcessingContext",
) -> None:
    palette = ColourPalette.parse(
        conv.style_palette or ReportTheme.DEFAULT_COLOUR.label,
        default=ReportTheme.DEFAULT_COLOUR,
    )
    mode = DisplayMode.parse(conv.style_mode)
    report.theme.setColour(palette).setDisplayMode(mode)

    for session_key, setter_name in _IMAGE_THEME_SETTERS:
        if (raw := conv.raw.get(session_key)) is None:
            continue
        image, err = ImageFileLikeAndFileName.prepare(raw)
        if err:
            builder.addMessage(err, Severity.WARNING, MessageType.Conversion)
        elif image is not None:
            pc.addDevInfoMessage(f"Adding {session_key} to report {image}")
            setter: Callable = getattr(report.theme, setter_name)
            setter(image)


def _build_report_package(
    report: "InlineReport",
    builder: ConversionResultsBuilder,
    pc: "ProcessingContext",
):
    pc.mark(
        "Generating Inline Report",
        additionalInfo=f"({report.factCount} facts to include)",
    )
    report_package = report.getInlineReportPackage()
    builder.addMessage(
        f"Inline XBRL report {report_package} created "
        f"(containing {report.factCount} facts)",
        Severity.INFO,
        MessageType.Conversion,
    )
    return report_package if builder.conversionSuccessful else None


def _validate(
    report_package,
    arelle: "ArelleReportProcessor",
    builder: ConversionResultsBuilder,
    pc: "ProcessingContext",
) -> None:
    pc.mark(
        "Validating Inline Report",
        additionalInfo=(
            f"Using Arelle (XBRL Certified Software™) [{ARELLE_VERSION_INFORMATION}]"
        ),
    )
    arelle_results = arelle.validateReportPackage(report_package)
    builder.addMessages(arelle_results.messages)
