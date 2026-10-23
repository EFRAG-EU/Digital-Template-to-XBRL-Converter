"""Running mammoth on a Word document, in-process or in a child process with a
time limit."""

from __future__ import annotations

import json
import logging
import subprocess
import sys
from pathlib import Path

from markupsafe import Markup

from mireport.docx_converter._exceptions import DocxConversionError
from mireport.docx_converter._messages import DocxConversion
from mireport.docx_converter._worker import convert
from mireport.filesupport import FilelikeAndFileName
from mireport.html_to_xhtml import XhtmlError, normaliseFragment

L = logging.getLogger(__name__)

DEFAULT_TIMEOUT_SECONDS = 30
_WORKER = Path(__file__).with_name("_worker.py")


def convertDocxToHtml(
    docx: FilelikeAndFileName, *, idPrefix: str, timeoutSeconds: float | None = None
) -> DocxConversion:
    """Convert the .docx @docx to HTML.

    Every id mammoth writes, for bookmarks, footnotes and comments, starts
    @idPrefix. mammoth's own are the same in every document (``footnote-1``…),
    and ids must be unique across the report, so each document needs a prefix
    of its own. It must be a valid start of an XML name, such as a concept's
    local name followed by ``-``.

    With @timeoutSeconds, mammoth runs in a child process that is killed after
    that long, because a crafted document can keep it busy for a very long time.
    A thread could not be stopped, and while ``re`` backtracks it holds the GIL.
    Without, mammoth runs in-process: for the CLI, whose user supplies their own
    documents.

    mammoth's HTML is then made XHTML Strict, here rather than in the child
    process, which imports nothing of mireport.

    :raises DocxConversionError: if the document could not be converted.
    """
    if timeoutSeconds is None:
        html, messages = _convertInProcess(docx, idPrefix)
    else:
        html, messages = _convertInChildProcess(docx, idPrefix, timeoutSeconds)
    try:
        xhtml = normaliseFragment(html)
    except XhtmlError as e:
        raise DocxConversionError(
            e.about("The document"), filename=docx.filename
        ) from e
    return DocxConversion(filename=docx.filename, html=Markup(xhtml), messages=messages)


def _convertInProcess(
    docx: FilelikeAndFileName, idPrefix: str
) -> tuple[str, list[str]]:
    try:
        return convert(docx.fileLike(), idPrefix)
    except Exception as e:
        raise DocxConversionError("Could not be read.", filename=docx.filename) from e


def _convertInChildProcess(
    docx: FilelikeAndFileName, idPrefix: str, timeoutSeconds: float
) -> tuple[str, list[str]]:
    try:
        completed = subprocess.run(
            [sys.executable, "-I", str(_WORKER), idPrefix],
            input=docx.fileContent,
            capture_output=True,
            timeout=timeoutSeconds,
            check=True,
            shell=False,
        )
    except subprocess.TimeoutExpired as e:
        raise DocxConversionError(
            f"Took longer than {timeoutSeconds:g} seconds to convert.",
            filename=docx.filename,
        ) from e
    except subprocess.CalledProcessError as e:
        detail = e.stderr.decode("utf-8", errors="replace").strip()
        L.warning(f"Converting Word document {docx.filename} failed: {detail}")
        raise DocxConversionError("Could not be read.", filename=docx.filename) from e
    except (OSError, subprocess.SubprocessError) as e:
        raise DocxConversionError(
            f"Could not run the Word document converter. {e}", filename=docx.filename
        ) from e
    output = json.loads(completed.stdout)
    return output["html"], output["messages"]
