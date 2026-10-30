"""Exceptions for Word document conversion.

Rooted in ``MIReportException``, as :mod:`mireport.pdf_converter._exceptions`
is, so they sit in the same hierarchy as everything else that can go wrong in a
conversion.
"""

from __future__ import annotations

from mireport.exceptions import MIReportException


class DocxConversionError(MIReportException):
    """Raised when a Word document could not be converted. The message is for
    the user, and is about the document named @filename."""

    def __init__(self, message: str, *, filename: str) -> None:
        super().__init__(message)
        self.filename = filename
