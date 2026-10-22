"""Rules for single elements and attributes, whatever document they are in.

Each removes or repairs something XHTML Strict rejects, or script that it
allows but that must not travel in a report.
"""

from __future__ import annotations

from lxml import etree

from mireport.html_to_xhtml._exceptions import XhtmlError
from mireport.html_to_xhtml._tree import (
    IX_NAMESPACE,
    clarkName,
    descendants,
    elementsIn,
    localNameOf,
    removeKeepingTail,
    tagText,
)

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


def stripInvalidAttributes(root: etree._Element) -> None:
    """Remove attributes the schema does not define.

    This costs pdf2htmlEX's page-navigation script the data-page-no hooks it
    reads, and pdftohtml its page background colour. That is a fair price: an
    invalid member is rejected outright, whereas one that lost some
    styling or interactivity still renders.
    """
    for element in elementsIn(root):
        for name in list(element.attrib):
            if (
                name.startswith(_DATA_ATTRIBUTE_PREFIX)
                or name in _HTML5_ONLY_ATTRIBUTES
                or name in _PRESENTATIONAL_ATTRIBUTES
                or name == "lang"  # xml:lang is the XHTML spelling and is kept
            ):
                del element.attrib[name]


# Attributes XHTML Strict requires, with the value to give one that is missing:
# "type" on style, which HTML5 made optional, and "alt" on img, which mammoth
# leaves off an image that has no alt text in Word.
_REQUIRED_ATTRIBUTES = {
    "style": ("type", "text/css"),
    "img": ("alt", ""),
}


def addRequiredAttributes(root: etree._Element) -> None:
    for localName, (attribute, default) in _REQUIRED_ATTRIBUTES.items():
        for element in descendants(root, localName):
            if element.get(attribute) is None:
                element.set(attribute, default)


_LINE_THROUGH = "text-decoration:line-through"
_CHECKED_BOX = "☒"
_UNCHECKED_BOX = "☐"


def _replaceWithText(element: etree._Element, text: str) -> None:
    element.tail = text + (element.tail or "")
    removeKeepingTail(element)


def translateExactEquivalents(root: etree._Element) -> None:
    """Rewrite the few elements outside XHTML Strict, or unwanted in a report,
    that have an exact equivalent, so :func:`rejectUnknownElements` need not
    refuse them.

    mammoth writes ``<s>`` (Transitional, like ``<strike>``) for struck-through
    text, which becomes a span that looks the same; ``<del>`` would look the
    same too, but would claim the text was deleted. It writes a Word checkbox as
    a disabled ``<input type="checkbox">``, which becomes the box character it
    shows.
    """
    for localName in ("s", "strike"):
        for element in descendants(root, localName):
            element.tag = clarkName("span")
            style = element.get("style")
            element.set("style", f"{_LINE_THROUGH};{style}" if style else _LINE_THROUGH)
    for element in descendants(root, "input"):
        if element.get("type") == "checkbox":
            box = _UNCHECKED_BOX if element.get("checked") is None else _CHECKED_BOX
            _replaceWithText(element, box)


def stripScripts(root: etree._Element) -> None:
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
    for script in descendants(root, "script"):
        removeKeepingTail(script)


# An event handler attribute and a javascript: URL are both valid XHTML Strict,
# so neither stripInvalidAttributes (schema errors) nor rejectUnknownElements
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


def stripScripting(root: etree._Element) -> None:
    """Remove the script that is not a <script> element.

    Preventive, unlike everything else here: neither converter emits a handler
    or a javascript: URL today. It is here because the price of being wrong
    about that is script running same-origin in the web app, and because
    dropping an attribute costs a document nothing it needs — an ``<a>`` without
    its href still renders its text.
    """
    for element in elementsIn(root):
        for name, value in list(element.attrib.items()):
            if name.startswith(_EVENT_HANDLER_PREFIX) or (
                name in _URI_ATTRIBUTES and _isScriptingUri(value)
            ):
                del element.attrib[name]


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


def rejectUnknownElements(root: etree._Element) -> None:
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
    we actually use emit. The cost is bounded —
    :mod:`mireport.pdf_converter._batch` reports a failed conversion as a
    warning and ships the PDF unconverted — whereas the member would be
    rejected anyway, from inside a zip, as one warning among hundreds.

    The same reasoning applies to :data:`_UNWANTED_ELEMENTS`, which the schema
    does define, so they are refused here too and named separately. The
    exceptions are the few with an exact equivalent, which
    :func:`translateExactEquivalents` has already rewritten.

    :raises XhtmlError: naming every element that is not XHTML Strict, or is
        Strict but has no place in a report.
    """
    unknown = {
        localName
        for element in elementsIn(root)
        if not tagText(element).startswith(f"{{{IX_NAMESPACE}}}")
        and (localName := localNameOf(element)) not in _XHTML_STRICT_ELEMENTS
    }
    if unknown:
        raise XhtmlError(
            "contains elements XHTML Strict does not define: "
            f"{', '.join(sorted(unknown))}."
        )
    unwanted = {
        localName
        for element in elementsIn(root)
        if (localName := localNameOf(element)) in _UNWANTED_ELEMENTS
    }
    if unwanted:
        raise XhtmlError(
            f"contains elements a report has no use for: {', '.join(sorted(unwanted))}."
        )
