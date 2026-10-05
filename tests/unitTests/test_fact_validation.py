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
    concept,
    cube,
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
