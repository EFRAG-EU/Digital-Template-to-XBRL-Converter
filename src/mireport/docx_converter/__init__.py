"""Conversion of Word documents (.docx) into HTML for text blocks.

Conversion uses mammoth. The result carries the HTML and what mammoth said
about the document, for :meth:`DocxConversion.addTo` to report. The web app
converts in a child process with a time limit, see :func:`convertDocxToHtml`.
"""

from __future__ import annotations

from mireport.docx_converter._convert import DEFAULT_TIMEOUT_SECONDS, convertDocxToHtml
from mireport.docx_converter._exceptions import DocxConversionError
from mireport.docx_converter._messages import DocxConversion

__all__ = [
    "DEFAULT_TIMEOUT_SECONDS",
    "DocxConversion",
    "DocxConversionError",
    "convertDocxToHtml",
]
