"""Exceptions for turning HTML into XHTML.

Rooted in ``MIReportException``, as the converters' own exceptions are, so they
sit in the same hierarchy as everything else that can go wrong in a conversion.
"""

from __future__ import annotations

from mireport.exceptions import MIReportException


class XhtmlError(MIReportException):
    """Raised when HTML could not be made into valid XHTML.

    The message finishes a sentence whose subject the caller supplies, since
    only the caller knows where the HTML came from: ``f"Converted PDF output
    {e}"`` reads "Converted PDF output contains elements XHTML Strict does not
    define: …".
    """
