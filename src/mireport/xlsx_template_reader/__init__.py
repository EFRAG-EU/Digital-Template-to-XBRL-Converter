"""Reads filled-in digital templates (.xlsx) and converts them to Inline XBRL.

The public surface is XlsxProcessor (construct via from_file/from_bytes, then
createReport()) and TemplateCheckResult (returned by checkTemplate/checkReport).
Underscore-prefixed modules are internal.
"""

from mireport.xlsx_template_reader.processor import (
    TemplateCheckResult,
    XlsxProcessor,
)

__all__ = ["TemplateCheckResult", "XlsxProcessor"]
