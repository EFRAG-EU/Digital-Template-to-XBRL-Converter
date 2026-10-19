"""Facts whose taxonomy gives no hypercube in presentation still reach the report.

A taxonomy may define its hypercubes in the definition linkbase only, leaving its presentation
groups as plain lists. A list group used to drop every dimensionally qualified fact; they now
get a table per set of dimensions, built from the facts themselves.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from datetime import date
from typing import Any

import pytest
from tests.unitTests.synthetic_taxonomy import group_taxonomy_json, qname

from mireport.exceptions import InlineReportException
from mireport.report import InlineReport
from mireport.report.factbuilder import FactBuilder
from mireport.taxonomy import Taxonomy
from mireport.xml import getBootstrapQNameMaker

_Q = getBootstrapQNameMaker()
_Q.addNamespacePrefix("utr", "http://www.xbrl.org/2009/utr")


@pytest.fixture
def taxonomy(register_taxonomy: Callable[[dict[str, Any]], Taxonomy]) -> Taxonomy:
    return register_taxonomy(group_taxonomy_json())


def _report(taxonomy: Taxonomy, *, orphan: bool = False) -> InlineReport:
    report = InlineReport(taxonomy)
    report.setEntity("http://standards.iso.org/iso/17442", "529900T8BM49AURSDO55")
    report.addDurationPeriod("cur", date(2025, 1, 1), date(2025, 12, 31))
    report.setDefaultPeriodName("cur")

    def add(name: str, value: Any, **dims: str) -> None:
        concept = taxonomy.getConcept(name)
        fb = FactBuilder(report).setConcept(concept).setValue(value)
        if concept.isNumeric:
            fb.setSimpleUnit(_Q.fromString("utr:kg")).setDecimals(0)
        for axis, member in dims.items():
            dimension = taxonomy.getConcept(qname(axis))
            if dimension.isTypedDimension:
                fb.setTypedDimension(dimension, member)
            else:
                fb.setExplicitDimension(dimension, taxonomy.getConcept(member))
        report.addFact(fb.buildFact())

    add(qname("Emissions"), 10, ScopeAxis=qname("ScopeA"))
    add(qname("Emissions"), 20, ScopeAxis=qname("ScopeB"))
    add(qname("Total"), 30)
    add(qname("Intensity"), 5, TargetAxis="T1")
    add(qname("Intensity"), 7, TargetAxis="T2")
    add(qname("Note"), "note one", TargetAxis="T1")
    if orphan:
        add(qname("Orphan"), "lost")  # in no presentation group
    return report


def _render(report: InlineReport) -> str:
    return report.getInlineReport().fileContent.decode("utf-8")


def _tagged(html: str) -> list[str]:
    return re.findall(r'<ix:non(?:Fraction|Numeric)[^>]*name="([^"]+)"', html)


def test_every_fact_is_shown_though_presentation_has_no_hypercube(
    taxonomy: Taxonomy,
) -> None:
    report = _report(taxonomy)
    report.requireAllFactsRendered = True
    html = _render(report)
    assert sorted(_tagged(html)) == sorted(
        [qname("Emissions")] * 2
        + [qname("Total")]
        + [qname("Intensity")] * 2
        + [qname("Note")]
    )


def test_each_dimension_set_gets_its_own_titled_table(taxonomy: Taxonomy) -> None:
    html = _render(_report(taxonomy))
    titles = [t.strip() for t in re.findall(r"<h2[^>]*>\s*([^<]*)", html)]
    subheadings = re.findall(r'<h3 class="block-heading">\s*([^<]*)', html)
    # The group's own section, then one per dimension set; the title is never suffixed.
    assert titles.count("Group") == 3
    assert subheadings == ["By Scope", "By Target"]
    assert html.count('<table class="thematic-table">') == 2


def test_the_smaller_axis_becomes_the_columns(taxonomy: Taxonomy) -> None:
    html = _render(_report(taxonomy))
    byScope, byTarget = re.findall(r"<table.*?</table>", html, re.DOTALL)
    # One concept across two scopes: the scopes are rows.
    assert re.findall(r'<th class="[^"]*">\s*(Scope [AB])', byScope) == [
        "Scope A",
        "Scope B",
    ]
    # Two concepts across two targets: concepts are rows, targets are columns.
    headings = re.findall(r"<th[^>]*>\s*([^<]*?)\s*</th>", byTarget)
    assert "T1" in headings and "T2" in headings
    assert "Intensity" in headings and "Note" in headings


def test_a_gap_in_the_grid_is_left_blank(taxonomy: Taxonomy) -> None:
    byTarget = re.findall(r"<table.*?</table>", _render(_report(taxonomy)), re.DOTALL)[
        1
    ]
    # Note has no fact for T2.
    assert byTarget.count("<td></td>") == 1


def test_a_fact_that_appears_nowhere_is_an_error_when_required(
    taxonomy: Taxonomy,
) -> None:
    report = _report(taxonomy, orphan=True)
    report.requireAllFactsRendered = True
    with pytest.raises(InlineReportException, match="appear nowhere"):
        _render(report)


def test_a_fact_that_appears_nowhere_is_left_out_by_default(
    taxonomy: Taxonomy,
) -> None:
    assert qname("Orphan") not in _tagged(_render(_report(taxonomy, orphan=True)))
