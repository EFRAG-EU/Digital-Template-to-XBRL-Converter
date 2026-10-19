"""The rendered document is sound as a document: well-formed, rectangular tables, nothing external.

Nobody looks at the output in a browser here (that stays human); these catch the mistakes a machine
can: a period header spanning the wrong number of columns, a row a cell short, a broken tag. See
table_integrity.py for what is checked.
"""

from __future__ import annotations

import copy
from collections.abc import Callable
from typing import Any

import pytest
from tests.unitTests.synthetic_taxonomy import group_document, group_taxonomy_json
from tests.unitTests.table_integrity import parse, problems, table_widths

from mireport.conversionresults import ConversionResultsBuilder
from mireport.taxonomy import Taxonomy
from mireport.xbrljson_reader import XbrlJsonProcessor

EARLIER_YEAR = "2024-01-01T00:00:00/2025-01-01T00:00:00"


def _with_another_period(doc: dict[str, Any]) -> dict[str, Any]:
    """Every fact repeated a year earlier with a different value."""
    for key, fact in list(doc["facts"].items()):
        earlier = copy.deepcopy(fact)
        earlier["dimensions"]["period"] = EARLIER_YEAR
        if earlier["value"].isdigit():
            earlier["value"] = str(int(earlier["value"]) + 1)
        doc["facts"][f"{key}-earlier"] = earlier
    return doc


def _table(xml: str):
    return parse(f"<html xmlns='http://www.w3.org/1999/xhtml'>{xml}</html>".encode())


class TestTheChecker:
    """The checker itself, on hand-written tables."""

    def test_a_rectangular_table_is_fine(self) -> None:
        html = (
            "<table><tr><td>a</td><td>b</td></tr><tr><td>c</td><td>d</td></tr></table>"
        )
        assert (
            problems(f"<html xmlns='http://www.w3.org/1999/xhtml'>{html}</html>") == []
        )

    def test_a_short_row_is_found(self) -> None:
        html = "<table><tr><td>a</td><td>b</td></tr><tr><td>c</td></tr></table>"
        found = problems(f"<html xmlns='http://www.w3.org/1999/xhtml'>{html}</html>")
        assert found and "not rectangular" in found[0]

    def test_colspan_and_rowspan_are_counted(self) -> None:
        table = _table(
            "<table><tr><th rowspan='2'>x</th><th colspan='2'>y</th></tr>"
            "<tr><td>a</td><td>b</td></tr></table>"
        ).find(".//{*}table")
        assert table_widths(table) == [3, 3]

    def test_a_header_span_wider_than_the_body_is_found(self) -> None:
        html = "<table><tr><th colspan='3'>y</th></tr><tr><td>a</td><td>b</td></tr></table>"
        assert problems(f"<html xmlns='http://www.w3.org/1999/xhtml'>{html}</html>")

    def test_malformed_xml_is_found(self) -> None:
        assert "not well-formed" in problems("<html><p></html>")[0]

    def test_an_external_resource_is_found(self) -> None:
        html = "<html xmlns='http://www.w3.org/1999/xhtml'><img src='https://x.example/a.png'/></html>"
        assert "loads https://x.example/a.png" in problems(html)[0]


@pytest.mark.parametrize(
    "another_period", [False, True], ids=["one period", "with another period"]
)
def test_a_report_with_lists_explicit_and_typed_tables_is_sound(
    register_taxonomy: Callable[[dict[str, Any]], Taxonomy], another_period: bool
) -> None:
    """Comparative columns (a declared prior period) are checked on a real report in
    xlsx_template_reader/test_prior_period_everywhere.py."""
    register_taxonomy(group_taxonomy_json())
    doc = group_document()
    if another_period:
        doc = _with_another_period(doc)
    processor = XbrlJsonProcessor(doc, ConversionResultsBuilder(), strict=True)
    html = processor.createReport().getInlineReport().fileContent.decode("utf-8")
    assert problems(html) == []
