"""Inline a converted document's sibling files as ``data:`` URIs.

A document set member travels in the report package as a single file, so
anything it still references by relative path is simply lost. pdf2htmlEX can
embed its own assets given the right flags; pdftohtml cannot — the Poppler build
this was written against has no ``-dataurls`` option at all and always writes one
PNG per page next to the HTML.

Rather than make each backend responsible for self-containment, this runs over
every backend's output while its working directory still exists. For pdf2htmlEX
it should find nothing to do, which is the point: it is a safety net as much as
a fallback, and catches an embed flag being wrong or a future version changing
what it inlines.

CSS is tokenised with tinycss2 rather than pattern-matched: ``url()`` appears in
stylesheets and style attributes with or without quotes, alongside comments and
strings that may themselves contain parentheses, and a regular expression over
the whole serialised document cannot tell those apart from a reference.
"""

from __future__ import annotations

import base64
import mimetypes
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.parse import unquote

import tinycss2
from lxml import etree
from tinycss2.ast import (
    CurlyBracketsBlock,
    FunctionBlock,
    Node,
    ParenthesesBlock,
    SquareBracketsBlock,
    StringToken,
    URLToken,
)

if TYPE_CHECKING:
    from collections.abc import Iterable

LINK_ATTRIBUTES = ("src", "href")
STYLE_ATTRIBUTE = "style"

_SKIP_PREFIXES = ("data:", "http://", "https://", "//", "#", "mailto:", "about:")


def _dataUriFromPath(path: Path) -> str | None:
    """The ``data:`` URI holding @path's contents, or None if it is not a file."""
    if not path.is_file():
        return None
    mimeType, _ = mimetypes.guess_type(path.name)
    encoded = base64.b64encode(path.read_bytes()).decode("ascii")
    return f"data:{mimeType or 'application/octet-stream'};base64,{encoded}"


class _ResourceResolver:
    """Turns a reference into a ``data:`` URI, reading each file once."""

    def __init__(self, workingDir: Path) -> None:
        self._workingDir = workingDir.resolve()
        # None is an answer, not an absence: nothing usable is there to read.
        self._cache: dict[Path, str | None] = {}

    def uriFor(self, reference: str) -> str | None:
        """The ``data:`` URI for @reference, or None to leave it alone.

        Anything that escapes the working directory is refused rather than read:
        the reference ultimately derives from a converter's handling of an
        uploaded file, and nothing outside its own scratch space is ever
        legitimate.
        """
        if not reference or reference.startswith(_SKIP_PREFIXES):
            return None
        candidate = (self._workingDir / unquote(reference)).resolve()
        if not candidate.is_relative_to(self._workingDir):
            return None
        try:
            return self._cache[candidate]
        except KeyError:
            uri = self._cache[candidate] = _dataUriFromPath(candidate)
            return uri


class _CssInliner:
    """Rewrites the url() references in one stylesheet or style attribute."""

    def __init__(self, resolver: _ResourceResolver) -> None:
        self._resolver = resolver
        self._changed = False

    def rewrite(self, css: str) -> str | None:
        """Return @css with local url() references inlined, or None if unchanged.

        Component values rather than rules: a style attribute holds declarations
        and a <style> element holds rules, but url() means the same in both and
        the token stream serialises back unchanged.
        """
        tokens = tinycss2.parse_component_value_list(css, skip_comments=False)
        self._changed = False
        self._rewriteTokens(tokens)
        return tinycss2.serialize(tokens) if self._changed else None

    def _rewriteTokens(self, tokens: Iterable[Node]) -> None:
        for token in tokens:
            match token:
                case URLToken():
                    if (uri := self._resolver.uriFor(token.value)) is not None:
                        # A data: URI holds none of the whitespace, quotes,
                        # parentheses or backslashes that would need quoting, so
                        # the unquoted spelling is always valid here.
                        token.value = uri
                        token.representation = f"url({uri})"
                        self._changed = True
                case FunctionBlock(lower_name="url"):
                    # A quoted url("...") is a function block, not a URLToken.
                    self._rewriteQuotedUrls(token.arguments)
                case FunctionBlock():
                    self._rewriteTokens(token.arguments)
                case CurlyBracketsBlock() | SquareBracketsBlock() | ParenthesesBlock():
                    self._rewriteTokens(token.content)

    def _rewriteQuotedUrls(self, arguments: Iterable[Node]) -> None:
        for argument in arguments:
            if not isinstance(argument, StringToken):
                continue
            if (uri := self._resolver.uriFor(argument.value)) is not None:
                argument.value = uri
                argument.representation = f'"{uri}"'
                self._changed = True


class _DocumentInliner:
    """Rewrites every reference in one parsed document, in place."""

    def __init__(self, root: etree._Element, workingDir: Path) -> None:
        self._root = root
        self._resolver = _ResourceResolver(workingDir)
        self._css = _CssInliner(self._resolver)
        self.changed = False

    def run(self) -> None:
        for element in self._root.iter():
            if not isinstance(element.tag, str):
                continue
            self._inlineAttributeLinks(element)
            self._inlineElementCss(element)

    def _inlineAttributeLinks(self, element: etree._Element) -> None:
        for attribute in LINK_ATTRIBUTES:
            if (reference := element.get(attribute)) is None:
                continue
            if (uri := self._resolver.uriFor(reference)) is not None:
                element.set(attribute, uri)
                self.changed = True

    def _inlineElementCss(self, element: etree._Element) -> None:
        """Rewrite url() in a <style> element's body and in a style attribute."""
        if (
            etree.QName(element).localname == "style"
            and element.text
            and (rewritten := self._css.rewrite(element.text)) is not None
        ):
            element.text = rewritten
            self.changed = True
        if (style := element.get(STYLE_ATTRIBUTE)) and (
            rewritten := self._css.rewrite(style)
        ) is not None:
            element.set(STYLE_ATTRIBUTE, rewritten)
            self.changed = True


def inlineLocalResources(content: bytes, workingDir: Path) -> bytes:
    """Replace references to files in @workingDir with ``data:`` URIs.

    Returns @content unchanged if it cannot be parsed — normalisation runs next
    and reports the problem properly.
    """
    try:
        root = etree.fromstring(
            content, etree.HTMLParser(recover=True, encoding="utf-8")
        )
    except (etree.XMLSyntaxError, ValueError):
        return content
    if root is None:
        return content

    inliner = _DocumentInliner(root, workingDir)
    inliner.run()
    if not inliner.changed:
        return content

    # Serialised as XML rather than HTML: an HTML serialisation would unclose
    # void elements again, undoing well-formedness that a converter's output may
    # already have had. Normalisation re-parses either way, but there is no
    # reason to hand it something worse than it was given.
    return etree.tostring(root, method="xml")
