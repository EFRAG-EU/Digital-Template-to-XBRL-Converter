"""No two facts of a coverage report share a context, and none carries a unit the UTR forbids.

The unit tests here use fakes for the pure decisions (rotation, shifted periods, composite
units). The whole-report tests run the generator over every built-in taxonomy and check the
two properties directly, and the round trip pushes a small generated report through the
strict xBRL-JSON reader, which refuses an instant that is not at the start or end of a declared
duration.
"""

from __future__ import annotations

import json
from collections import Counter

import pytest
from tests.unitTests.coverage_report_generator.test_sampling import (
    FakeConcept,
    FakeQName,
    FakeTaxonomy,
    FakeUTR,
)

from mireport.conversionresults import ConversionResultsBuilder
from mireport.coverage_report_generator._sampling import (
    SampleEntityPeriod,
    _Rotation,
    _Variant,
    buildFacts,
    compositeUnitFor,
    coreDimensionsFor,
    reportableConcepts,
)
from mireport.exceptions import SampleGenerationException
from mireport.taxonomy import (
    Taxonomy,
    getTaxonomy,
    listTaxonomies,
    loadBuiltInTaxonomyJSON,
)
from mireport.xbrljson_reader import XbrlJsonProcessor

PERIOD = SampleEntityPeriod(
    entity="lei:529900T8BM49AURSDO55",
    entityPrefix="lei",
    entityNamespace="http://standards.iso.org/iso/17442",
    periodInstant="2027-01-01T00:00:00",
    periodDuration="2026-01-01T00:00:00/2027-01-01T00:00:00",
)


class TestShiftedPeriod:
    def test_a_year_earlier_keeps_the_oim_form(self) -> None:
        shifted = PERIOD.shifted(1)
        assert shifted.periodInstant == "2026-01-01T00:00:00"
        assert shifted.periodDuration == "2025-01-01T00:00:00/2026-01-01T00:00:00"
        assert shifted.entity == PERIOD.entity

    def test_no_shift_is_the_same_period(self) -> None:
        assert PERIOD.shifted(0) is PERIOD

    def test_a_29_february_becomes_the_28th(self) -> None:
        leap = SampleEntityPeriod(
            entity="lei:X",
            entityPrefix="lei",
            entityNamespace="http://standards.iso.org/iso/17442",
            periodInstant="2028-02-29T00:00:00",
            periodDuration="2027-02-28T00:00:00/2028-02-29T00:00:00",
        )
        shifted = leap.shifted(1)
        assert shifted.periodInstant == "2027-02-28T00:00:00"
        assert shifted.periodDuration == "2026-02-28T00:00:00/2027-02-28T00:00:00"

    def test_the_end_of_a_day_written_as_24_hundred_hours_is_kept_so(self) -> None:
        # the xBRL-JSON reader's tests write the sample instant this way, and Python's
        # parser refuses T24:00:00 before 3.14
        period = SampleEntityPeriod(
            entity="lei:X",
            entityPrefix="lei",
            entityNamespace="http://standards.iso.org/iso/17442",
            periodInstant="2025-12-31T24:00:00",
            periodDuration="2025-01-01T00:00:00/2026-01-01T00:00:00",
        )
        shifted = period.shifted(2)
        assert shifted.periodInstant == "2023-12-31T24:00:00"
        assert shifted.periodDuration == "2023-01-01T00:00:00/2024-01-01T00:00:00"


class TestRotation:
    UNITS = (_Variant(), _Variant(unit=FakeQName("utr", "t")))

    def test_the_first_fact_shows_the_usual_variant_then_each_next_one(self) -> None:
        rotation = _Rotation(list(self.UNITS))
        assert rotation.take({"concept": "c", "a": "1"}) == self.UNITS[0]
        assert rotation.take({"concept": "c", "a": "2"}) == self.UNITS[1]
        assert rotation.take({"concept": "c", "a": "3"}) == self.UNITS[0]  # round again

    def test_a_context_already_covered_is_skipped_and_costs_no_variant(self) -> None:
        rotation = _Rotation(list(self.UNITS))
        assert rotation.take({"concept": "c", "a": "1"}) is not None
        assert rotation.take({"concept": "c", "a": "1"}) is None
        assert rotation.take({"concept": "c", "a": "2"}) == self.UNITS[1]

    def test_facts_that_differ_only_in_unit_share_a_context(self) -> None:
        rotation = _Rotation(list(self.UNITS))
        assert rotation.take({"concept": "c", "unit": "utr:kg"}) is not None
        assert rotation.take({"concept": "c", "unit": "utr:t"}) is None

    def test_variants_no_fact_reached_are_left_over(self) -> None:
        rotation = _Rotation([*self.UNITS, _Variant(emptySet=True)])
        rotation.take({"concept": "c", "a": "1"})
        assert rotation.uncovered() == [self.UNITS[1], _Variant(emptySet=True)]
        rotation.take({"concept": "c", "a": "2"})
        rotation.take({"concept": "c", "a": "3"})
        assert rotation.uncovered() == []


def _composite_concept() -> FakeConcept:
    return FakeConcept(
        qname=FakeQName("esrs", "Intensity"),
        isNumeric=True,
        dataTypeLocalName="ghgEmissionsPerMonetaryItemType",
    )


def _composite_taxonomy() -> FakeTaxonomy:
    tco2e, eur = FakeQName("utr", "tCO2e"), FakeQName("iso4217", "EUR")
    return FakeTaxonomy(
        UTR=FakeUTR(
            unitByPreferredId={"tCO2e": tco2e, "EUR": eur},
            composites={
                "ghgEmissionsPerMonetaryItemType": (
                    FakeQName("utr", "ghgEmissionsItemType"),
                    FakeQName("xbrli", "monetaryItemType"),
                )
            },
            permittedByType={
                "ghgEmissionsItemType": frozenset({tco2e}),
                "monetaryItemType": frozenset({eur}),
            },
        )
    )


class TestCompositeUnits:
    def test_a_per_unit_type_gets_a_divide_unit_of_each_sides_preferred_unit(
        self,
    ) -> None:
        taxonomy = _composite_taxonomy()
        numerator, denominator = compositeUnitFor(taxonomy, _composite_concept())  # type: ignore[misc]
        assert (str(numerator), str(denominator)) == ("utr:tCO2e", "iso4217:EUR")

    def test_the_fact_is_written_in_that_unit_not_left_unitless(self) -> None:
        dimensions, used = coreDimensionsFor(
            _composite_taxonomy(), _composite_concept(), PERIOD
        )
        assert dimensions["unit"] == "utr:tCO2e/iso4217:EUR"
        assert FakeQName("iso4217", "EUR") in used

    def test_a_concept_with_its_own_documented_unit_is_not_composite(self) -> None:
        concept = FakeConcept(
            qname=FakeQName("esrs", "Intensity"),
            isNumeric=True,
            dataTypeLocalName="ghgEmissionsPerMonetaryItemType",
            requiredUnits=frozenset({FakeQName("utr", "x")}),
        )
        assert compositeUnitFor(_composite_taxonomy(), concept) is None

    def test_a_side_with_no_preferred_unit_raises_rather_than_guessing(self) -> None:
        taxonomy = FakeTaxonomy(
            UTR=FakeUTR(
                composites={
                    "ghgEmissionsPerMonetaryItemType": (
                        FakeQName("utr", "mysteryItemType"),
                        FakeQName("xbrli", "monetaryItemType"),
                    )
                },
                permittedByType={},
            )
        )
        with pytest.raises(SampleGenerationException):
            compositeUnitFor(taxonomy, _composite_concept())


def _contexts(facts: dict[str, dict]) -> Counter[tuple]:
    """Each fact's context: everything that identifies it but its value and its unit."""
    return Counter(
        tuple(sorted((k, v) for k, v in fact["dimensions"].items() if k != "unit"))
        for fact in facts.values()
    )


def _builtIn() -> list[pytest.ParameterSet]:
    if not listTaxonomies():
        loadBuiltInTaxonomyJSON()
    return [
        pytest.param(entryPoints, id="+".join(sorted(entryPoints))[-60:])
        for entryPoints in sorted(listTaxonomies(), key=lambda e: sorted(e))
    ]


@pytest.mark.parametrize("entryPoints", _builtIn())
@pytest.mark.parametrize("nil", [False, True], ids=["values", "nil"])
def test_a_built_in_taxonomys_report_has_no_repeated_context_and_valid_units(
    entryPoints: frozenset[str], nil: bool
) -> None:
    taxonomy: Taxonomy = getTaxonomy(entryPoints)
    facts, _, _, _ = buildFacts(taxonomy, reportableConcepts(taxonomy), PERIOD, nil=nil)

    repeated = [ctx for ctx, n in _contexts(facts).items() if n > 1]
    assert repeated == []

    numeric = {c.qname: c for c in taxonomy.concepts if c.isNumeric}
    for fact in facts.values():
        concept = numeric.get(
            taxonomy.QNameMaker.fromString(fact["dimensions"]["concept"])
        )
        if concept is None:
            continue
        unit = fact["dimensions"].get("unit")
        composite = taxonomy.UTR.compositeFor(concept.dataType)
        if (
            composite is not None
            and not taxonomy.UTR.getUnitsForDataType(concept.dataType)
            and concept.getRequiredUnitQNames() is None
        ):
            # a per-unit type has no named units and may not be reported without a unit
            assert unit is not None and "/" in unit, concept.qname
            numerator, denominator = (
                taxonomy.QNameMaker.fromString(part) for part in unit.split("/")
            )
            assert taxonomy.UTR.validComposite(concept.dataType, numerator, denominator)
        elif unit is not None:
            assert taxonomy.UTR.valid(
                concept.dataType, taxonomy.QNameMaker.fromString(unit)
            ), (concept.qname, unit)


def test_variants_that_need_their_own_context_are_in_earlier_periods() -> None:
    """In a built-in taxonomy a concept with no dimension and several units has its
    alternative units in a period of their own, never beside its main fact."""
    if not listTaxonomies():
        loadBuiltInTaxonomyJSON()
    taxonomy = getTaxonomy(
        next(e for e in listTaxonomies() if any("/vsme/2026-05-01/" in u for u in e))
    )
    facts, _, _, _ = buildFacts(
        taxonomy, reportableConcepts(taxonomy), PERIOD, nil=False
    )
    byConcept: dict[str, list[dict]] = {}
    for fact in facts.values():
        byConcept.setdefault(fact["dimensions"]["concept"], []).append(fact)

    units = byConcept["vsme:TotalSealedArea"]  # ha and sqkm, no dimension
    assert len(units) == 2
    mainPeriod, extraPeriod = (f["dimensions"]["period"] for f in units)
    assert mainPeriod == PERIOD.periodDuration
    assert extraPeriod == PERIOD.shifted(1).periodDuration
    assert {f["dimensions"]["unit"] for f in units} == {"utr:ha", "utr:sqkm"}


def test_a_generated_report_goes_through_the_strict_reader() -> None:
    """The reader declares every duration the facts use and needs each instant to sit at the
    start or end of one; a report with variants in earlier periods must still satisfy it."""
    if not listTaxonomies():
        loadBuiltInTaxonomyJSON()
    entryPoints = next(
        e for e in listTaxonomies() if any("/vsme/2026-05-01/" in u for u in e)
    )
    taxonomy = getTaxonomy(entryPoints)
    concepts = [
        c
        for c in reportableConcepts(taxonomy)
        if c.qname.localName
        in {
            "TotalSealedArea",
            "AmountOfEmissionToAir",
            "NaceSectorClassificationCodes",
            "Scope1AndScope2GreenhouseGasEmissionsIntensityValueLocationBased",
        }
    ]
    facts, namespaces, _, _ = buildFacts(taxonomy, concepts, PERIOD, nil=False)
    document = {
        "documentInfo": {
            "documentType": "https://xbrl.org/2021/xbrl-json",
            "features": {"xbrl:canonicalValues": True},
            "namespaces": namespaces,
            "taxonomy": sorted(entryPoints),
        },
        "facts": facts,
    }
    report = XbrlJsonProcessor(
        json.loads(json.dumps(document)), ConversionResultsBuilder(), strict=True
    ).createReport()
    assert report.getInlineReport().fileContent
