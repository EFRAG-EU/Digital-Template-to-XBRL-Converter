"""Facts are checked against the taxonomy's hypercubes when they are built.

Every fact comes from FactBuilder.buildFact, including one copied with FactBuilder.fromFact and
changed, so none gets in invalid. A typed dimension a hypercube declares must be present; its
value's *content* is not checked (a known gap).
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import date
from typing import Any

import pytest
from tests.unitTests.synthetic_taxonomy import (
    NAMESPACE,
    concept,
    cube,
    fact,
    group_document,
    group_taxonomy_json,
    qname,
    taxonomy_json,
)

from mireport.exceptions import InlineReportException
from mireport.report import InlineReport
from mireport.report.factbuilder import FactBuilder
from mireport.report.model import TypedDimensionValue
from mireport.taxonomy import Taxonomy


def _taxonomy_document() -> dict[str, Any]:
    """``ByRegion`` needs a typed ``RegionAxis``; ``Both`` is in that cube and in another
    (``ByKind``, explicit ``KindAxis``); ``Plain`` is in no cube."""
    return taxonomy_json(
        "test://synthetic/fact-validation",
        concepts={
            qname("TableRegion"): concept("Table region", hypercube=True),
            qname("TableKind"): concept("Table kind", hypercube=True),
            qname("RegionAxis"): concept(
                "Region [Axis]",
                dimension=True,
                other={"typedElement": qname("RegionType")},
            ),
            qname("KindAxis"): concept("Kind [Axis]", dimension=True),
            qname("KindA"): concept("Kind A"),
            qname("ByRegion"): concept("By region"),
            qname("Both"): concept("Both"),
            qname("Plain"): concept("Plain"),
        },
        presentation={},
        dimensions={
            "role-region": {
                qname("TableRegion"): cube(
                    [qname("ByRegion"), qname("Both")],
                    {},
                    typed=[qname("RegionAxis")],
                )
            },
            "role-kind": {
                qname("TableKind"): cube(
                    [qname("Both")], {qname("KindAxis"): [qname("KindA")]}
                )
            },
        },
        xsElements={
            qname("RegionType"): {"dataType": "xs:string", "baseDataType": "xs:string"}
        },
    )


@pytest.fixture
def report(register_taxonomy: Callable[[dict[str, Any]], Taxonomy]) -> InlineReport:
    report = InlineReport(register_taxonomy(_taxonomy_document()))
    report.addDurationPeriod("cur", date(2025, 1, 1), date(2025, 12, 31))
    report.setDefaultPeriodName("cur")
    return report


def _builder(report: InlineReport, name: str, value: str = "x") -> FactBuilder:
    return (
        FactBuilder(report).setConcept(report.taxonomy.getConcept(name)).setValue(value)
    )


def _typed(report: InlineReport, value: str) -> TypedDimensionValue:
    return TypedDimensionValue.of(
        report.taxonomy.getConcept(qname("RegionAxis")), value
    )


def test_the_builder_rejects_a_missing_declared_typed_dimension(
    report: InlineReport,
) -> None:
    with pytest.raises(InlineReportException, match="No valid dimensional"):
        _builder(report, qname("ByRegion")).buildFact()


def test_the_builder_accepts_the_typed_dimension(report: InlineReport) -> None:
    fb = _builder(report, qname("ByRegion")).setTypedDimension(
        report.taxonomy.getConcept(qname("RegionAxis")), "UK"
    )
    assert fb.buildFact().typed_values[0].value == "UK"


def test_from_fact_copies_and_revalidates(report: InlineReport) -> None:
    original = (
        _builder(report, qname("ByRegion"))
        .setTypedDimension(report.taxonomy.getConcept(qname("RegionAxis")), "UK")
        .buildFact()
    )
    copy = FactBuilder.fromFact(report, original).setValue("y").buildFact()
    assert copy is not original
    assert copy.value == "y"
    assert copy.typed_values == original.typed_values
    assert copy.period == original.period


def test_a_concept_in_two_cubes_is_valid_for_either(report: InlineReport) -> None:
    """``Both`` is in the typed cube and the explicit one: a fact valid for the explicit
    cube has no typed dimension, and that is fine (OR across the cubes)."""
    taxonomy = report.taxonomy
    for_kind = (
        _builder(report, qname("Both"))
        .setExplicitDimension(
            taxonomy.getConcept(qname("KindAxis")), taxonomy.getConcept(qname("KindA"))
        )
        .buildFact()
    )
    for_region = (
        _builder(report, qname("Both"))
        .setTypedDimension(taxonomy.getConcept(qname("RegionAxis")), "UK")
        .buildFact()
    )
    report.addFact(for_kind)
    report.addFact(for_region)
    assert len(report.facts) == 2
    # Neither cube accepts a fact with neither dimension.
    with pytest.raises(InlineReportException):
        _builder(report, qname("Both")).buildFact()


def test_a_fact_valid_for_the_other_cube_is_not_forced_into_the_typed_table(
    register_taxonomy: Callable[[dict[str, Any]], Taxonomy],
) -> None:
    """``Intensity`` sits in a typed-dimension cube (a table) and in an explicit one (another
    group). A fact for the explicit cube has no typed dimension, which is valid for it: the typed
    table must neither take it nor fail on it. (A concept in several groups shows its facts in each
    group, by whichever of its own dimensions fit; that is the layout's existing design.)"""
    import re

    from mireport.conversionresults import ConversionResultsBuilder
    from mireport.xbrljson_reader import XbrlJsonProcessor

    other_role = f"{NAMESPACE}/role/Other"
    taxonomy = group_taxonomy_json()
    taxonomy["concepts"][qname("KindCube")] = concept("Kind cube", hypercube=True)
    taxonomy["presentation"][other_role] = {
        "definition": "Other",
        "rows": [[0, qname("Group")], [1, qname("Intensity")]],
    }
    taxonomy["dimensions"][other_role] = {
        qname("KindCube"): cube(
            [qname("Intensity")],
            {qname("ScopeAxis"): [qname("ScopeA"), qname("ScopeB")]},
        )
    }
    register_taxonomy(taxonomy)
    doc = group_document()
    doc["facts"]["kind"] = fact(
        qname("Intensity"),
        "55",
        dimensions={qname("ScopeAxis"): qname("ScopeA")},
    )
    html = (
        XbrlJsonProcessor(doc, ConversionResultsBuilder(), strict=True)
        .createReport()
        .getInlineReport()
        .fileContent.decode("utf-8")
    )
    tagged = re.findall(
        rf'<ix:non(?:Fraction|Numeric)[^>]*name="{qname("Intensity")}"[^>]*>([^<]*)<',
        html,
    )
    assert "55" in tagged  # shown (strict mode would have raised otherwise)
    tables = [
        t for t in re.findall(r"<table.*?</table>", html, re.DOTALL) if ">55<" in t
    ]
    assert tables
    assert not any("T1" in t or "T2" in t for t in tables)  # never in the typed table
