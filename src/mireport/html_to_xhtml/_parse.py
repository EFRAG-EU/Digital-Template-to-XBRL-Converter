"""Getting a tree in the XHTML namespace out of whatever a tool emitted."""

from __future__ import annotations

import codecs

from lxml import etree, html

from mireport.html_to_xhtml._exceptions import XhtmlError
from mireport.html_to_xhtml._tree import (
    IX_NAMESPACE,
    XHTML_NAMESPACE,
    XML_NAMESPACE,
    clarkName,
    elementsIn,
    tagText,
)

# Byte order marks of the wide encodings, in which NUL bytes are ordinary text.
_WIDE_ENCODING_BOMS = (
    codecs.BOM_UTF32_LE,
    codecs.BOM_UTF32_BE,
    codecs.BOM_UTF16_LE,
    codecs.BOM_UTF16_BE,
)


def _isBinary(content: bytes) -> bool:
    """Whether @content is binary rather than text: it contains a NUL byte and
    does not announce itself as UTF-16 or UTF-32 with a byte order mark.

    A NUL is not a legal character in XML or HTML, but what an HTML parser does
    with one depends on the libxml2 it was built against: the one bundled with
    lxml on Windows refuses the document, while the HTML5-style parser in
    libxml2 2.14 (the Linux wheels) quietly turns it into U+FFFD and carries on,
    so garbage came out as a "recovered" document. Refusing it here makes the
    outcome the same everywhere.
    """
    return b"\x00" in content and not content.startswith(_WIDE_ENCODING_BOMS)


def _parseAsXhtml(content: bytes) -> etree._Element | None:
    """Return the root if @content is already well-formed XHTML, else None.

    Worth trying first because the HTML parser has no notion of namespaces: fed
    XHTML, it would treat ``xmlns:ix`` as an ordinary attribute and ``ix:header``
    as an element literally named "ix:header". Anything that is already valid
    XHTML — a future converter's output, or our own, re-run — is therefore
    better handled here than recovered there.
    """
    try:
        root = etree.fromstring(content, etree.XMLParser(resolve_entities=False))
    except etree.XMLSyntaxError:
        return None
    return root if root.tag == clarkName("html") else None


def _parseAsHtml(content: bytes) -> etree._Element:
    """Recover a document from @content however malformed it is.

    Lenient by design: an XML parser rejects a raw ``<`` in script content,
    HTML5's ``<meta charset>`` leaves a childless element the schema forbids,
    and a converter is free to leave a void element unclosed. All of that has to
    become a tree before any of it can be repaired.

    :raises XhtmlError: if there is no document here at all.
    """
    try:
        return html.document_fromstring(content)
    except (etree.ParserError, etree.XMLSyntaxError, ValueError) as e:
        raise XhtmlError(f"could not be parsed as HTML. {e}") from e


def _copyAttribute(target: etree._Element, name: str, value: str) -> None:
    """Copy one attribute, translating or dropping what lxml will not accept.

    The HTML parser has no notion of namespaces, so an XHTML input's ``xml:lang``
    and ``xmlns:ix`` arrive as attributes literally named that — and passing
    either to ``set()`` raises ValueError. pdftohtml emits ``xml:lang`` on every
    document, so this is the ordinary case rather than a defensive one, and it
    must never surface as anything but an XhtmlError.
    """
    if name.startswith("{"):
        # Already resolved to Clark notation, so already correct. Checked first
        # because such a name contains a colon (in its namespace URI) and would
        # otherwise be mistaken for a prefixed one and thrown away.
        target.set(name, value)
        return
    if name.startswith("xmlns"):
        # Namespaces are rebuilt from scratch below; a stale declaration copied
        # as an attribute would be meaningless at best.
        return
    if ":" in name:
        prefix, _, local = name.partition(":")
        if prefix == "xml":
            target.set(clarkName(local, XML_NAMESPACE), value)
        # Any other prefix belongs to a namespace we have not carried over, so
        # there is nothing sound to map it onto.
        return
    target.set(name, value)


def _fixPrefixedAttributes(element: etree._Element) -> None:
    """Rewrite in place the prefixed attribute names the HTML parser invented."""
    for name in list(element.attrib):
        if name.startswith("{"):
            continue  # already namespaced, nothing to repair
        if ":" not in name and not name.startswith("xmlns"):
            continue
        value = element.attrib[name]
        del element.attrib[name]
        _copyAttribute(element, name, value)


def _namespaceElements(root: etree._Element) -> None:
    """Put every unnamespaced element under @root in the XHTML namespace, and
    repair the attribute names the HTML parser invented, in place."""
    for element in elementsIn(root):
        tag = tagText(element)
        if not tag.startswith("{"):
            element.tag = clarkName(tag)
        _fixPrefixedAttributes(element)


def _moveIntoXhtmlNamespace(root: etree._Element) -> etree._Element:
    """Put every element in the XHTML namespace and return a fresh root.

    lxml's HTML parser produces unnamespaced tags, and an element's nsmap cannot
    be changed after construction — hence building a new root with the prefixes
    we want and moving the parsed content into it, rather than editing in place.
    """
    _namespaceElements(root)
    replacement = etree.Element(
        clarkName("html"),
        nsmap={None: XHTML_NAMESPACE, "ix": IX_NAMESPACE},
    )
    for name, value in root.attrib.items():
        _copyAttribute(replacement, name, value)
    replacement.extend(list(root))
    return replacement


def parseDocument(content: bytes) -> etree._Element:
    """Parse @content, whatever a tool emitted, into a tree in the XHTML
    namespace, ready for :func:`mireport.html_to_xhtml.normaliseDocument`.

    The one place a document is parsed: everything after this works on, and
    hands along, the tree.

    :raises XhtmlError: if @content is empty or binary (it contains NUL bytes
        and is not UTF-16/32 text), or cannot be parsed as HTML at all — each
        means the tool produced something other than a document, whatever its
        exit code claimed.
    """
    if not content.strip():
        raise XhtmlError("was empty.")
    if _isBinary(content):
        raise XhtmlError("is binary (it contains NUL bytes), not HTML.")
    try:
        if (root := _parseAsXhtml(content)) is None:
            root = _moveIntoXhtmlNamespace(_parseAsHtml(content))
    except (ValueError, etree.LxmlError) as e:
        raise XhtmlError(f"could not be turned into XHTML. {e}") from e
    return root


def parseFragment(fragment: str) -> etree._Element:
    """Parse @fragment, leniently, into a ``div`` in the XHTML namespace that
    holds it.

    :raises XhtmlError: if @fragment cannot be parsed as HTML at all.
    """
    try:
        wrapper = html.fragment_fromstring(fragment, create_parent="div")
    except (etree.ParserError, etree.XMLSyntaxError, ValueError) as e:
        raise XhtmlError(f"could not be parsed as HTML. {e}") from e
    _namespaceElements(wrapper)
    return wrapper
