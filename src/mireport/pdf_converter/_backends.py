"""The PDF-to-HTML converters we know how to drive, in order of preference.

Both are system binaries, not Python dependencies, and neither is guaranteed to
be present. Supporting more than one is what makes the feature usable at all on
a machine that has whichever happens to be installed:

- **pdf2htmlEX** is the better converter and is preferred wherever present. It
  reproduces the PDF's own fonts and vector content, and can inline everything
  itself.
- **pdftohtml** ships with Poppler and renders each page to a bitmap. Lower
  fidelity (text is not selectable, output is an image per page), but a real
  fallback rather than nothing.

Adding a third means adding a subclass here and an entry to
``BACKENDS_IN_PREFERENCE_ORDER``; nothing else in the package needs to change.

Backends are deliberately not responsible for self-containment. pdftohtml in
particular cannot inline its own images — this Poppler build has no
``-dataurls`` option at all — so :mod:`mireport.pdf_converter._inline` handles
that for every backend afterwards, from the working directory.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, NamedTuple

if TYPE_CHECKING:
    from pathlib import Path

# Every backend writes here, inside its private working directory. Fixed rather
# than derived from the upload's name because the two tools disagree about what
# the final argument means — pdf2htmlEX treats it as a name within --dest-dir,
# pdftohtml as a path, and appends ".html" to anything not already ending in it.
WORKING_OUTPUT_NAME = "output.html"


class PdfConverterBackend:
    """How to drive one PDF-to-HTML command line."""

    name: str
    executable: str
    description: str

    def argv(self, binary: str, *, inputPath: Path, workingDir: Path) -> list[str]:
        """The full command line, as an explicit list — never a shell string,
        because these arguments derive from an uploaded filename."""
        raise NotImplementedError

    def outputPath(self, workingDir: Path) -> Path:
        return workingDir / WORKING_OUTPUT_NAME

    def __repr__(self) -> str:
        return f"{self.__class__.__name__}({self.name!r})"


class Pdf2HtmlExBackend(PdfConverterBackend):
    name = "pdf2htmlEX"
    executable = "pdf2htmlEX"
    description = "pdf2htmlEX (high fidelity, retains selectable text)"

    # One self-contained file: fonts, CSS, images and script all inlined, pages
    # not split into separate documents. --embed-javascript stays 1 even though
    # the normaliser strips every script afterwards: setting it to 0 makes the
    # script an *external* file rather than removing it, which is worse for
    # something that ships alone.
    #
    # Deliberately no --quiet: pdf2htmlEX 0.14.8 has no such option and exits
    # non-zero on an unknown one, which would fail every single conversion. Its
    # progress output goes to the captured stdout and is discarded anyway.
    FLAGS = (
        "--embed-css",
        "1",
        "--embed-font",
        "1",
        "--embed-image",
        "1",
        "--embed-javascript",
        "1",
        "--embed-outline",
        "1",
        "--svg-embed-bitmap",
        "1",
        "--split-pages",
        "0",
        "--process-outline",
        "0",
        "--printing",
        "1",
    )

    def argv(self, binary: str, *, inputPath: Path, workingDir: Path) -> list[str]:
        return [
            binary,
            *self.FLAGS,
            "--dest-dir",
            str(workingDir),
            str(inputPath),
            WORKING_OUTPUT_NAME,
        ]


class PdfToHtmlBackend(PdfConverterBackend):
    name = "pdftohtml"
    executable = "pdftohtml"
    description = "pdftohtml from Poppler (renders each page as an image)"

    # -c renders each page faithfully as a bitmap rather than approximating the
    # layout with positioned text; -s -noframes puts every page in one document
    # instead of the frameset-plus-file-per-page default. The images land beside
    # the HTML and are inlined afterwards.
    FLAGS = ("-c", "-s", "-noframes", "-q", "-fmt", "png")

    def argv(self, binary: str, *, inputPath: Path, workingDir: Path) -> list[str]:
        return [
            binary,
            *self.FLAGS,
            str(inputPath),
            str(workingDir / WORKING_OUTPUT_NAME),
        ]


BACKENDS_IN_PREFERENCE_ORDER: tuple[PdfConverterBackend, ...] = (
    Pdf2HtmlExBackend(),
    PdfToHtmlBackend(),
)


class ResolvedConverter(NamedTuple):
    """A backend paired with the executable actually found for it."""

    backend: PdfConverterBackend
    binaryPath: str

    def __str__(self) -> str:
        return f"{self.backend.name} ({self.binaryPath})"


def backendByName(name: str) -> PdfConverterBackend | None:
    for backend in BACKENDS_IN_PREFERENCE_ORDER:
        if backend.name.lower() == name.lower():
            return backend
    return None
