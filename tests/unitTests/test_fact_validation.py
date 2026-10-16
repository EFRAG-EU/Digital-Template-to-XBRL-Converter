"""Facts are checked against the taxonomy's hypercubes wherever they enter a report.

FactBuilder.buildFact checks, and so does InlineReport.addFact, so a Fact constructed directly (or
copied with FactBuilder.fromFact and changed) cannot get in invalid. A typed dimension a hypercube
declares must be present; its value's *content* is not checked (a known gap).
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import date
from typing import Any

import pytest

import mireport.taxonomy as _taxonomy_module
from mireport.exceptions import InlineReportException
from mireport.report import InlineReport
from mireport.report.fact import Fact
from mireport.report.factbuilder import FactBuilder
from mireport.report.model import FactPeriod, TypedDimensionValue
from mireport.taxonomy import Taxonomy, loadTaxonomyJSON

_NS = "https://example.com/vsme"
_LABEL = "http://www.xbrl.org/2003/role/label"


def _concept(
    data_type: str = "xbrli:stringItemType",
    *,
    period_type: str = "duration",
    hypercube: bool = False,
    dimension: bool = False,
    other: dict[str, Any] | None = None,
) -> dict[str, Any]:
    concept: dict[str, Any] = {
        "labels": {"en": {_LABEL: "Label"}},
        "dataType": data_type,
        "baseDataType": data_type,
        "periodType": period_type,
    }
    if hypercube or dimension:
        concept["abstract"] = True
    if hypercube:
        concept["hypercube"] = True
    if dimension:
        concept["dimension"] = True
    if other:
        concept["other"] = other
    return concept


def _cube(
    primary_items: list[str],
    *,
    explicit: dict[str, list[str]] | None = None,
    typed: list[str] | None = None,
) -> dict[str, Any]:
    cube: dict[str, Any] = {
        "primaryItems": [[i, q] for i, q in enumerate(primary_items)],
        "xbrldt:contextElement": "scenario",
        "xbrldt:closed": True,
        "explicitDimensions": explicit or {},
    }
    if typed:
        cube["typedDimensions"] = typed
    return cube


@pytest.fixture(autouse=True)
def _isolated_taxonomy_registry() -> Iterator[None]:
    before = set(_taxonomy_module._TAXONOMIES)
    yield
    for entryPoint in set(_taxonomy_module._TAXONOMIES) - before:
        del _taxonomy_module._TAXONOMIES[entryPoint]


def _taxonomy() -> Taxonomy:
    """``vsme:ByRegion`` needs a typed ``vsme:RegionAxis``; ``vsme:Both`` is in that cube and in
    another (``vsme:ByKind``, explicit ``vsme:KindAxis``); ``vsme:Plain`` is in no cube."""
    concepts = {
        "vsme:TableRegion": _concept(hypercube=True),
        "vsme:TableKind": _concept(hypercube=True),
        "vsme:RegionAxis": _concept(dimension=True, other={"typedElement": "vsme:TYP"}),
        "vsme:KindAxis": _concept(dimension=True),
        "vsme:KindA": _concept(),
        "vsme:ByRegion": _concept(),
        "vsme:Both": _concept(),
        "vsme:Plain": _concept(),
    }
    dimensions = {
        "role-region": {
            "vsme:TableRegion": _cube(
                ["vsme:ByRegion", "vsme:Both"], typed=["vsme:RegionAxis"]
            )
        },
        "role-kind": {
            "vsme:TableKind": _cube(
                ["vsme:Both"], explicit={"vsme:KindAxis": ["vsme:KindA"]}
            )
        },
    }
    return loadTaxonomyJSON(
        {
            "entryPoint": "test://fact-validation",
            "namespaces": {"vsme": _NS, "xs": "http://www.w3.org/2001/XMLSchema"},
            "concepts": concepts,
            "presentation": {},
            "dimensions": dimensions,
            "xs_elements": {
                "vsme:TYP": {"dataType": "xs:string", "baseDataType": "xs:string"}
            },
        }
    )


@pytest.fixture
def report() -> InlineReport:
    report = InlineReport(_taxonomy())
    report.addDurationPeriod("cur", date(2025, 1, 1), date(2025, 12, 31))
    report.setDefaultPeriodName("cur")
    return report


def _builder(report: InlineReport, name: str, value: str = "x") -> FactBuilder:
    return (
        FactBuilder(report).setConcept(report.taxonomy.getConcept(name)).setValue(value)
    )


def _typed(report: InlineReport, value: str) -> TypedDimensionValue:
    return TypedDimensionValue.of(report.taxonomy.getConcept("vsme:RegionAxis"), value)


def test_the_builder_rejects_a_missing_declared_typed_dimension(
    report: InlineReport,
) -> None:
    with pytest.raises(InlineReportException, match="No valid dimensional"):
        _builder(report, "vsme:ByRegion").buildFact()


def test_the_builder_accepts_the_typed_dimension(report: InlineReport) -> None:
    fb = _builder(report, "vsme:ByRegion").setTypedDimension(
        report.taxonomy.getConcept("vsme:RegionAxis"), "UK"
    )
    assert fb.buildFact().typed_values[0].value == "UK"


def test_add_fact_rejects_a_directly_built_fact_missing_the_typed_dimension(
    report: InlineReport,
) -> None:
    fact = Fact(
        report.taxonomy.getConcept("vsme:ByRegion"),
        "x",
        report,
        period=FactPeriod(report.defaultReportPeriod),
    )
    with pytest.raises(InlineReportException, match="No valid dimensional"):
        report.addFact(fact)
    assert report.facts == []


def test_add_fact_rejects_a_dimension_on_a_concept_in_no_cube(
    report: InlineReport,
) -> None:
    fact = Fact(
        report.taxonomy.getConcept("vsme:Plain"),
        "x",
        report,
        period=FactPeriod(report.defaultReportPeriod),
        typed_dimensions=[_typed(report, "UK")],
    )
    with pytest.raises(InlineReportException, match="does not participate"):
        report.addFact(fact)


def test_add_fact_accepts_a_valid_fact(report: InlineReport) -> None:
    fact = Fact(
        report.taxonomy.getConcept("vsme:ByRegion"),
        "x",
        report,
        period=FactPeriod(report.defaultReportPeriod),
        typed_dimensions=[_typed(report, "UK")],
    )
    report.addFact(fact)
    assert report.facts == [fact]


def test_from_fact_copies_and_revalidates(report: InlineReport) -> None:
    original = (
        _builder(report, "vsme:ByRegion")
        .setTypedDimension(report.taxonomy.getConcept("vsme:RegionAxis"), "UK")
        .buildFact()
    )
    copy = FactBuilder.fromFact(report, original).setValue("y").buildFact()
    assert copy is not original
    assert copy.value == "y"
    assert copy.typed_values == original.typed_values
    assert copy.period == original.period


def test_a_concept_in_two_cubes_is_valid_for_either(report: InlineReport) -> None:
    """``vsme:Both`` is in the typed cube and the explicit one: a fact valid for the explicit
    cube has no typed dimension, and that is fine (OR across the cubes)."""
    taxonomy = report.taxonomy
    for_kind = (
        _builder(report, "vsme:Both")
        .setExplicitDimension(
            taxonomy.getConcept("vsme:KindAxis"), taxonomy.getConcept("vsme:KindA")
        )
        .buildFact()
    )
    for_region = (
        _builder(report, "vsme:Both")
        .setTypedDimension(taxonomy.getConcept("vsme:RegionAxis"), "UK")
        .buildFact()
    )
    report.addFact(for_kind)
    report.addFact(for_region)
    assert len(report.facts) == 2
    # Neither cube accepts a fact with neither dimension.
    with pytest.raises(InlineReportException):
        _builder(report, "vsme:Both").buildFact()


def test_a_fact_valid_for_the_other_cube_is_not_forced_into_the_typed_table() -> None:
    """``tp:Intensity`` sits in a typed-dimension cube (a table) and in an explicit one (another
    group). A fact for the explicit cube has no typed dimension, which is valid for it: the typed
    table must neither take it nor fail on it. (A concept in several groups shows its facts in each
    group, by whichever of its own dimensions fit; that is the layout's existing design.)"""
    import re

    from tests.unitTests.xbrljson_reader import test_dimensional_fallback as base

    from mireport.conversionresults import ConversionResultsBuilder
    from mireport.xbrljson_reader import XbrlJsonProcessor

    other_role = "https://example.com/tp/role/Other"
    taxonomy = base._taxonomy_json()
    taxonomy["concepts"]["tp:KindCube"] = base._concept("Kind cube", hypercube=True)
    taxonomy["presentation"][other_role] = {
        "definition": "Other",
        "rows": [[0, "tp:Group"], [1, "tp:Intensity"]],
    }
    taxonomy["dimensions"][other_role] = {
        "tp:KindCube": base._cube(
            ["tp:Intensity"], {"tp:ScopeAxis": ["tp:ScopeA", "tp:ScopeB"]}
        )
    }
    loadTaxonomyJSON(taxonomy)
    doc = base._document()
    doc["facts"]["kind"] = base._fact(
        "tp:Intensity", "55", **{"tp:ScopeAxis": "tp:ScopeA"}
    )
    html = (
        XbrlJsonProcessor(doc, ConversionResultsBuilder(), strict=True)
        .createReport()
        .getInlineReport()
        .fileContent.decode("utf-8")
    )
    tagged = re.findall(
        r'<ix:non(?:Fraction|Numeric)[^>]*name="tp:Intensity"[^>]*>([^<]*)<', html
    )
    assert "55" in tagged  # shown (strict mode would have raised otherwise)
    tables = [
        t for t in re.findall(r"<table.*?</table>", html, re.DOTALL) if ">55<" in t
    ]
    assert tables
    assert not any("T1" in t or "T2" in t for t in tables)  # never in the typed table
