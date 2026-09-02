"""Merging a converted, normalised PDF into another Inline XBRL document.

An alternative to document set membership (:mod:`._convert`,
:mod:`mireport.report.reportpackage`): rather than travel as its own document
next to the tagged report, a merged annex becomes a subtree of the report's own
document — appended to the back, one per uploaded PDF, each with its own table
of contents entry.

Two things a standalone document set member gets for free break once several
documents become one: id uniqueness (an `id` is a document-wide XML constraint,
not something the merge can scope away) and CSS isolation (a stylesheet that
was written assuming it owns the whole page now shares a cascade with the
report's own). Both are handled here, once per annex, so that:

- every id the annex defines, and every same-document reference to one
  (``href="#..."``, ``for``, ``aria-describedby`` and friends), is renamed
  unique to this annex (:func:`_renameIds`);
- the annex's own CSS can never affect anything outside its container, via
  ``@scope`` (:func:`_wrapCss`).

Isolation the other way round — keeping the *report's* own CSS from reaching
into a merged annex — is handled here too, rather than by touching the
report's stylesheet at all: an unlayered, id-prefixed reset is emitted ahead of
the annex's ``@scope``-wrapped rules in the same ``<style>``. This deliberately
does not use a ``@layer`` for the annex CSS: unlayered author declarations
always beat *every* layered declaration regardless of specificity, so putting
the annex's own rules in a layer would have made the report's unlayered rules
win inside the annex — backwards from what is wanted. An unlayered, id-prefixed
reset instead outranks the report's `body.theme-dark a:link`-style rules on
specificity alone, and (being unlayered, same as everything else here) simply
comes later in source order than the report's own ``<style>``.

Known gap: nested rules (``@media``, ``@supports``) are not descended into when
renaming id selectors, and ``@font-face``/``@keyframes`` names are not renamed
between annexes. Neither supported converter emits any of these today; if one
starts to, :func:`_renameIdSelectors` is the place to extend.

:func:`mergeAnnexesIntoReport` is the other half of this module: it takes the
finished, already-tagged report (the output of
:meth:`mireport.report.inlinereport.InlineReport.getInlineReport`) and splices
one or more :class:`MergedAnnex` into it, entirely with lxml, after the fact.
The report generator itself carries no knowledge that supplementary PDFs
exist — nothing here reaches back into ``inlinereport.py`` or its templates.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, NamedTuple

import tinycss2
from lxml import etree
from tinycss2.ast import HashToken, QualifiedRule

from mireport.filesupport import FilelikeAndFileName
from mireport.pdf_converter._exceptions import PdfConversionError

if TYPE_CHECKING:
    from collections.abc import Sequence

XHTML_NAMESPACE = "http://www.w3.org/1999/xhtml"

# The same class name mireport.pdf_converter._xhtml._wrapBodyContent already
# gives its own wrapper: kept identical so a merged annex is recognisable
# (and, e.g., selectable by a future print stylesheet) whichever code wrapped
# it.
ANNEX_CLASS = "pdf-annex"

# Attributes whose value (or, for the first, only when it starts with "#") is
# a same-document id reference that must follow an id it points to when that
# id is renamed. Not exhaustive of every ARIA attribute, but every one a PDF
# converter has a reason to emit (page anchors, exactly what pdftohtml links
# between pages with).
_ID_REF_ATTRIBUTES = (
    "for",
    "aria-describedby",
    "aria-labelledby",
    "aria-owns",
    "aria-controls",
    "aria-activedescendant",
)


def _tag(localName: str, namespace: str = XHTML_NAMESPACE) -> str:
    return f"{{{namespace}}}{localName}"


class MergedAnnex(NamedTuple):
    """One converted PDF, ready to be spliced into another report's HTML.

    :param anchor_id: unique id of the wrapping container, used both as the
        CSS ``@scope`` root and as the in-document anchor a table of contents
        entry links to.
    :param label: the original PDF's filename, for display in that entry.
    :param body_html: the annex's content as a single well-formed XHTML
        fragment (one root element, carrying @anchor_id as its id), ready to
        replace a placeholder element of the same id.
    :param head_css: the annex's own CSS, scoped to @anchor_id with ``@scope``
        and preceded by an unlayered reset that keeps the report's own styling
        from reaching inside, ready to be inserted as-is inside a ``<style>``
        element in the merged document's ``<head>``.
    """

    anchor_id: str
    label: str
    body_html: str
    head_css: str


def buildMergedAnnex(xhtml: bytes, *, index: int, label: str) -> MergedAnnex:
    """Turn one PDF's normalised XHTML into a :class:`MergedAnnex`.

    @xhtml must already be well-formed XHTML with no ``ix:header`` — i.e. the
    output of :func:`mireport.pdf_converter._xhtml.normaliseToXhtml` called
    with ``injectIxHeader=False``. A merged document needs exactly one header,
    supplied by the report the annex joins, not one per source PDF.

    :param index: distinguishes this annex from any others being merged into
        the same report. Used to derive @anchor_id (``pdf-annex-{index}``) and
        the CSS layer name, so that several annexes' ids and layers cannot
        collide with each other.
    :raises PdfConversionError: if @xhtml is not well-formed XML.
    """
    anchorId = f"pdf-annex-{index}"
    try:
        root = etree.fromstring(xhtml, etree.XMLParser(resolve_entities=False))
    except etree.XMLSyntaxError as e:
        raise PdfConversionError(
            f"Converted PDF output could not be parsed for merging. {e}"
        ) from e

    idMap = _renameIds(root, prefix=f"{anchorId}-")
    cssTexts = _extractStyles(root)
    headCss = _wrapCss(cssTexts, anchorId=anchorId, idMap=idMap)
    bodyHtml = _extractBodyFragment(root, anchorId=anchorId)
    return MergedAnnex(
        anchor_id=anchorId, label=label, body_html=bodyHtml, head_css=headCss
    )


def _renameIds(root: etree._Element, *, prefix: str) -> dict[str, str]:
    """Rename every ``id`` in @root to ``{prefix}{original id}``, rewriting
    same-document references (``href="#..."`` and :data:`_ID_REF_ATTRIBUTES`)
    to match. Returns the old-to-new mapping, for :func:`_wrapCss` to apply the
    same renaming to any ``#id`` CSS selector.
    """
    idMap: dict[str, str] = {}
    for element in root.iter():
        if not isinstance(element.tag, str):
            continue
        oldId = element.get("id")
        if oldId is not None and oldId not in idMap:
            idMap[oldId] = f"{prefix}{oldId}"
    if not idMap:
        return idMap

    for element in root.iter():
        if not isinstance(element.tag, str):
            continue
        if (oldId := element.get("id")) is not None:
            element.set("id", idMap[oldId])
        if (
            (href := element.get("href")) is not None
            and href.startswith("#")
            and (renamed := idMap.get(href[1:])) is not None
        ):
            element.set("href", f"#{renamed}")
        for attribute in _ID_REF_ATTRIBUTES:
            if (value := element.get(attribute)) is None:
                continue
            rewritten = " ".join(idMap.get(token, token) for token in value.split())
            if rewritten != value:
                element.set(attribute, rewritten)
    return idMap


def _extractStyles(root: etree._Element) -> list[str]:
    """Detach and return the text of every ``<style>`` in @root's ``<head>``.

    They belong scoped and layered in :func:`_wrapCss`'s output, not verbatim
    in the merged document's head, so they are removed here rather than left
    for the caller to skip over.
    """
    head = root.find(_tag("head"))
    if head is None:
        return []
    texts = []
    for style in list(head.findall(_tag("style"))):
        if style.text:
            texts.append(style.text)
        head.remove(style)
    return texts


def _renameIdSelectors(cssText: str, idMap: dict[str, str]) -> str:
    """Rewrite any ``#id`` selector in @cssText to the id it was renamed to.

    Declaration values (e.g. ``color: #fff``) are untouched: a ``HashToken``
    only means an id selector when it appears in a rule's prelude, not its
    declaration block. Nested rules (``@media``, ``@supports``) are not
    descended into — see the module docstring.
    """
    if not idMap:
        return cssText
    rules = tinycss2.parse_stylesheet(
        cssText, skip_comments=False, skip_whitespace=False
    )
    changed = False
    for rule in rules:
        if not isinstance(rule, QualifiedRule):
            continue
        for token in rule.prelude:
            if isinstance(token, HashToken) and token.value in idMap:
                token.value = idMap[token.value]
                changed = True
    return tinycss2.serialize(rules) if changed else cssText


def _wrapCss(cssTexts: Sequence[str], *, anchorId: str, idMap: dict[str, str]) -> str:
    """Combine @cssTexts into one stylesheet, isolated to @anchorId.

    Isolation is two-directional but asymmetric in mechanism. Annex CSS is
    kept from reaching outside @anchorId's subtree with ``@scope``. The
    report's own CSS is kept from reaching in with an unlayered, id-prefixed
    reset placed *before* the scoped rules: ``#{anchorId} { all: revert; }``
    stops the report's typography inheriting past the wrapper, and an
    id-prefixed reset on every link state stops the report's ``a:link`` /
    ``a:hover`` / ``a:visited`` rules (including its dark-theme variants)
    matching inside — those are the only report rules with any real chance of
    matching annex markup at all, since everything else the report defines is
    scoped to classes (``.page``, ``.fact``, ``.toc-*``, ...) an annex never
    has. See the module docstring for why this is not a ``@layer``.
    """
    body = "\n".join(
        _renameIdSelectors(text, idMap) for text in cssTexts if text.strip()
    )
    reset = (
        f"#{anchorId} {{ all: revert; }}\n"
        f"#{anchorId} a, #{anchorId} a:link, #{anchorId} a:visited, "
        f"#{anchorId} a:hover {{ all: revert; }}\n"
    )
    return f"{reset}@scope (#{anchorId}) {{\n{body}\n}}\n"


def _extractBodyFragment(root: etree._Element, *, anchorId: str) -> str:
    """Return @root's ``<body>`` content as one wrapped, serialised fragment."""
    wrapper = etree.Element(
        _tag("div"),
        {"id": anchorId, "class": ANNEX_CLASS},
        nsmap={None: XHTML_NAMESPACE},
    )
    if (body := root.find(_tag("body"))) is not None:
        wrapper.text = body.text
        for child in list(body):
            wrapper.append(child)
    etree.cleanup_namespaces(wrapper)
    return etree.tostring(wrapper, encoding="unicode")


def mergeAnnexesIntoReport(
    report: FilelikeAndFileName,
    annexes: Sequence[MergedAnnex],
    *,
    tocLabel: str = "Annexes",
) -> FilelikeAndFileName:
    """Splice @annexes into the back of @report.

    @report is the finished output of
    :meth:`mireport.report.inlinereport.InlineReport.getInlineReport` — this
    works entirely on its already-tagged bytes, reparsed with lxml, so that
    the report generator itself never needs to know supplementary PDFs exist.
    For each annex, in order: its ``<style>`` (:attr:`MergedAnnex.head_css`,
    already isolated both ways — see :func:`_wrapCss`) is appended to
    ``<head>``; its content (:attr:`MergedAnnex.body_html`) is appended to
    ``<body>``; and one entry linking to it is appended to the report's own
    table of contents, grouped under @tocLabel exactly like every other
    heading group there (see
    ``inline-report-presentation.html.jinja``'s table-of-contents markup) —
    except @tocLabel is a hardcoded English string, since there is no
    taxonomy-derived label to draw one from for annex content.

    Returns @report unchanged if @annexes is empty, rather than round-tripping
    it through lxml for nothing: that round-trip is not byte-identical to the
    original (lxml collapses the newline-indented ``xmlns:`` declarations on
    the ``<html>`` start tag onto one line, and reorders them ahead of other
    attributes). Arelle validates the result fine either way, but there is no
    reason to pay for, or risk, a cosmetic difference when there is nothing to
    merge.

    :raises PdfConversionError: if @report cannot be reparsed, or is missing
        the ``<head>``, ``<body>``, or ``<ol class="toc-list">`` this expects
        — a caller/report mismatch, not a user-facing problem.
    """
    if not annexes:
        return report

    parser = etree.XMLParser(resolve_entities=False)
    try:
        root = etree.fromstring(report.fileContent, parser)
    except etree.XMLSyntaxError as e:
        raise PdfConversionError(
            f"Generated report could not be reparsed to merge annexes into. {e}"
        ) from e
    tree = root.getroottree()

    if (head := root.find(_tag("head"))) is None:
        raise PdfConversionError(
            "Generated report has no <head> element to add annex CSS to."
        )
    if (body := root.find(_tag("body"))) is None:
        raise PdfConversionError(
            "Generated report has no <body> element to append annexes to."
        )
    if (tocList := root.find(f".//{_tag('ol')}[@class='toc-list']")) is None:
        raise PdfConversionError(
            'Generated report has no <ol class="toc-list"> to add an annexes entry to.'
        )

    annexesItem = etree.SubElement(tocList, _tag("li"))
    etree.SubElement(annexesItem, _tag("span")).text = tocLabel
    annexesList = etree.SubElement(annexesItem, _tag("ul"))

    for annex in annexes:
        style = etree.SubElement(head, _tag("style"))
        style.set("type", "text/css")
        style.text = annex.head_css

        annexRoot = etree.fromstring(
            annex.body_html.encode("UTF-8"), etree.XMLParser(resolve_entities=False)
        )
        body.append(annexRoot)

        entry = etree.SubElement(annexesList, _tag("li"))
        link = etree.SubElement(entry, _tag("a"))
        link.set("href", f"#{annex.anchor_id}")
        link.text = annex.label

    merged = etree.tostring(tree, encoding="unicode", doctype=tree.docinfo.doctype)
    return FilelikeAndFileName(
        fileContent=merged.encode("UTF-8"), filename=report.filename
    )
