"""Finding and running a PDF-to-HTML converter.

Backend-agnostic: which command line to build is
:mod:`mireport.pdf_converter._backends`' business, and everything here — finding
an executable, running it safely, mapping every failure onto PdfConversionError
— is the same whichever is chosen.

Everything is written so that failure is a warning rather than an exception
escaping into a request: a missing binary, a non-zero exit, a pathological PDF
that never terminates, and an exit code of 0 that produced no file all become
PdfConversionError.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

from mireport.pdf_converter._backends import (
    BACKENDS_IN_PREFERENCE_ORDER,
    PdfConverterBackend,
    ResolvedConverter,
    backendByName,
)
from mireport.pdf_converter._exceptions import PdfConversionError, PdfToolNotFoundError
from mireport.pdf_converter._inline import inlineLocalResources

L = logging.getLogger(__name__)

DEFAULT_TIMEOUT_SECONDS = 60
INPUT_FILENAME = "input.pdf"
PATH_ENV_VAR = "PDF_CONVERTER_PATH"


def availableConverters() -> list[ResolvedConverter]:
    """Every known converter present on PATH, best first."""
    found = []
    for backend in BACKENDS_IN_PREFERENCE_ORDER:
        path = shutil.which(backend.executable)
        if path is not None:
            found.append(ResolvedConverter(backend, path))
    return found


def findConverter(
    binaryPath: str | None = None, *, backendName: str | None = None
) -> ResolvedConverter:
    """Resolve the best available converter.

    Call this once before converting a batch: it means one "no PDF converter
    installed" message instead of one per uploaded file.

    :param binaryPath: an explicit executable to use. Its backend is inferred
        from the filename unless @backendName says otherwise. Defaults to
        ``$PDF_CONVERTER_PATH``, which is how a deployment whose converter is
        installed off PATH names it — the same answer for every front end.
    :param backendName: force a particular backend rather than taking the best
        available one.
    :raises PdfToolNotFoundError: if no converter can be found.
    """
    if binaryPath is None:
        binaryPath = os.environ.get(PATH_ENV_VAR) or None
    if binaryPath is not None:
        backend = _backendFor(binaryPath, backendName)
        found = shutil.which(binaryPath)
        if found is None:
            raise PdfToolNotFoundError(
                f"PDF converter {binaryPath!r} was not found. "
                "PDFs cannot be converted without it."
            )
        return ResolvedConverter(backend, found)

    if backendName is not None:
        named = backendByName(backendName)
        if named is None:
            raise PdfToolNotFoundError(f"Unknown PDF converter {backendName!r}.")
        found = shutil.which(named.executable)
        if found is None:
            raise PdfToolNotFoundError(
                f"PDF converter {named.executable!r} was not found."
            )
        return ResolvedConverter(named, found)

    available = availableConverters()
    if not available:
        wanted = ", ".join(b.executable for b in BACKENDS_IN_PREFERENCE_ORDER)
        raise PdfToolNotFoundError(
            f"No PDF converter was found. PDFs cannot be converted for display "
            f"without one of: {wanted}."
        )
    return available[0]


def _backendFor(binaryPath: str, backendName: str | None) -> PdfConverterBackend:
    if backendName is not None:
        backend = backendByName(backendName)
        if backend is None:
            raise PdfToolNotFoundError(f"Unknown PDF converter {backendName!r}.")
        return backend
    # Infer from the executable's name, so that pointing binaryPath at an
    # install outside PATH still drives it correctly. Falls back to the
    # preferred backend, which is also what a test stub named for nothing in
    # particular should get.
    stem = Path(binaryPath).stem.lower()
    for candidate in BACKENDS_IN_PREFERENCE_ORDER:
        if candidate.executable.lower() in stem:
            return candidate
    return BACKENDS_IN_PREFERENCE_ORDER[0]


def runConverter(
    pdfBytes: bytes,
    *,
    converter: ResolvedConverter | None = None,
    binaryPath: str | None = None,
    timeoutSeconds: int = DEFAULT_TIMEOUT_SECONDS,
) -> bytes:
    """Convert @pdfBytes, returning self-contained but un-normalised HTML.

    Sibling files the converter wrote are inlined before the working directory
    is destroyed; the result still needs
    :func:`mireport.pdf_converter._xhtml.normaliseToXhtml` before it is fit for a
    document set.

    :raises PdfConversionError: on any failure, including a successful exit that
        produced no output file.
    """
    if converter is None:
        converter = findConverter(binaryPath)
    backend, binary = converter

    with tempfile.TemporaryDirectory(prefix="mireport-pdf-") as workingDir:
        working = Path(workingDir)
        inputPath = working / INPUT_FILENAME
        inputPath.write_bytes(pdfBytes)

        argv = backend.argv(binary, inputPath=inputPath, workingDir=working)
        try:
            subprocess.run(
                argv,
                capture_output=True,
                stdin=subprocess.DEVNULL,
                encoding="utf-8",
                errors="replace",
                timeout=timeoutSeconds,
                check=True,
                shell=False,
            )
        except subprocess.TimeoutExpired as e:
            raise PdfConversionError(
                f"PDF conversion timed out after {timeoutSeconds} seconds "
                f"({backend.name})."
            ) from e
        except subprocess.CalledProcessError as e:
            detail = (e.stderr or e.stdout or "").strip()
            raise PdfConversionError(
                f"PDF converter {backend.name} failed "
                f"(exit code {e.returncode}). {detail}".strip()
            ) from e
        except (OSError, subprocess.SubprocessError) as e:
            raise PdfConversionError(
                f"Could not run the PDF converter {backend.name}. {e}"
            ) from e

        outputPath = backend.outputPath(working)
        if not outputPath.is_file():
            raise PdfConversionError(
                f"PDF converter {backend.name} reported success but produced no "
                "output file."
            )
        return inlineLocalResources(outputPath.read_bytes(), working)
