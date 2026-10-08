"""Exceptions for the PDF conversion machinery.

Defined here rather than in :mod:`mireport.exceptions` because they describe
PDF-tooling failures specifically, but rooted in ``MIReportException`` so they
sit in the same hierarchy as everything else that can go wrong in a conversion.
"""

from __future__ import annotations

from mireport.exceptions import MIReportException


class PdfConversionError(MIReportException):
    """Raised when a PDF could not be converted."""


class PdfToolNotFoundError(PdfConversionError):
    """Raised when the pdf2htmlEX executable is not installed.

    A subclass of PdfConversionError so callers that only care whether
    conversion worked need catch one type, but distinguishable so the web app
    can say "this deployment has no PDF converter" rather than "your PDF is
    broken" — and can say it once rather than once per uploaded file.
    """
