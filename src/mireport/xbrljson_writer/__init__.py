"""Write any Report as an xBRL-JSON document: toXbrlJson() gives the reader's own
XbrlJsonDocument, which toFile() writes."""

from mireport.xbrljson_writer.writer import XBRL_NS, XbrlJsonExtensions, toXbrlJson

__all__ = ["XBRL_NS", "XbrlJsonExtensions", "toXbrlJson"]
