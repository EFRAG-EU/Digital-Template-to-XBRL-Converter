"""Converting a single PDF into an Inline XBRL document set member."""

from __future__ import annotations

from pathlib import PurePosixPath
from typing import TYPE_CHECKING

from mireport.filesupport import FilelikeAndFileName, zipSafeString
from mireport.pdf_converter._run import DEFAULT_TIMEOUT_SECONDS, runConverter
from mireport.pdf_converter._xhtml import normaliseToXhtml

if TYPE_CHECKING:
    from mireport.pdf_converter._backends import ResolvedConverter

OUTPUT_SUFFIX = ".xhtml"


def outputFilenameFor(filename: str) -> str:
    """Derive the document set member's filename from the uploaded PDF's.

    Note this names the file in the report package only. What the converter is
    told to write is fixed (``_backends.WORKING_OUTPUT_NAME``), so no
    user-supplied text reaches a command line as a filename at all.
    """
    lastSegment = filename.replace("\\", "/").rpartition("/")[2]
    # Leading dots are stripped before sanitising: Path treats ".pdf" as a
    # dotfile whose whole name is the stem, which would otherwise produce the
    # hidden-looking ".pdf.xhtml" for an upload named ".pdf".
    stem = (PurePosixPath(lastSegment).stem or lastSegment).lstrip(".")
    return f"{zipSafeString(stem, fallback='annex')}{OUTPUT_SUFFIX}"


def convertPdfToHtml(
    pdfBytes: bytes,
    *,
    filename: str,
    converter: ResolvedConverter | None = None,
    binaryPath: str | None = None,
    timeoutSeconds: int = DEFAULT_TIMEOUT_SECONDS,
    injectIxHeader: bool = True,
) -> FilelikeAndFileName:
    """Convert @pdfBytes into an Inline XBRL document set member.

    :param filename: the uploaded PDF's name, used to name the output.
    :param converter: the backend and executable to use. Pass the result of
        :func:`mireport.pdf_converter.findConverter` when converting several
        files, so the search happens — and any failure is reported — once.
    :param binaryPath: an explicit executable, resolved into a converter when
        @converter is not given.
    :param injectIxHeader: forwarded to
        :func:`mireport.pdf_converter._xhtml.normaliseToXhtml`. Pass False when
        the result will be merged into another report rather than shipped as
        its own document set member.
    :raises PdfToolNotFoundError: if no converter is installed.
    :raises PdfConversionError: if the conversion or its output is unusable.
    """
    raw = runConverter(
        pdfBytes,
        converter=converter,
        binaryPath=binaryPath,
        timeoutSeconds=timeoutSeconds,
    )
    return FilelikeAndFileName(
        fileContent=normaliseToXhtml(raw, injectIxHeader=injectIxHeader),
        filename=outputFilenameFor(filename),
    )
