"""Running mammoth on a Word document, in-process or in a child process with a
time limit."""

from __future__ import annotations

import json
import logging
import subprocess
import sys
from pathlib import Path

import mammoth
from markupsafe import Markup

from mireport.docx_converter._exceptions import DocxConversionError
from mireport.docx_converter._messages import DocxConversion
from mireport.filesupport import FilelikeAndFileName

L = logging.getLogger(__name__)

DEFAULT_TIMEOUT_SECONDS = 30
_WORKER = Path(__file__).with_name("_worker.py")


def convertDocxToHtml(
    docx: FilelikeAndFileName, *, timeoutSeconds: float | None = None
) -> DocxConversion:
    """Convert the .docx @docx to HTML.

    With @timeoutSeconds, mammoth runs in a child process that is killed after
    that long, because a crafted document can keep it busy for a very long time.
    A thread could not be stopped, and while ``re`` backtracks it holds the GIL.
    Without, mammoth runs in-process: for the CLI, whose user supplies their own
    documents.

    :raises DocxConversionError: if the document could not be converted.
    """
    if timeoutSeconds is None:
        html, messages = _convertInProcess(docx)
    else:
        html, messages = _convertInChildProcess(docx, timeoutSeconds)
    return DocxConversion(filename=docx.filename, html=Markup(html), messages=messages)


def _convertInProcess(docx: FilelikeAndFileName) -> tuple[str, list[str]]:
    try:
        result = mammoth.convert_to_html(docx.fileLike())
    except Exception as e:
        raise DocxConversionError(
            f"Word document {docx.filename} could not be read."
        ) from e
    return result.value, [message.message for message in result.messages]


def _convertInChildProcess(
    docx: FilelikeAndFileName, timeoutSeconds: float
) -> tuple[str, list[str]]:
    try:
        completed = subprocess.run(
            [sys.executable, "-I", str(_WORKER)],
            input=docx.fileContent,
            capture_output=True,
            timeout=timeoutSeconds,
            check=True,
            shell=False,
        )
    except subprocess.TimeoutExpired as e:
        raise DocxConversionError(
            f"Word document {docx.filename} took longer than {timeoutSeconds:g} "
            "seconds to convert."
        ) from e
    except subprocess.CalledProcessError as e:
        detail = e.stderr.decode("utf-8", errors="replace").strip()
        L.warning(f"Converting Word document {docx.filename} failed: {detail}")
        raise DocxConversionError(
            f"Word document {docx.filename} could not be read."
        ) from e
    except (OSError, subprocess.SubprocessError) as e:
        raise DocxConversionError(
            f"Could not run the Word document converter. {e}"
        ) from e
    output = json.loads(completed.stdout)
    return output["html"], output["messages"]
