"""Converting a batch of supplementary PDFs, reporting through the usual channel.

Shared by both front ends: the CLI and the web app need identical behaviour, and
the policy encoded here — what is fatal, what is a warning, what still ships —
must not be able to drift between them.

The policy: the original PDF is always attached, converted or not. Conversion
failures are warnings, never errors, so a PDF that will not convert cannot sink
an otherwise-valid report. The converter is looked for once, so a machine with
none installed says so once rather than once per file.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, NamedTuple

from mireport.conversionresults import MessageType, Severity
from mireport.pdf_converter._convert import convertPdfToHtml
from mireport.pdf_converter._exceptions import PdfConversionError
from mireport.pdf_converter._run import findConverter

if TYPE_CHECKING:
    from collections.abc import Sequence

    from mireport.conversionresults import ConversionResultsBuilder, ProcessingContext
    from mireport.filesupport import FilelikeAndFileName


class PdfBatchResult(NamedTuple):
    """What to hand to
    :meth:`mireport.report.inlinereport.InlineReport.getInlineReportPackage`."""

    attachments: list[FilelikeAndFileName]
    docsetMembers: list[FilelikeAndFileName]


def convertSupplementaryPdfs(
    pdfs: Sequence[FilelikeAndFileName],
    results: ConversionResultsBuilder,
    pc: ProcessingContext,
) -> PdfBatchResult:
    """Convert @pdfs into attachments and document set members.

    Nothing raises: every failure becomes a WARNING on @results, which does not
    clear ``conversionSuccessful``.
    """
    attachments = list(pdfs)
    if not attachments:
        return PdfBatchResult([], [])

    # Opened before any message is added, so that everything below is reported
    # inside this section rather than trailing the caller's previous one.
    pc.mark(
        "Converting supplementary PDFs",
        additionalInfo=f"({len(attachments)} to convert)",
    )

    try:
        converter = findConverter()
    except PdfConversionError as e:
        results.addMessage(
            f"Supplementary PDFs are attached to the report package but could "
            f"not be converted for display. {e}",
            Severity.WARNING,
            MessageType.Conversion,
        )
        return PdfBatchResult(attachments, [])

    pc.addDevInfoMessage(f"Converting PDFs using {converter}")
    members: list[FilelikeAndFileName] = []
    for attachment in attachments:
        pc.addDevInfoMessage(f"Converting {attachment.filename}")
        try:
            converted = convertPdfToHtml(
                attachment.fileContent,
                filename=attachment.filename,
                converter=converter,
            )
        except PdfConversionError as e:
            results.addMessage(
                f"Could not convert {attachment.filename} for display. It is "
                f"attached to the report package unconverted. {e}",
                Severity.WARNING,
                MessageType.Conversion,
            )
            continue
        pc.addDevInfoMessage(f"Converted PDF {attachment} to {converted}")
        members.append(converted)
    return PdfBatchResult(attachments, members)
