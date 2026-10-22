"""Exceptions for turning HTML into XHTML.

Rooted in ``MIReportException``, as the converters' own exceptions are, so they
sit in the same hierarchy as everything else that can go wrong in a conversion.
"""

from __future__ import annotations

from mireport.exceptions import MIReportException


class XhtmlError(MIReportException):
    """Raised when HTML could not be made into valid XHTML.

    @problem says what is wrong, as the end of a sentence about the HTML ("was
    empty.", "contains elements XHTML Strict does not define: …"). Only the
    caller knows where the HTML came from, so :meth:`about` lets it say so.
    """

    def __init__(self, problem: str) -> None:
        super().__init__(f"The HTML {problem}")
        self.problem = problem

    def about(self, source: str) -> str:
        """The message, with @source (such as "Converted PDF output") as what
        the problem is with."""
        return f"{source} {self.problem}"
