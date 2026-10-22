"""Assembling a whole XHTML document: the head, the body and its ``ix:header``.

Everything here is about a document as a document; the rules for single
elements are in :mod:`._hygiene` and run here too.
"""

from __future__ import annotations

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
from mireport.html_to_xhtml._tree import (
    IX_NAMESPACE,
    _descendants,
    _elements,
    _remove,
    _tag,
    _tagText,
)

_HIDDEN_STYLE = "display:none"
_DEFAULT_TITLE = "Supplementary document"

# Anchors: XHTML Strict has no "name" attribute on <a>, so pdftohtml's page
# anchors are converted to ids. A raw page number is not a valid id either — an
# NCName cannot start with a digit — so both anchors and the links that target
# them are prefixed.
_ANCHOR_ID_PREFIX = "pdfpage-"


def _convertAnchorNames(root: etree._Element) -> None:
    """Turn ``<a name="1">`` into ``<a id="pdfpage-1">``, links included."""
    anchors = _descendants(root, "a")
    renamed = set()
    for anchor in anchors:
        if (name := anchor.attrib.pop("name", None)) is None:
            continue
        if anchor.get("id") is None:
            anchor.set("id", f"{_ANCHOR_ID_PREFIX}{name}")
            renamed.add(name)
    if not renamed:
        return
    for anchor in anchors:
        href = anchor.get("href", "")
        if href.startswith("#") and href[1:] in renamed:
            anchor.set("href", f"#{_ANCHOR_ID_PREFIX}{href[1:]}")


def _inlineAnchorContent(root: etree._Element) -> None:
    """Turn a link's ``div`` hit area into a ``span``.

    XHTML Strict lets ``<a>`` hold inline content only, and pdf2htmlEX renders
    every PDF link annotation as an empty, absolutely positioned ``div`` inside
    one. Renaming it changes nothing on screen — absolute positioning computes
    to ``display:block`` whatever the element is, and pdf2htmlEX's rules for
    these boxes are class-based — while keeping the link clickable, which
    dropping the anchor would not.
    """
    for anchor in _descendants(root, "a"):
        for div in anchor.iter(_tag("div")):
            div.tag = _tag("span")


def _ensureHead(root: etree._Element) -> etree._Element:
    if (head := root.find(_tag("head"))) is None:
        head = etree.Element(_tag("head"))
        root.insert(0, head)
    return head


def _hoistStyles(root: etree._Element) -> None:
    """Move <style> elements out of the body and into the head.

    XHTML Strict allows style only in the head, but pdftohtml emits one per page
    inline in the body. Moving rather than dropping keeps the page layout.
    """
    if (body := root.find(_tag("body"))) is None:
        return
    if not (styles := _descendants(body, "style")):
        return
    head = _ensureHead(root)
    for style in styles:
        _remove(style)
        style.tail = None
        head.append(style)


def _wrapBodyContent(root: etree._Element) -> None:
    """Ensure <body> contains only block-level content, by wrapping it all.

    XHTML Strict requires body's children to be block-level, and pdftohtml puts
    bare ``<a>`` page anchors directly in the body — "This element is not
    expected". A single wrapping ``<div>`` accepts both block and inline content
    and so makes any converter's body valid without having to classify what is
    in it.

    A body of nothing but divs is already valid and is left alone, so that
    normalising our own output is a fixed point rather than nesting one more
    wrapper on every pass.
    """
    body = root.find(_tag("body"))
    if body is None or len(body) == 0:
        return
    if not (body.text or "").strip() and all(
        child.tag == _tag("div") for child in body
    ):
        return
    wrapper = etree.Element(_tag("div"), {"class": "pdf-annex"})
    wrapper.text = body.text
    body.text = None
    for child in list(body):
        body.remove(child)
        wrapper.append(child)
    body.append(wrapper)


def _dropUnusableMeta(root: etree._Element) -> None:
    """Remove <meta> elements the schema cannot accept.

    ``<meta charset="utf-8">`` is HTML5-only; stripping the attribute leaves a
    bare ``<meta/>``, and the schema requires ``content`` on every meta. Nothing
    is lost by dropping it — the encoding is carried by the XML declaration.
    """
    for meta in _descendants(root, "meta"):
        if meta.get("content") is None:
            _remove(meta)


def _repairHead(root: etree._Element) -> None:
    """Ensure a <head> exists and has at least one child the schema permits.

    The schema requires one of script/style/meta/link/object/title/base. Once
    <meta charset> has been stripped, a pdf2htmlEX head can be left empty.
    """
    head = _ensureHead(root)
    if head.find(_tag("title")) is None:
        title = etree.Element(_tag("title"))
        title.text = _DEFAULT_TITLE
        head.insert(0, title)


def _hasIxContent(root: etree._Element) -> bool:
    ixPrefix = f"{{{IX_NAMESPACE}}}"
    return any(_tagText(element).startswith(ixPrefix) for element in _elements(root))


def _addHiddenIxHeader(root: etree._Element) -> None:
    """Prepend the minimum that earns document set membership."""
    if (body := root.find(_tag("body"))) is None:
        body = etree.SubElement(root, _tag("body"))
    hidden = etree.Element(_tag("div"), style=_HIDDEN_STYLE)
    header = etree.SubElement(hidden, _tag("header", IX_NAMESPACE))
    etree.SubElement(header, _tag("resources", IX_NAMESPACE))
    body.insert(0, hidden)


def normaliseDocument(
    root: etree._Element, *, injectIxHeader: bool = True
) -> etree._Element:
    """Make @root, from :func:`mireport.html_to_xhtml.parseDocument`, valid
    XHTML Strict, optionally carrying an empty ``ix:header``. Works in place
    and returns @root.

    :param injectIxHeader: whether to inject the empty, hidden ``ix:header``
        that earns a standalone document set member its Inline XBRL identity
        (see the package docstring). Pass False when the result is destined to
        be merged into another document rather than shipped as its own
        document set member — the merged document needs exactly one header,
        supplied by the report it joins, not one per source PDF.
    :raises XhtmlError: if @root holds an element that can't be made valid.
    """
    # Rewriting a foreign document into XHTML is exactly the kind of work that
    # turns an unexpected input into a raw lxml ValueError, and a converted PDF
    # failing must always be a warning rather than an exception reaching a
    # request. Anything unforeseen here is still a conversion failure.
    try:
        _stripInvalidAttributes(root)
        _convertAnchorNames(root)
        _inlineAnchorContent(root)
        _dropUnusableMeta(root)
        _stripScripts(root)
        _stripScripting(root)
        _translateExactEquivalents(root)
        _addRequiredAttributes(root)
        _hoistStyles(root)
        _repairHead(root)
        # Wrap first, then insert the header: both end up block-level children
        # of body, which is what the schema wants.
        _wrapBodyContent(root)
        if injectIxHeader and not _hasIxContent(root):
            _addHiddenIxHeader(root)
        _rejectUnknownElements(root)
        etree.cleanup_namespaces(root)
    except (ValueError, etree.LxmlError) as e:
        raise XhtmlError(f"could not be turned into XHTML. {e}") from e
    return root


def documentToBytes(root: etree._Element) -> bytes:
    """@root serialised as a standalone XHTML file, for a report package."""
    return etree.tostring(
        root, xml_declaration=True, encoding="UTF-8", pretty_print=False
    )
