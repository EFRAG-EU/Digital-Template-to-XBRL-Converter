"""Converting a batch of supplementary PDFs, reporting through the usual channel.

Shared by both front ends: the CLI and the web app need identical behaviour, and
the policy encoded here — what is fatal, what is a warning, what still ships —
must not be able to drift between them.

The policy: the original PDF is always attached, converted or not. Conversion
failures are warnings, never errors, so a PDF that will not convert cannot sink
an otherwise-valid report. The converter is looked for once, so a machine with
none installed says so once rather than once per file.

A successfully converted PDF then becomes either a document set member
(``mode=PdfSupplementaryMode.DOCSET``, the default — see
:mod:`mireport.report.reportpackage`) or content merged into the back of the
report itself (``mode=PdfSupplementaryMode.MERGE`` — see
:mod:`mireport.pdf_converter._merge`). Either way a conversion failure falls
back to attachment-only; @mode only chooses what a *successful* conversion
becomes.
"""

from __future__ import annotations

from enum import StrEnum
from functools import partial
from typing import TYPE_CHECKING, NamedTuple

from mireport.conversionresults import MessageType, Severity, Stage
from mireport.pdf_converter._convert import convertPdfToHtml, convertPdfToXhtml
from mireport.pdf_converter._exceptions import PdfConversionError
from mireport.pdf_converter._merge import MergedAnnex, buildMergedAnnex
from mireport.pdf_converter._run import findConverter

if TYPE_CHECKING:
    from collections.abc import Sequence

    from mireport.conversionresults import ConversionResultsBuilder
    from mireport.filesupport import FilelikeAndFileName


class PdfSupplementaryMode(StrEnum):
    """How a successfully converted PDF is added to the report package."""

    DOCSET = "docset"
    MERGE = "merge"


class PdfBatchResult(NamedTuple):
    """What to hand to
    :meth:`mireport.report.inlinereport.InlineReport.getInlineReportPackage`.

    Exactly one of @docsetMembers and @mergedAnnexes is ever populated,
    according to whichever @mode was requested of
    :func:`convertSupplementaryPdfs`.
    """

    attachments: list[FilelikeAndFileName]
    docsetMembers: list[FilelikeAndFileName]
    mergedAnnexes: list[MergedAnnex]


def convertSupplementaryPdfs(
    pdfs: Sequence[FilelikeAndFileName],
    results: ConversionResultsBuilder,
    *,
    mode: PdfSupplementaryMode = PdfSupplementaryMode.DOCSET,
) -> PdfBatchResult:
    """Convert @pdfs into attachments plus, per @mode, document set members or
    merged annexes.

    Nothing raises: every failure becomes a WARNING on @results, which does not
    clear ``conversionSuccessful``.
    """
    attachments = list(pdfs)
    if not attachments:
        return PdfBatchResult([], [], [])

    with results.section(Stage.PDF):
        return _convertAll(attachments, results, mode)


def _convertAll(
    attachments: list[FilelikeAndFileName],
    results: ConversionResultsBuilder,
    mode: PdfSupplementaryMode,
) -> PdfBatchResult:
    try:
        converter = findConverter()
    except PdfConversionError as e:
        results.addMessage(
            f"Supplementary PDFs are attached to the report package but could "
            f"not be converted for display. {e}",
            Severity.WARNING,
            MessageType.Conversion,
        )
        return PdfBatchResult(attachments, [], [])

    results.addDevInfoMessage(f"Converting PDFs using {converter} (mode={mode})")
    docsetMembers: list[FilelikeAndFileName] = []
    mergedAnnexes: list[MergedAnnex] = []
    for index, attachment in enumerate(attachments, start=1):
        devInfo = partial(
            results.addMessage,
            severity=Severity.INFO,
            message_type=MessageType.DevInfo,
            source=attachment.filename,
        )
        devInfo("Converting for display")
        try:
            if mode == PdfSupplementaryMode.MERGE:
                annex = buildMergedAnnex(
                    convertPdfToXhtml(
                        attachment.fileContent,
                        converter=converter,
                        injectIxHeader=False,
                    ),
                    index=index,
                    label=attachment.filename,
                )
            else:
                converted = convertPdfToHtml(
                    attachment.fileContent,
                    filename=attachment.filename,
                    converter=converter,
                )
        except PdfConversionError as e:
            results.addMessage(
                "Could not convert it for display. It is attached to the report "
                f"package unconverted. {e}",
                Severity.WARNING,
                MessageType.Conversion,
                source=attachment.filename,
            )
            continue
        if mode == PdfSupplementaryMode.MERGE:
            devInfo(f"Converted to merged {annex}")
            mergedAnnexes.append(annex)
            shown = "Converted for display as an annex to the report."
        else:
            devInfo(f"Converted to {converted}")
            docsetMembers.append(converted)
            shown = "Converted for display alongside the report."
        results.addMessage(
            f"{shown} It is also attached to the report package.",
            Severity.INFO,
            MessageType.Conversion,
            source=attachment.filename,
        )
    return PdfBatchResult(attachments, docsetMembers, mergedAnnexes)
