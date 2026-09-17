"""Unit tests for coverage_report_generator._sampling's pure decision logic.

Fakes stand in for Concept/QName/Taxonomy rather than baking a real taxonomy: the
logic under test (dimension intersection, representative-value picking, sample-value
resolution) only needs the handful of attributes it actually reads.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pytest

from mireport.coverage_report_generator._sampling import (
    SampleEntityPeriod,
    buildDimensionValues,
    buildFactDimensions,
    domainForDimension,
    pickExplicitRepresentative,
    typedDimensionSampleValue,
    unitFor,
    valueFor,
)
from mireport.exceptions import SampleGenerationException
from mireport.taxonomy import PeriodType


@dataclass(frozen=True)
class FakeQName:
    prefix: str
    localName: str
    namespace: str = "http://example.com/ns"

    def __str__(self) -> str:
        return f"{self.prefix}:{self.localName}"

    def __lt__(self, other: FakeQName) -> bool:
        return str(self) < str(other)


@dataclass(frozen=True)
class FakeConcept:
    qname: FakeQName
    isEnumerationSingle: bool = False
    isEnumerationSet: bool = False
    isNumeric: bool = False
    eeDomain: frozenset[FakeConcept] = field(default_factory=frozenset)
    dataTypeLocalName: str = "stringItemType"
    baseDataTypeLocalName: str = "stringItemType"
    periodType: PeriodType = PeriodType.Duration

    def getEEDomain(self) -> frozenset[FakeConcept]:
        return self.eeDomain

    @property
    def dataType(self) -> FakeQName:
        return FakeQName("xbrli", self.dataTypeLocalName)

    @property
    def baseDataType(self) -> FakeQName:
        return FakeQName("xbrli", self.baseDataTypeLocalName)

    def __lt__(self, other: FakeConcept) -> bool:
        return self.qname < other.qname


def member(prefix: str, name: str) -> FakeConcept:
    return FakeConcept(qname=FakeQName(prefix, name))


class TestValueForEnumerations:
    def test_enumeration_single_empty_domain_stays_nil(self) -> None:
        concept = FakeConcept(qname=FakeQName("esrs", "C"), isEnumerationSingle=True)
        assert valueFor(concept) == (None, None, frozenset())

    def test_enumeration_single_picks_one_member(self) -> None:
        m1, m2 = member("esrs", "MemberA"), member("esrs", "MemberB")
        concept = FakeConcept(
            qname=FakeQName("esrs", "C"),
            isEnumerationSingle=True,
            eeDomain=frozenset({m1, m2}),
        )
        value, decimals, qnames = valueFor(concept)
        assert value == "esrs:MemberA"
        assert decimals is None
        assert qnames == frozenset({m1.qname})

    def test_enumeration_set_empty_domain_is_valid_empty_value_not_nil(self) -> None:
        concept = FakeConcept(qname=FakeQName("esrs", "C"), isEnumerationSet=True)
        assert valueFor(concept) == ("", None, frozenset())

    def test_enumeration_set_single_member_domain_uses_just_that_one(self) -> None:
        m1 = member("esrs", "MemberA")
        concept = FakeConcept(
            qname=FakeQName("esrs", "C"),
            isEnumerationSet=True,
            eeDomain=frozenset({m1}),
        )
        assert valueFor(concept) == ("esrs:MemberA", None, frozenset({m1.qname}))

    def test_enumeration_set_gets_two_members_sorted_by_qname_string(self) -> None:
        # Deliberately out of alphabetical order in the domain, and spanning two
        # prefixes, to prove the value is sorted by the *string* form (as Arelle's
        # canonical-order check compares) and not by domain/member insertion order.
        early = FakeConcept(qname=FakeQName("aaa", "Zzz"))
        late = FakeConcept(qname=FakeQName("zzz", "Aaa"))
        concept = FakeConcept(
            qname=FakeQName("esrs", "C"),
            isEnumerationSet=True,
            eeDomain=frozenset({late, early}),
        )
        value, decimals, qnames = valueFor(concept)
        assert value == "aaa:Zzz zzz:Aaa"
        assert decimals is None
        assert qnames == frozenset({early.qname, late.qname})


class TestValueForOtherTypes:
    def test_declared_type_override_wins_over_base_type(self) -> None:
        concept = FakeConcept(
            qname=FakeQName("esrs", "C"),
            isNumeric=True,
            dataTypeLocalName="percentItemType",
            baseDataTypeLocalName="decimalItemType",
        )
        assert valueFor(concept) == ("0.5", 2, frozenset())

    def test_falls_back_to_base_type_value(self) -> None:
        concept = FakeConcept(
            qname=FakeQName("esrs", "C"),
            isNumeric=True,
            dataTypeLocalName="monetaryItemType",
            baseDataTypeLocalName="monetaryItemType",
        )
        assert valueFor(concept) == ("1.0", 0, frozenset())

    def test_non_numeric_base_type_gets_no_decimals(self) -> None:
        concept = FakeConcept(
            qname=FakeQName("esrs", "C"),
            isNumeric=False,
            dataTypeLocalName="booleanItemType",
            baseDataTypeLocalName="booleanItemType",
        )
        assert valueFor(concept) == ("true", None, frozenset())

    def test_unknown_base_type_raises(self) -> None:
        concept = FakeConcept(
            qname=FakeQName("esrs", "C"),
            dataTypeLocalName="mysteryItemType",
            baseDataTypeLocalName="mysteryItemType",
        )
        with pytest.raises(SampleGenerationException):
            valueFor(concept)


class TestTypedDimensionSampleValue:
    def test_row_zero_is_the_fixed_placeholder(self) -> None:
        dim = member("esrs", "TypedAxis")
        assert typedDimensionSampleValue(dim, 0) == "TypedAxis sample value"

    def test_later_rows_are_distinct_from_each_other(self) -> None:
        dim = member("esrs", "TypedAxis")
        assert typedDimensionSampleValue(dim, 1) == "TypedAxis typed member 1"
        assert typedDimensionSampleValue(dim, 2) == "TypedAxis typed member 2"
        assert typedDimensionSampleValue(dim, 1) != typedDimensionSampleValue(dim, 2)


class FakeUTR:
    def __init__(
        self,
        *,
        permitted: frozenset[FakeQName] = frozenset(),
        unitByPreferredId: dict[str, FakeQName] | None = None,
        preferredIsValid: bool = True,
    ) -> None:
        self._permitted = permitted
        self._unitByPreferredId = unitByPreferredId or {}
        self._preferredIsValid = preferredIsValid

    def getUnitsForDataType(self, dataType: FakeQName) -> frozenset[FakeQName]:
        return self._permitted

    def getQNameForUnitId(self, unitId: str) -> FakeQName | None:
        return self._unitByPreferredId.get(unitId)

    def valid(self, dataType: FakeQName, unit: FakeQName) -> bool:
        return self._preferredIsValid


@dataclass
class FakeTaxonomy:
    UTR: FakeUTR


class TestUnitFor:
    def test_non_numeric_concept_takes_no_unit(self) -> None:
        concept = FakeConcept(qname=FakeQName("esrs", "C"), isNumeric=False)
        assert unitFor(FakeTaxonomy(UTR=FakeUTR()), concept) is None

    def test_utr_unconstrained_type_takes_no_unit(self) -> None:
        concept = FakeConcept(
            qname=FakeQName("esrs", "C"),
            isNumeric=True,
            dataTypeLocalName="percentItemType",
        )
        taxonomy = FakeTaxonomy(UTR=FakeUTR(permitted=frozenset()))
        assert unitFor(taxonomy, concept) is None

    def test_utr_constrained_type_with_no_preferred_id_raises(self) -> None:
        concept = FakeConcept(
            qname=FakeQName("esrs", "C"),
            isNumeric=True,
            dataTypeLocalName="lengthItemType",
        )
        taxonomy = FakeTaxonomy(
            UTR=FakeUTR(permitted=frozenset({FakeQName("utr", "m")}))
        )
        with pytest.raises(SampleGenerationException):
            unitFor(taxonomy, concept)

    def test_preferred_unit_not_utr_valid_raises(self) -> None:
        concept = FakeConcept(
            qname=FakeQName("esrs", "C"),
            isNumeric=True,
            dataTypeLocalName="monetaryItemType",
        )
        eur = FakeQName("iso4217", "EUR")
        taxonomy = FakeTaxonomy(
            UTR=FakeUTR(
                permitted=frozenset({eur}),
                unitByPreferredId={"EUR": eur},
                preferredIsValid=False,
            )
        )
        with pytest.raises(SampleGenerationException):
            unitFor(taxonomy, concept)

    def test_preferred_valid_unit_is_returned(self) -> None:
        concept = FakeConcept(
            qname=FakeQName("esrs", "C"),
            isNumeric=True,
            dataTypeLocalName="monetaryItemType",
        )
        eur = FakeQName("iso4217", "EUR")
        taxonomy = FakeTaxonomy(
            UTR=FakeUTR(permitted=frozenset({eur}), unitByPreferredId={"EUR": eur})
        )
        assert unitFor(taxonomy, concept) == eur


@dataclass
class FakeExplicitDimensionSignature:
    dimension: FakeConcept
    domain: frozenset[FakeConcept]


@dataclass
class FakeHypercubeDeclaration:
    # A tuple, not a frozenset: only iteration is needed, and FakeExplicitDimensionSignature
    # is not itself hashable (it holds a frozenset field), so a set literal of them would not
    # construct.
    explicitDimensions: tuple[FakeExplicitDimensionSignature, ...]


class TestDomainForDimension:
    def test_single_hypercube_returns_its_domain_sorted(self) -> None:
        dim = member("esrs", "Axis")
        m1, m2 = member("esrs", "Zeta"), member("esrs", "Alpha")
        hc = FakeHypercubeDeclaration(
            explicitDimensions=(
                FakeExplicitDimensionSignature(
                    dimension=dim, domain=frozenset({m1, m2})
                ),
            )
        )
        assert domainForDimension(dim, [hc]) == [m2, m1]

    def test_two_hypercubes_intersect_not_union(self) -> None:
        # XBRL Dimensions 1.0 section 3.1.2: every hypercube declaring the same
        # dimension within one EffectiveHypercube is conjoined, so the valid domain
        # is the intersection of what each declares, not their union.
        dim = member("esrs", "Axis")
        common, onlyInFirst, onlyInSecond = (
            member("esrs", "Common"),
            member("esrs", "OnlyFirst"),
            member("esrs", "OnlySecond"),
        )
        hc1 = FakeHypercubeDeclaration(
            explicitDimensions=(
                FakeExplicitDimensionSignature(
                    dimension=dim, domain=frozenset({common, onlyInFirst})
                ),
            )
        )
        hc2 = FakeHypercubeDeclaration(
            explicitDimensions=(
                FakeExplicitDimensionSignature(
                    dimension=dim, domain=frozenset({common, onlyInSecond})
                ),
            )
        )
        assert domainForDimension(dim, [hc1, hc2]) == [common]


class TestPickExplicitRepresentative:
    def test_dimension_with_default_uses_default_even_if_not_first_member(self) -> None:
        dim = member("esrs", "Axis")
        default, other = member("esrs", "Beta"), member("esrs", "Alpha")
        representative = pickExplicitRepresentative(
            membersByDim={dim: [other, default]}, defaultByDim={dim: default}
        )
        assert representative == {dim: default}

    def test_dimension_without_default_uses_first_member(self) -> None:
        dim = member("esrs", "Axis")
        first, second = member("esrs", "Alpha"), member("esrs", "Beta")
        representative = pickExplicitRepresentative(
            membersByDim={dim: [first, second]}, defaultByDim={dim: None}
        )
        assert representative == {dim: first}

    def test_dimension_with_no_members_is_omitted(self) -> None:
        dim = member("esrs", "Axis")
        representative = pickExplicitRepresentative(
            membersByDim={dim: []}, defaultByDim={dim: None}
        )
        assert representative == {}


class TestBuildDimensionValues:
    def test_explicit_dimension_at_its_default_is_omitted(self) -> None:
        dim = member("esrs", "Axis")
        default = member("esrs", "DefaultMember")
        dimensions, used = buildDimensionValues(
            explicitChosen={dim: default},
            defaultByDim={dim: default},
            typedChosen={},
        )
        assert dimensions == {}
        assert used == set()

    def test_explicit_dimension_not_at_default_is_written_and_tracked(self) -> None:
        dim = member("esrs", "Axis")
        default, chosen = member("esrs", "DefaultMember"), member("esrs", "OtherMember")
        dimensions, used = buildDimensionValues(
            explicitChosen={dim: chosen},
            defaultByDim={dim: default},
            typedChosen={},
        )
        assert dimensions == {"esrs:Axis": "esrs:OtherMember"}
        assert used == {dim.qname, chosen.qname}

    def test_typed_dimension_is_always_written_as_a_string_value(self) -> None:
        dim = member("esrs", "TypedAxis")
        dimensions, used = buildDimensionValues(
            explicitChosen={}, defaultByDim={}, typedChosen={dim: "sample value"}
        )
        assert dimensions == {"esrs:TypedAxis": "sample value"}
        assert used == {dim.qname}


class FakeEffectiveHypercube:
    def __init__(
        self, *, roleUri: str = "urn:example:role", valid: bool = True
    ) -> None:
        self.roleUri = roleUri
        self._valid = valid

    def matches(
        self,
        explicitDims: dict[FakeConcept, FakeConcept],
        typedDims: dict[FakeConcept, str],
    ) -> bool:
        return self._valid


SAMPLE_PERIOD = SampleEntityPeriod(
    entity="lei:529900T8BM49AURSDO55",
    entityPrefix="lei",
    entityNamespace="http://standards.iso.org/iso/17442",
    periodInstant="2027-01-01T00:00:00",
    periodDuration="2026-01-01T00:00:00/2027-01-01T00:00:00",
)


class TestBuildFactDimensions:
    def test_no_variation_uses_representative_values_and_omits_defaults(self) -> None:
        concept = FakeConcept(qname=FakeQName("esrs", "C"))
        axis = member("esrs", "Axis")
        default = member("esrs", "DefaultMember")
        dimensions, used = buildFactDimensions(
            FakeTaxonomy(UTR=FakeUTR()),
            concept,
            SAMPLE_PERIOD,
            FakeEffectiveHypercube(),
            {axis: default},
            {axis: default},
            {},
        )
        assert dimensions == {
            "concept": "esrs:C",
            "entity": "lei:529900T8BM49AURSDO55",
            "period": SAMPLE_PERIOD.periodDuration,
        }
        assert used == {concept.qname}

    def test_varied_explicit_dimension_overrides_representative_value(self) -> None:
        concept = FakeConcept(qname=FakeQName("esrs", "C"))
        axis = member("esrs", "Axis")
        default, chosen = member("esrs", "DefaultMember"), member("esrs", "OtherMember")
        dimensions, used = buildFactDimensions(
            FakeTaxonomy(UTR=FakeUTR()),
            concept,
            SAMPLE_PERIOD,
            FakeEffectiveHypercube(),
            {axis: default},
            {axis: default},
            {},
            variedExplicit=axis,
            explicitValue=chosen,
        )
        assert dimensions["esrs:Axis"] == "esrs:OtherMember"
        assert used == {concept.qname, axis.qname, chosen.qname}

    def test_varied_typed_dimension_overrides_placeholder(self) -> None:
        concept = FakeConcept(qname=FakeQName("esrs", "C"))
        typedAxis = member("esrs", "TypedAxis")
        dimensions, _ = buildFactDimensions(
            FakeTaxonomy(UTR=FakeUTR()),
            concept,
            SAMPLE_PERIOD,
            FakeEffectiveHypercube(),
            {},
            {},
            {typedAxis: "placeholder"},
            variedTyped=typedAxis,
            typedValue="chosen value",
        )
        assert dimensions["esrs:TypedAxis"] == "chosen value"

    def test_raises_when_generated_dimensions_do_not_match_the_hypercube(self) -> None:
        concept = FakeConcept(qname=FakeQName("esrs", "C"))
        with pytest.raises(SampleGenerationException):
            buildFactDimensions(
                FakeTaxonomy(UTR=FakeUTR()),
                concept,
                SAMPLE_PERIOD,
                FakeEffectiveHypercube(valid=False),
                {},
                {},
                {},
            )
