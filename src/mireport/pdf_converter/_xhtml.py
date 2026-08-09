"""Turn HTML5 into an Inline XBRL document set member.

Two things stand between the converter's output and a document that can join the
tagged report in an Inline XBRL Document Set:

1. **Well-formedness.** iXBRL 1.1 defines an Inline XBRL Document as a
   well-formed XML document (spec 3.1), and a Validating Conformant Processor
   accepts only documents rooted in ``{xhtml}html`` and valid against the
   XHTML + iXBRL schema (spec 3.3.1). Neither converter's output survives an XML
   parser. pdf2htmlEX is the closer of the two — it self-closes its void
   elements and declares the XHTML namespace already — but it serves HTML5,
   whose content model lets a raw ``<`` sit inside ``<script>``, and its own
   copyright banner does exactly that. So output is re-parsed leniently and
   re-serialised as XHTML.

2. **Inline XBRL identity.** The same definition requires an Inline XBRL
   Document to contain Inline XBRL Elements, so a member declaring ``xmlns:ix``
   and nothing else is not one, and a processor drops it from the set — Arelle
   says so with an ``arelle:nonIxdsDocument`` warning, and the member would then
   travel in the zip but never reach the viewer. So an empty ``ix:header`` is
   injected, inside a ``display:none`` div as spec 8.1.2 recommends.
   It declares no facts, no contexts, no units and no schema references: the
   annex contributes page content, and the tagged report remains the sole source
   of XBRL data. Only one ``ix:header`` need carry anything across the whole set
   (spec 8.1.3), and the tagged report supplies it.

Script is removed outright, and that means every form of it: the ``<script>``
element (:func:`_stripScripts`), event handler attributes and ``javascript:``
URLs (:func:`_stripScripting`). The web app serves document set members
same-origin, so script surviving in an annex built from an uploaded PDF would
run with the user's session.

Everything else here repairs a specific thing a real converter emits that the
XHTML *Strict* half of that schema rejects; none of it is precautionary, and
``tests/integrationTests/test_real_pdf_converter.py`` is what proves it. What is
*not* repaired is refused: an element outside XHTML Strict fails the conversion
rather than being translated on a guess — see :func:`_rejectUnknownElements`.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from lxml import etree, html

from mireport.pdf_converter._exceptions import PdfConversionError

if TYPE_CHECKING:
    from collections.abc import Iterator

XHTML_NAMESPACE = "http://www.w3.org/1999/xhtml"
IX_NAMESPACE = "http://www.xbrl.org/2013/inlineXBRL"
XML_NAMESPACE = "http://www.w3.org/XML/1998/namespace"


def _tag(localName: str, namespace: str = XHTML_NAMESPACE) -> str:
    return f"{{{namespace}}}{localName}"


def _descendants(root: etree._Element, localName: str) -> list[etree._Element]:
    return root.findall(f".//{_tag(localName)}")


def _elements(root: etree._Element) -> Iterator[etree._Element]:
    """Every element in @root, skipping comments and processing instructions."""
    return (e for e in root.iter() if isinstance(e.tag, str))


def _localName(element: etree._Element) -> str:
    """@element's name without its namespace.

    Split rather than etree.QName, which refuses to parse exactly the names
    worth reporting — the prefixed ones the HTML parser took literally.
    """
    return element.tag.rpartition("}")[2]


_HIDDEN_STYLE = "display:none"
_DEFAULT_TITLE = "Supplementary document"

# Attributes with no place in the strict XHTML schema every inline document is
# validated against. Each entry here was a real validation error from a
# real converter, not a precaution:
#
#   charset            pdf2htmlEX's HTML5 <meta charset>
#   data-*             pdf2htmlEX's data-page-no on every page div
#   lang               pdftohtml emits both lang and xml:lang; only the latter
#                      is allowed, and it is preserved
#   bgcolor/link/...   pdftohtml's presentational attributes on <body>, which
#                      are XHTML Transitional and not Strict
_HTML5_ONLY_ATTRIBUTES = frozenset({"charset"})
_PRESENTATIONAL_ATTRIBUTES = frozenset(
    {
        "alink",
        "background",
        "bgcolor",
        "link",
        "text",
        "vlink",
    }
)
_DATA_ATTRIBUTE_PREFIX = "data-"

# Anchors: XHTML Strict has no "name" attribute on <a>, so pdftohtml's page
# anchors are converted to ids. A raw page number is not a valid id either — an
# NCName cannot start with a digit — so both anchors and the links that target
# them are prefixed.
_ANCHOR_ID_PREFIX = "pdfpage-"


def _stripInvalidAttributes(root: etree._Element) -> None:
    """Remove attributes the schema does not define.

    This costs pdf2htmlEX's page-navigation script the data-page-no hooks it
    reads, and pdftohtml its page background colour. That is a fair price: an
    invalid member is rejected outright, whereas one that lost some
    styling or interactivity still renders.
    """
    for element in _elements(root):
        for name in list(element.attrib):
            if (
                name.startswith(_DATA_ATTRIBUTE_PREFIX)
                or name in _HTML5_ONLY_ATTRIBUTES
                or name in _PRESENTATIONAL_ATTRIBUTES
                or name == "lang"  # xml:lang is the XHTML spelling and is kept
            ):
                del element.attrib[name]


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


# XHTML Strict requires "type" on style; HTML5 made it optional.
_REQUIRED_TYPE_ATTRIBUTES = {
    "style": "text/css",
}


def _addRequiredTypeAttributes(root: etree._Element) -> None:
    for localName, defaultType in _REQUIRED_TYPE_ATTRIBUTES.items():
        for element in _descendants(root, localName):
            if element.get("type") is None:
                element.set("type", defaultType)


def _remove(element: etree._Element) -> None:
    """Detach @element, leaving the text that followed it where it was."""
    if (parent := element.getparent()) is None:
        return
    if tail := element.tail:
        if (previous := element.getprevious()) is None:
            parent.text = (parent.text or "") + tail
        else:
            previous.tail = (previous.tail or "") + tail
    parent.remove(element)


def _stripScripts(root: etree._Element) -> None:
    """Remove every <script> element.

    A document set member is part of a financial report that others will open;
    script the converter happened to bundle has no business travelling with it,
    and the iXBRL viewer supplies its own. pdf2htmlEX's script only drives
    optional page navigation, so the page still renders from its CSS-positioned
    content without it.

    Done here rather than with a converter flag because there isn't one that
    means this: pdf2htmlEX's --embed-javascript 0 makes the script *external*
    rather than absent, which is strictly worse for a file that ships alone.
    """
    for script in _descendants(root, "script"):
        _remove(script)


# An event handler attribute and a javascript: URL are both valid XHTML Strict,
# so neither _stripInvalidAttributes (schema errors) nor _rejectUnknownElements
# (elements) would ever catch them. Arelle's own detector enumerates the
# handlers as HTML_EVENT_HANDLER_ATTRIBUTES, as do the ESEF and ROS filing
# rules; XHTML Strict defines no other attribute beginning "on", so the prefix
# generalises that list and covers whatever HTML5 has added since.
_EVENT_HANDLER_PREFIX = "on"
_SCRIPTING_SCHEMES = ("javascript:", "vbscript:", "data:text/html")
# Every URI-valued attribute XHTML Strict defines.
_URI_ATTRIBUTES = frozenset(
    {
        "action",
        "cite",
        "classid",
        "codebase",
        "data",
        "href",
        "longdesc",
        "profile",
        "src",
        "usemap",
    }
)


def _isScriptingUri(value: str) -> bool:
    """Whether @value names a scheme that executes rather than fetches.

    Whitespace and NULs are removed throughout rather than trimmed, because a
    browser ignores them inside a URL: ``java\\tscript:`` runs.
    """
    collapsed = "".join(value.split()).replace("\x00", "").lower()
    return collapsed.startswith(_SCRIPTING_SCHEMES)


def _stripScripting(root: etree._Element) -> None:
    """Remove the script that is not a <script> element.

    Preventive, unlike everything else here: neither converter emits a handler
    or a javascript: URL today. It is here because the price of being wrong
    about that is script running same-origin in the web app, and because
    dropping an attribute costs a document nothing it needs — an ``<a>`` without
    its href still renders its text.
    """
    for element in _elements(root):
        for name, value in list(element.attrib.items()):
            if name.startswith(_EVENT_HANDLER_PREFIX) or (
                name in _URI_ATTRIBUTES and _isScriptingUri(value)
            ):
                del element.attrib[name]


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
    return any(element.tag.startswith(ixPrefix) for element in _elements(root))


# Every element XHTML 1.0 Strict defines — not what is welcome, but what the
# XHTML + iXBRL schema will accept.
_XHTML_STRICT_ELEMENTS = frozenset(
    (
        "a", "abbr", "acronym", "address", "area", "b", "base", "bdo", "big",
        "blockquote", "body", "br", "button", "caption", "cite", "code", "col",
        "colgroup", "dd", "del", "dfn", "div", "dl", "dt", "em", "fieldset",
        "form", "h1", "h2", "h3", "h4", "h5", "h6", "head", "hr", "html", "i",
        "img", "input", "ins", "kbd", "label", "legend", "li", "link", "map",
        "meta", "noscript", "object", "ol", "optgroup", "option", "p", "param",
        "pre", "q", "samp", "script", "select", "small", "span", "strong",
        "style", "sub", "sup", "table", "tbody", "td", "textarea", "tfoot",
        "th", "thead", "title", "tr", "tt", "ul", "var",
    )
)  # fmt: skip

# Valid XHTML Strict, but nothing a converted PDF has any use for: <object> and
# <param> embed something the browser runs, a form submits somewhere, and <base>
# silently re-points every relative URL in the document. Refused rather than
# stripped because neither converter emits any of them, so one appearing means
# the input is not what this module was built for.
_UNWANTED_ELEMENTS = frozenset(
    {
        "base",
        "button",
        "form",
        "input",
        "object",
        "param",
        "select",
        "textarea",
    }
)


def _rejectUnknownElements(root: etree._Element) -> None:
    """Refuse to emit a document containing an element we do not recognise.

    Three things land here: HTML5 elements neither supported converter emits
    today, other vocabularies the HTML parser handed over unnamespaced (an
    inline ``<svg>``), and prefixed names it took literally — ``<o:p>`` becomes
    an element named that *inside* the XHTML namespace, and would serialise
    against a declaration that no longer exists.

    Refused rather than translated because the mapping is a guess: nothing here
    knows whether an unknown element was block or inline, and getting it wrong
    produces a plausible-looking annex that is subtly wrong. Refused rather than
    dropped because dropping is silent, and the point is to learn what the tools
    we actually use emit. The cost is bounded — :mod:`._batch` reports a failed
    conversion as a warning and ships the PDF unconverted — whereas the member
    would be rejected anyway, from inside a zip, as one warning among hundreds.

    The same reasoning applies to :data:`_UNWANTED_ELEMENTS`, which the schema
    does define, so they are refused here too and named separately.

    :raises PdfConversionError: naming every element that is not XHTML Strict,
        or is Strict but has no place in an annex.
    """
    unknown = {
        localName
        for element in _elements(root)
        if not element.tag.startswith(f"{{{IX_NAMESPACE}}}")
        and (localName := _localName(element)) not in _XHTML_STRICT_ELEMENTS
    }
    if unknown:
        raise PdfConversionError(
            "Converted PDF output contains elements XHTML Strict does not "
            f"define: {', '.join(sorted(unknown))}."
        )
    unwanted = {
        localName
        for element in _elements(root)
        if (localName := _localName(element)) in _UNWANTED_ELEMENTS
    }
    if unwanted:
        raise PdfConversionError(
            "Converted PDF output contains elements a supplementary document "
            f"has no use for: {', '.join(sorted(unwanted))}."
        )


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
    return root if root.tag == _tag("html") else None


def _parseAsHtml(content: bytes) -> etree._Element:
    """Recover a document from @content however malformed it is.

    Lenient by design: an XML parser rejects a raw ``<`` in script content,
    HTML5's ``<meta charset>`` leaves a childless element the schema forbids,
    and a converter is free to leave a void element unclosed. All of that has to
    become a tree before any of it can be repaired.

    :raises PdfConversionError: if there is no document here at all.
    """
    try:
        return html.document_fromstring(content)
    except (etree.ParserError, etree.XMLSyntaxError, ValueError) as e:
        raise PdfConversionError(
            f"Converted PDF output could not be parsed as HTML. {e}"
        ) from e


def _copyAttribute(target: etree._Element, name: str, value: str) -> None:
    """Copy one attribute, translating or dropping what lxml will not accept.

    The HTML parser has no notion of namespaces, so an XHTML input's ``xml:lang``
    and ``xmlns:ix`` arrive as attributes literally named that — and passing
    either to ``set()`` raises ValueError. pdftohtml emits ``xml:lang`` on every
    document, so this is the ordinary case rather than a defensive one, and it
    must never surface as anything but a PdfConversionError.
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
            target.set(_tag(local, XML_NAMESPACE), value)
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


def _moveIntoXhtmlNamespace(root: etree._Element) -> etree._Element:
    """Put every element in the XHTML namespace and return a fresh root.

    lxml's HTML parser produces unnamespaced tags, and an element's nsmap cannot
    be changed after construction — hence building a new root with the prefixes
    we want and moving the parsed content into it, rather than editing in place.
    """
    for element in _elements(root):
        if not element.tag.startswith("{"):
            element.tag = _tag(element.tag)
        _fixPrefixedAttributes(element)

    replacement = etree.Element(
        _tag("html"),
        nsmap={None: XHTML_NAMESPACE, "ix": IX_NAMESPACE},
    )
    for name, value in root.attrib.items():
        _copyAttribute(replacement, name, value)
    replacement.extend(list(root))
    return replacement


def _addHiddenIxHeader(root: etree._Element) -> None:
    """Prepend the minimum that earns document set membership."""
    if (body := root.find(_tag("body"))) is None:
        body = etree.SubElement(root, _tag("body"))
    hidden = etree.Element(_tag("div"), style=_HIDDEN_STYLE)
    header = etree.SubElement(hidden, _tag("header", IX_NAMESPACE))
    etree.SubElement(header, _tag("resources", IX_NAMESPACE))
    body.insert(0, hidden)


def normaliseToXhtml(content: bytes) -> bytes:
    """Return @content as well-formed XHTML carrying an empty ``ix:header``.

    :raises PdfConversionError: if @content cannot be parsed as HTML at all, or
        contains no ``html`` element — either means the converter produced
        something other than a document, whatever its exit code claimed.
    """
    if not content.strip():
        raise PdfConversionError("Converted PDF output was empty.")

    # Rewriting a foreign document into XHTML is exactly the kind of work that
    # turns an unexpected input into a raw lxml ValueError, and a converted PDF
    # failing must always be a warning rather than an exception reaching a
    # request. Anything unforeseen here is still a conversion failure.
    try:
        if (root := _parseAsXhtml(content)) is None:
            root = _moveIntoXhtmlNamespace(_parseAsHtml(content))
        _stripInvalidAttributes(root)
        _convertAnchorNames(root)
        _dropUnusableMeta(root)
        _stripScripts(root)
        _stripScripting(root)
        _addRequiredTypeAttributes(root)
        _hoistStyles(root)
        _repairHead(root)
        # Wrap first, then insert the header: both end up block-level children
        # of body, which is what the schema wants.
        _wrapBodyContent(root)
        if not _hasIxContent(root):
            _addHiddenIxHeader(root)
        _rejectUnknownElements(root)
        etree.cleanup_namespaces(root)
        return etree.tostring(
            root, xml_declaration=True, encoding="UTF-8", pretty_print=False
        )
    except (ValueError, etree.LxmlError) as e:
        raise PdfConversionError(
            f"Converted PDF output could not be turned into XHTML. {e}"
        ) from e
