"""End-to-end unit tests for buildFacts() against a real (hand-written, no Arelle)
taxonomy -- see test_taxonomy_dimensions.py for the same JSON-only-taxonomy
approach. The pure per-dimension decision logic (value/unit selection,
representative-value picking, dimension-map building) is covered in isolation
in test_sampling.py; this file is for behaviour that only emerges from the full
buildFacts() orchestration, starting with the combinatorial "vary every
dimension at once" fact.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest

import mireport.taxonomy as _taxonomy_module
from mireport.coverage_report_generator._sampling import (
    SampleEntityPeriod,
    buildFacts,
    reportableConcepts,
)
from mireport.taxonomy import Taxonomy, loadTaxonomyJSON

_NS = "https://example.com/vsme"
_STANDARD_LABEL = "http://www.xbrl.org/2003/role/label"


@pytest.fixture(autouse=True)
def _isolated_taxonomy_registry() -> Iterator[None]:
    """loadTaxonomyJSON() registers into the process-lifetime _TAXONOMIES
    registry with no way to unregister -- see the identical fixture in
    test_taxonomy_dimensions.py for why this cleanup matters."""
    before = set(_taxonomy_module._TAXONOMIES)
    yield
    for entryPoint in set(_taxonomy_module._TAXONOMIES) - before:
        del _taxonomy_module._TAXONOMIES[entryPoint]


def _concept(
    *,
    dimension: bool = False,
    hypercube: bool = False,
    abstract: bool = False,
    numeric: bool = False,
    data_type: str = "xbrli:stringItemType",
    base_data_type: str = "xbrli:stringItemType",
    other: dict[str, Any] | None = None,
) -> dict[str, Any]:
    jconcept: dict[str, Any] = {
        "labels": {"en": {_STANDARD_LABEL: "Label"}},
        "dataType": data_type,
        "baseDataType": base_data_type,
        "periodType": "duration",
    }
    if numeric:
        jconcept["numeric"] = True
    if dimension or hypercube or abstract:
        jconcept["abstract"] = True
    if dimension:
        jconcept["dimension"] = True
    if hypercube:
        jconcept["hypercube"] = True
    if other:
        jconcept["other"] = other
    return jconcept


def _cube(
    primary_items: list[str],
    explicit_dimensions: dict[str, list[str]] | None = None,
    *,
    typed_dimensions: list[str] | None = None,
) -> dict[str, Any]:
    cube: dict[str, Any] = {
        "primaryItems": [[i, q] for i, q in enumerate(primary_items)],
        "xbrldt:contextElement": "scenario",
        "xbrldt:closed": True,
        "explicitDimensions": explicit_dimensions or {},
    }
    if typed_dimensions:
        cube["typedDimensions"] = typed_dimensions
    return cube


def _build_taxonomy(
    entry_point: str,
    dimensions: dict[str, Any],
    *,
    extra_concepts: dict[str, dict[str, Any]] | None = None,
    extra_xs_elements: dict[str, dict[str, Any]] | None = None,
) -> Taxonomy:
    concepts = {
        "vsme:Table": _concept(hypercube=True),
        "vsme:Item": _concept(),
        "vsme:AxisOne": _concept(dimension=True),
        "vsme:MemberA": _concept(abstract=True),
        "vsme:MemberB": _concept(abstract=True),
        "vsme:AxisTwo": _concept(dimension=True),
        "vsme:MemberX": _concept(abstract=True),
        "vsme:MemberY": _concept(abstract=True),
        "vsme:TypedAxis": _concept(dimension=True, other={"typedElement": "vsme:TYP"}),
        **(extra_concepts or {}),
    }
    bits = {
        "entryPoint": entry_point,
        "namespaces": {"vsme": _NS, "xs": "http://www.w3.org/2001/XMLSchema"},
        "concepts": concepts,
        "presentation": {},
        "dimensions": dimensions,
        "xs_elements": {
            "vsme:TYP": {"dataType": "xs:string", "baseDataType": "xs:string"},
            **(extra_xs_elements or {}),
        },
    }
    return loadTaxonomyJSON(bits)


PERIOD = SampleEntityPeriod(
    entity="lei:529900T8BM49AURSDO55",
    entityPrefix="lei",
    entityNamespace="http://standards.iso.org/iso/17442",
    periodInstant="2027-01-01T00:00:00",
    periodDuration="2026-01-01T00:00:00/2027-01-01T00:00:00",
)


class TestVaryAllDimensionsFact:
    def test_one_extra_fact_varies_every_dimension_at_once(self) -> None:
        dimensions = {
            "role-1": {
                "vsme:Table": _cube(
                    ["vsme:Item"],
                    {
                        "vsme:AxisOne": ["vsme:MemberA", "vsme:MemberB"],
                        "vsme:AxisTwo": ["vsme:MemberX", "vsme:MemberY"],
                    },
                    typed_dimensions=["vsme:TypedAxis"],
                )
            },
            "_defaults": {
                "vsme:AxisOne": "vsme:MemberA",
                "vsme:AxisTwo": "vsme:MemberX",
            },
        }
        taxonomy = _build_taxonomy("test://build-facts/vary-all", dimensions)
        concepts = reportableConcepts(taxonomy)

        facts, _, emptyDomains, emptySetDomains = buildFacts(
            taxonomy, concepts, PERIOD, nil=False
        )

        assert emptyDomains == []
        assert emptySetDomains == []

        dimensionsSeen = [fact["dimensions"] for fact in facts.values()]

        proofOfReportability = {
            "concept": "vsme:Item",
            "entity": PERIOD.entity,
            "period": PERIOD.periodDuration,
            "vsme:TypedAxis": "TypedAxis sample value",
        }
        assert proofOfReportability in dimensionsSeen

        variesAll = {
            "concept": "vsme:Item",
            "entity": PERIOD.entity,
            "period": PERIOD.periodDuration,
            "vsme:AxisOne": "vsme:MemberB",
            "vsme:AxisTwo": "vsme:MemberY",
            "vsme:TypedAxis": "TypedAxis typed member 1",
        }
        assert dimensionsSeen.count(variesAll) == 1

        # proof-of-reportability + one fact per explicit axis's non-default
        # member + two typed sample rows + the one combinatorial fact.
        assert len(facts) == 6

    def test_no_extra_fact_when_only_one_dimension_can_vary(self) -> None:
        dimensions = {
            "role-1": {
                "vsme:Table": _cube(
                    ["vsme:Item"],
                    {"vsme:AxisOne": ["vsme:MemberA", "vsme:MemberB"]},
                )
            },
            "_defaults": {"vsme:AxisOne": "vsme:MemberA"},
        }
        taxonomy = _build_taxonomy("test://build-facts/single-dimension", dimensions)
        concepts = reportableConcepts(taxonomy)

        facts, _, _, _ = buildFacts(taxonomy, concepts, PERIOD, nil=False)

        # proof-of-reportability + the one non-default member -- no combinatorial
        # fact, since there is only one dimension to vary in the first place.
        assert len(facts) == 2


class TestExtraUnitFact:
    def test_narrowly_ambiguous_unit_gets_one_extra_fact(self) -> None:
        # electricChargeItemType is a real UTR entry with exactly two permitted
        # units (Ah, C), matched by local name regardless of namespace -- see
        # UTR.getUnitsForDataType() -- and has no PREFERRED_UNIT_IDS entry, so
        # this exercises the real UTR-ambiguity relaxation end to end rather
        # than via a FakeUTR.
        taxonomy = _build_taxonomy(
            "test://build-facts/extra-unit",
            dimensions={},
            extra_concepts={
                "vsme:Charge": _concept(
                    numeric=True,
                    data_type="vsme:electricChargeItemType",
                    base_data_type="xbrli:decimalItemType",
                )
            },
        )
        concept = taxonomy.getConcept("vsme:Charge")
        permitted = sorted(taxonomy.UTR.getUnitsForDataType(concept.dataType))
        assert len(permitted) == 2

        concepts = reportableConcepts(taxonomy)
        facts, _, _, _ = buildFacts(taxonomy, concepts, PERIOD, nil=False)

        chargeFacts = [
            fact["dimensions"]
            for fact in facts.values()
            if fact["dimensions"]["concept"] == "vsme:Charge"
        ]
        assert len(chargeFacts) == 2
        unitsSeen = {dims["unit"] for dims in chargeFacts}
        assert unitsSeen == {str(permitted[0]), str(permitted[1])}


class TestTypedDimensionValueIsTypeAware:
    def test_non_string_typed_dimension_gets_a_type_aware_value(self) -> None:
        dimensions = {
            "role-1": {
                "vsme:Table": _cube(
                    ["vsme:Item"], typed_dimensions=["vsme:DateTypedAxis"]
                )
            },
        }
        taxonomy = _build_taxonomy(
            "test://build-facts/typed-date",
            dimensions,
            extra_concepts={
                "vsme:DateTypedAxis": _concept(
                    dimension=True, other={"typedElement": "vsme:DTYP"}
                )
            },
            extra_xs_elements={
                "vsme:DTYP": {"dataType": "xs:date", "baseDataType": "xs:date"}
            },
        )
        concepts = reportableConcepts(taxonomy)

        facts, _, _, _ = buildFacts(taxonomy, concepts, PERIOD, nil=False)
        itemFacts = [
            fact["dimensions"]
            for fact in facts.values()
            if fact["dimensions"]["concept"] == "vsme:Item"
        ]
        typedValues = {dims["vsme:DateTypedAxis"] for dims in itemFacts}

        # Every value written for this typed dimension is itself a legal
        # xs:date lexical value -- the placeholder plus one per varied row --
        # not the blind "<name> sample value"/"<name> typed member N" string a
        # string-typed dimension gets.
        assert typedValues == {"2026-01-01", "2026-01-02", "2026-01-03"}
