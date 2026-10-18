"""Facts whose taxonomy gives no hypercube in presentation still reach the report.

A taxonomy may define its hypercubes in the definition linkbase only, leaving its presentation
groups as plain lists. A list group used to drop every dimensionally qualified fact; they now
get a table per set of dimensions, built from the facts themselves.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from datetime import date
from typing import Any

import pytest

import mireport.taxonomy as _taxonomy_module
from mireport.exceptions import InlineReportException
from mireport.report import InlineReport
from mireport.report.factbuilder import FactBuilder
from mireport.taxonomy import Taxonomy, loadTaxonomyJSON
from mireport.xml import getBootstrapQNameMaker

_NS = "https://example.com/tp"
_ENTRY = "test://layout/dimensional-fallback"
_ROLE = "https://example.com/tp/role/Group"
_LABEL = "http://www.xbrl.org/2003/role/label"
_Q = getBootstrapQNameMaker()
_Q.addNamespacePrefix("utr", "http://www.xbrl.org/2009/utr")


@pytest.fixture(autouse=True)
def _isolated_taxonomy_registry() -> Iterator[None]:
    before = set(_taxonomy_module._TAXONOMIES)
    yield
    for entryPoint in set(_taxonomy_module._TAXONOMIES) - before:
        del _taxonomy_module._TAXONOMIES[entryPoint]


def _concept(
    label: str,
    *,
    numeric: bool = False,
    abstract: bool = False,
    dimension: bool = False,
    hypercube: bool = False,
    other: dict[str, Any] | None = None,
) -> dict[str, Any]:
    kind = "xbrli:decimalItemType" if numeric else "xbrli:stringItemType"
    concept: dict[str, Any] = {
        "labels": {"en": {_LABEL: label}},
        "dataType": kind,
        "baseDataType": kind,
        "periodType": "duration",
    }
    if numeric:
        concept["numeric"] = True
    if abstract or dimension or hypercube:
        concept["abstract"] = True
    if dimension:
        concept["dimension"] = True
    if hypercube:
        concept["hypercube"] = True
    if other:
        concept["other"] = other
    return concept


def _cube(
    items: list[str], explicit: dict[str, list[str]], typed: list[str] | None = None
) -> dict[str, Any]:
    cube: dict[str, Any] = {
        "primaryItems": [[i, q] for i, q in enumerate(items)],
        "xbrldt:contextElement": "scenario",
        "xbrldt:closed": True,
        "explicitDimensions": explicit,
    }
    if typed:
        cube["typedDimensions"] = typed
    return cube


def _taxonomy() -> Taxonomy:
    """Only primary items are presented: no hypercube, axis or member in the group, though the
    definition linkbase has a cube by scope (explicit) and one by target (typed)."""
    return loadTaxonomyJSON(
        {
            "entryPoint": _ENTRY,
            "namespaces": {"tp": _NS, "xs": "http://www.w3.org/2001/XMLSchema"},
            "concepts": {
                "tp:Group": _concept("Group", abstract=True),
                "tp:Emissions": _concept("Emissions", numeric=True),
                "tp:Total": _concept("Total", numeric=True),
                "tp:Intensity": _concept("Intensity", numeric=True),
                "tp:Note": _concept("Note"),
                "tp:Orphan": _concept("Orphan"),
                "tp:ScopeAxis": _concept("Scope [Axis]", dimension=True),
                "tp:ScopeA": _concept("Scope A", abstract=True),
                "tp:ScopeB": _concept("Scope B", abstract=True),
                "tp:TargetAxis": _concept(
                    "Target [Axis]", dimension=True, other={"typedElement": "tp:TYP"}
                ),
                "tp:ScopeCube": _concept("Scope cube", hypercube=True),
                "tp:TargetCube": _concept("Target cube", hypercube=True),
            },
            "xs_elements": {
                "tp:TYP": {"dataType": "xs:string", "baseDataType": "xs:string"}
            },
            "presentation": {
                _ROLE: {
                    "definition": "Group",
                    "rows": [
                        [0, "tp:Group"],
                        [1, "tp:Emissions"],
                        [1, "tp:Total"],
                        [1, "tp:Intensity"],
                        [1, "tp:Note"],
                    ],
                }
            },
            "dimensions": {
                "_defaults": {},
                _ROLE: {
                    "tp:ScopeCube": _cube(
                        ["tp:Emissions"], {"tp:ScopeAxis": ["tp:ScopeA", "tp:ScopeB"]}
                    ),
                    "tp:TargetCube": _cube(
                        ["tp:Intensity", "tp:Note"], {}, ["tp:TargetAxis"]
                    ),
                },
            },
        }
    )


def _report(*, orphan: bool = False) -> InlineReport:
    taxonomy = _taxonomy()
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
            dimension = taxonomy.getConcept(f"tp:{axis}")
            if dimension.isTypedDimension:
                fb.setTypedDimension(dimension, member)
            else:
                fb.setExplicitDimension(dimension, taxonomy.getConcept(member))
        report.addFact(fb.buildFact())

    add("tp:Emissions", 10, ScopeAxis="tp:ScopeA")
    add("tp:Emissions", 20, ScopeAxis="tp:ScopeB")
    add("tp:Total", 30)
    add("tp:Intensity", 5, TargetAxis="T1")
    add("tp:Intensity", 7, TargetAxis="T2")
    add("tp:Note", "note one", TargetAxis="T1")
    if orphan:
        add("tp:Orphan", "lost")  # in no presentation group
    return report


def _render(report: InlineReport) -> str:
    return report.getInlineReport().fileContent.decode("utf-8")


def _tagged(html: str) -> list[str]:
    return re.findall(r'<ix:non(?:Fraction|Numeric)[^>]*name="([^"]+)"', html)


def test_every_fact_is_shown_though_presentation_has_no_hypercube() -> None:
    report = _report()
    report.requireAllFactsRendered = True
    html = _render(report)
    assert sorted(_tagged(html)) == sorted(
        ["tp:Emissions"] * 2 + ["tp:Total"] + ["tp:Intensity"] * 2 + ["tp:Note"]
    )


def test_each_dimension_set_gets_its_own_titled_table() -> None:
    html = _render(_report())
    titles = [t.strip() for t in re.findall(r"<h2[^>]*>\s*([^<]*)", html)]
    assert "Group - by Scope" in titles
    assert "Group - by Target" in titles
    assert html.count('<table class="thematic-table">') == 2


def test_the_smaller_axis_becomes_the_columns() -> None:
    html = _render(_report())
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


def test_a_gap_in_the_grid_is_left_blank() -> None:
    byTarget = re.findall(r"<table.*?</table>", _render(_report()), re.DOTALL)[1]
    # Note has no fact for T2.
    assert byTarget.count("<td></td>") == 1


def test_a_fact_that_appears_nowhere_is_an_error_when_required() -> None:
    report = _report(orphan=True)
    report.requireAllFactsRendered = True
    with pytest.raises(InlineReportException, match="appear nowhere"):
        _render(report)


def test_a_fact_that_appears_nowhere_is_left_out_by_default() -> None:
    assert "tp:Orphan" not in _tagged(_render(_report(orphan=True)))
