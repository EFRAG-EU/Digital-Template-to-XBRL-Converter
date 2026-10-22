"""Namespaces and the small lxml helpers every other module here uses."""

from __future__ import annotations

from typing import TYPE_CHECKING

from lxml import etree

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


def _tagText(element: etree._Element) -> str:
    """@element's tag, which is text for every element _elements() yields.

    lxml types ``tag`` as ``str | bytes | bytearray | QName`` because comments and
    processing instructions carry a function there; narrow it once, here.
    """
    tag = element.tag
    if not isinstance(tag, str):
        raise TypeError(f"Element tag is not text: {tag!r}")
    return tag


def _localName(element: etree._Element) -> str:
    """@element's name without its namespace.

    Split rather than etree.QName, which refuses to parse exactly the names
    worth reporting — the prefixed ones the HTML parser took literally.
    """
    return _tagText(element).rpartition("}")[2]


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
