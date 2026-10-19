"""Structural checks on a rendered Inline XBRL document, with no browser.

Visual judgement is not automated; what is cheap and catches real mistakes (a period header
spanning the wrong number of columns, a row a cell short) is: the document parses as XML, every
table is rectangular once colspan and rowspan are applied, a header row is as wide as the body, and
nothing loads an outside resource.
"""

from __future__ import annotations

from lxml import etree

XHTML = "{http://www.w3.org/1999/xhtml}"
_EXTERNAL = ("http://", "https://", "//", "ftp:")


def parse(content: bytes | str) -> etree._Element:
    if isinstance(content, str):
        content = content.encode("utf-8")
    return etree.fromstring(content)


def _span(cell: etree._Element, name: str) -> int:
    return int(cell.get(name, "1") or "1")


def table_widths(table: etree._Element) -> list[int]:
    """The width of each row of the table once colspan and rowspan are applied: its own cells
    plus the columns cells from rows above still cover."""
    occupied: set[tuple[int, int]] = (
        set()
    )  # (row, column) already taken by a spanning cell
    widths: list[int] = []
    for r, row in enumerate(table.iter(f"{XHTML}tr")):
        column = 0
        for cell in row:
            if cell.tag not in (f"{XHTML}td", f"{XHTML}th"):
                continue
            while (r, column) in occupied:
                column += 1
            colspan, rowspan = _span(cell, "colspan"), _span(cell, "rowspan")
            occupied.update(
                (r + dr, column + dc) for dr in range(rowspan) for dc in range(colspan)
            )
            column += colspan
        widths.append(max([column, *(c + 1 for rr, c in occupied if rr == r)]))
    return widths


def problems(content: bytes | str) -> list[str]:
    """Everything wrong with the document's structure; empty if sound."""
    try:
        root = parse(content)
    except etree.XMLSyntaxError as e:
        return [f"not well-formed XML: {e}"]
    found: list[str] = []
    for n, table in enumerate(root.iter(f"{XHTML}table")):
        widths = table_widths(table)
        if len(set(widths)) > 1:
            found.append(f"table {n} is not rectangular: row widths {widths}")
    for element in root.iter():
        for attribute in ("src", "href"):
            if (value := element.get(attribute)) is not None and value.startswith(
                _EXTERNAL
            ):
                found.append(f"<{etree.QName(element).localname}> loads {value}")
    return found
