from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterable

    from mireport.taxonomy import Concept


class MIReportException(Exception):
    """Base class for any XBRL related exceptions. Not expected to be raised directly."""

    pass


class UnitException(MIReportException):
    """Exception raised when a unit is not found in the UTR."""

    pass


class TaxonomyException(MIReportException):
    """All taxonomy related exceptions"""

    pass


class InlineReportException(MIReportException):
    """All Inline XBRL Report related exceptions"""

    pass


class UnknownTaxonomyException(TaxonomyException):
    """Exception raised when a taxonomy entry point is unknown."""

    pass


class BrokenNamespacePrefixException(MIReportException):
    """Exception raised when a prefix is bound to more than one namespace."""

    pass


class BrokenQNameException(MIReportException):
    """Exception raised when a QName is malformed."""

    pass


class AmbiguousComponentException(TaxonomyException):
    """Exception raised when a label or unqualified concept name is used to refer to a concept and it matches more than one concept (it is ambiguous).

    The matching concepts are available in candidates (empty when the raiser
    did not supply them)."""

    def __init__(self, *args: object, candidates: Iterable[Concept] = ()) -> None:
        super().__init__(*args)
        self.candidates: tuple[Concept, ...] = tuple(candidates)


class OpenPyXlRelatedException(MIReportException):
    """Exception raised when dealing with an issue in OpenPyXL"""

    pass


class EarlyAbortException(MIReportException):
    """Exception raised when a required field is missing in the report."""

    pass
