"""Read an xBRL-JSON report into an InlineReport, so it can be rendered as Inline XBRL."""

from mireport.xbrljson_reader.document import (
    XbrlJsonDocument,
    XbrlJsonException,
    XbrlJsonFact,
)
from mireport.xbrljson_reader.processor import XbrlJsonProcessor

__all__ = [
    "XbrlJsonDocument",
    "XbrlJsonException",
    "XbrlJsonFact",
    "XbrlJsonProcessor",
]
