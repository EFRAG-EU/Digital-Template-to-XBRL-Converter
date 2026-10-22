"""Normalising a fragment of HTML, such as a Word text block, that becomes part
of a report rather than a document of its own.

A fragment gets the rules for single elements in :mod:`._hygiene` and none of
the assembly in :mod:`._document`: the report it joins has its own head, body
and ``ix:header``.
"""

from __future__ import annotations

from xml.sax.saxutils import escape

from lxml import etree

from mireport.html_to_xhtml._exceptions import XhtmlError
from mireport.html_to_xhtml._hygiene import (
    _addRequiredAttributes,
    _rejectUnknownElements,
    _stripInvalidAttributes,
    _stripScripting,
    _stripScripts,
    _translateExactEquivalents,
)
from mireport.html_to_xhtml._parse import _parseFragment
from mireport.html_to_xhtml._tree import _elements, _localName

# Valid XHTML Strict, but only as a document's own structure or in its head,
# so never inside a fragment that joins someone else's body.
_DOCUMENT_ELEMENTS = frozenset({"body", "head", "html", "link", "meta", "title"})


def _rejectDocumentElements(root: etree._Element) -> None:
    """:raises XhtmlError: naming every element that belongs to a document
    rather than a fragment."""
    found = {
        localName
        for element in _elements(root)
        if (localName := _localName(element)) in _DOCUMENT_ELEMENTS
    }
    if found:
        raise XhtmlError(
            "contains elements that belong to a whole document, not a part of "
            f"one: {', '.join(sorted(found))}."
        )


def _serialiseContent(wrapper: etree._Element) -> str:
    """@wrapper's content, without @wrapper, as XHTML that relies on the
    default namespace of the document it is placed in."""
    for element in _elements(wrapper):
        element.tag = _localName(element)
    etree.cleanup_namespaces(wrapper)
    return escape(wrapper.text or "") + "".join(
        etree.tostring(child, encoding="unicode") for child in wrapper
    )


def normaliseFragment(fragment: str) -> str:
    """Return @fragment as well-formed XHTML Strict, parsed once and serialised
    once, since it goes into the report's template as text.

    :raises XhtmlError: if @fragment cannot be parsed, or holds an element that
        can't be made valid.
    """
    if not fragment.strip():
        return ""
    wrapper = _parseFragment(fragment)
    try:
        _stripInvalidAttributes(wrapper)
        _stripScripts(wrapper)
        _stripScripting(wrapper)
        _translateExactEquivalents(wrapper)
        _addRequiredAttributes(wrapper)
        _rejectUnknownElements(wrapper)
        _rejectDocumentElements(wrapper)
        return _serialiseContent(wrapper)
    except (ValueError, etree.LxmlError) as e:
        raise XhtmlError(f"could not be turned into XHTML. {e}") from e
