class MIReportException(Exception):
    """Base class for any XBRL related exceptions. Not expected to be raised directly."""



class UnitException(MIReportException):
    """Exception raised when a unit is not found in the UTR."""



class TaxonomyException(MIReportException):
    """All taxonomy related exceptions"""



class InlineReportException(MIReportException):
    """All Inline XBRL Report related exceptions"""



class UnknownTaxonomyException(TaxonomyException):
    """Exception raised when a taxonomy entry point is unknown."""



class BrokenNamespacePrefixException(MIReportException):
    """Exception raised when a prefix is bound to more than one namespace."""



class BrokenQNameException(MIReportException):
    """Exception raised when a QName is malformed."""



class AmbiguousComponentException(TaxonomyException):
    """Exception raised when a label or unqualified concept name is used to refer to a concept and it matches more than one concept (it is ambiguous)."""



class OpenPyXlRelatedException(MIReportException):
    """Exception raised when dealing with an issue in OpenPyXL"""



class EarlyAbortException(MIReportException):
    """Exception raised when a required field is missing in the report."""

