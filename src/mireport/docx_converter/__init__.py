"""Conversion of Word documents (.docx) into HTML for text blocks.

Conversion uses mammoth. The result carries the HTML and what mammoth said
about the document, for :meth:`DocxConversion.addTo` to report.
"""

from __future__ import annotations

from mireport.docx_converter._convert import convertDocxToHtml
from mireport.docx_converter._messages import DocxConversion

__all__ = [
    "DocxConversion",
    "convertDocxToHtml",
]
