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
    extraUnitsFor,
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
class FakeTypedElement:
    baseDataTypeLocalName: str

    @property
    def baseDataType(self) -> FakeQName:
        return FakeQName("xs", self.baseDataTypeLocalName)


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
    typedElementBaseType: str | None = "string"
    requiredUnits: frozenset[FakeQName] | None = None

    def getEEDomain(self) -> frozenset[FakeConcept]:
        return self.eeDomain

    def getRequiredUnitQNames(self) -> frozenset[FakeQName] | None:
        return self.requiredUnits

    @property
    def dataType(self) -> FakeQName:
        return FakeQName("xbrli", self.dataTypeLocalName)

    @property
    def baseDataType(self) -> FakeQName:
        return FakeQName("xbrli", self.baseDataTypeLocalName)

    @property
    def typedElement(self) -> FakeTypedElement | None:
        if self.typedElementBaseType is None:
            return None
        return FakeTypedElement(self.typedElementBaseType)

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
    def test_row_zero_is_the_fixed_placeholder_for_string(self) -> None:
        dim = member("esrs", "TypedAxis")
        assert typedDimensionSampleValue(dim, 0) == "TypedAxis sample value"

    def test_later_rows_are_distinct_from_each_other_for_string(self) -> None:
        dim = member("esrs", "TypedAxis")
        assert typedDimensionSampleValue(dim, 1) == "TypedAxis typed member 1"
        assert typedDimensionSampleValue(dim, 2) == "TypedAxis typed member 2"
        assert typedDimensionSampleValue(dim, 1) != typedDimensionSampleValue(dim, 2)

    def test_no_typed_element_raises(self) -> None:
        dim = FakeConcept(
            qname=FakeQName("esrs", "TypedAxis"), typedElementBaseType=None
        )
        with pytest.raises(SampleGenerationException):
            typedDimensionSampleValue(dim, 0)

    def test_unmapped_base_type_raises(self) -> None:
        dim = FakeConcept(
            qname=FakeQName("esrs", "TypedAxis"), typedElementBaseType="mysteryType"
        )
        with pytest.raises(SampleGenerationException):
            typedDimensionSampleValue(dim, 0)

    @pytest.mark.parametrize(
        ("baseType", "row", "expected"),
        [
            ("boolean", 0, "true"),
            ("boolean", 1, "false"),
            ("boolean", 2, "true"),
            ("date", 1, "2026-01-02"),
            ("date", 2, "2026-01-03"),
            ("decimal", 1, "2.0"),
            ("decimal", 2, "3.0"),
            ("integer", 1, "1"),
            ("integer", 2, "2"),
            ("gYear", 1, "2027"),
            ("gYear", 2, "2028"),
            ("anyURI", 1, "https://example.org/TypedAxis/1"),
            ("anyURI", 2, "https://example.org/TypedAxis/2"),
        ],
    )
    def test_non_string_base_types_use_canonical_values(
        self, baseType: str, row: int, expected: str
    ) -> None:
        dim = FakeConcept(
            qname=FakeQName("esrs", "TypedAxis"), typedElementBaseType=baseType
        )
        assert typedDimensionSampleValue(dim, row) == expected

    def test_non_string_varied_rows_are_distinct_from_each_other(self) -> None:
        dim = FakeConcept(
            qname=FakeQName("esrs", "TypedAxis"), typedElementBaseType="date"
        )
        assert typedDimensionSampleValue(dim, 1) != typedDimensionSampleValue(dim, 2)

    def test_date_rolls_over_the_month_boundary_instead_of_an_invalid_day(
        self,
    ) -> None:
        # Not a row count TYPED_DIMENSION_SAMPLE_ROWS would ever reach today, but
        # the generator must still produce a calendar-valid date rather than a
        # string like "2026-01-32" if that constant ever grows.
        dim = FakeConcept(
            qname=FakeQName("esrs", "TypedAxis"), typedElementBaseType="date"
        )
        assert typedDimensionSampleValue(dim, 35) == "2026-02-05"


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

    def test_required_unit_wins_over_utr_guessing(self) -> None:
        kg, t = FakeQName("utr", "kg"), FakeQName("utr", "t")
        concept = FakeConcept(
            qname=FakeQName("esrs", "C"),
            isNumeric=True,
            dataTypeLocalName="massItemType",
            requiredUnits=frozenset({kg, t}),
        )
        # The UTR would permit far more than these two, but the taxonomy's own
        # guidance for this concept specifically takes priority.
        taxonomy = FakeTaxonomy(
            UTR=FakeUTR(permitted=frozenset({kg, t, FakeQName("utr", "lb")}))
        )
        assert unitFor(taxonomy, concept) == min(kg, t)

    def test_required_unit_wins_over_preferred_unit_ids(self) -> None:
        usd = FakeQName("iso4217", "USD")
        concept = FakeConcept(
            qname=FakeQName("esrs", "C"),
            isNumeric=True,
            dataTypeLocalName="monetaryItemType",
            requiredUnits=frozenset({usd}),
        )
        taxonomy = FakeTaxonomy(
            UTR=FakeUTR(
                permitted=frozenset({usd, FakeQName("iso4217", "EUR")}),
                unitByPreferredId={"EUR": FakeQName("iso4217", "EUR")},
            )
        )
        assert unitFor(taxonomy, concept) == usd

    def test_utr_unconstrained_type_takes_no_unit(self) -> None:
        concept = FakeConcept(
            qname=FakeQName("esrs", "C"),
            isNumeric=True,
            dataTypeLocalName="percentItemType",
        )
        taxonomy = FakeTaxonomy(UTR=FakeUTR(permitted=frozenset()))
        assert unitFor(taxonomy, concept) is None

    def test_single_permitted_unit_with_no_preferred_id_is_auto_picked(self) -> None:
        concept = FakeConcept(
            qname=FakeQName("esrs", "C"),
            isNumeric=True,
            dataTypeLocalName="lengthItemType",
        )
        metre = FakeQName("utr", "m")
        taxonomy = FakeTaxonomy(UTR=FakeUTR(permitted=frozenset({metre})))
        assert unitFor(taxonomy, concept) == metre

    def test_narrowly_ambiguous_type_with_no_preferred_id_picks_the_lowest(
        self,
    ) -> None:
        concept = FakeConcept(
            qname=FakeQName("esrs", "C"),
            isNumeric=True,
            dataTypeLocalName="lengthItemType",
        )
        metre, foot = FakeQName("utr", "m"), FakeQName("utr", "ft")
        taxonomy = FakeTaxonomy(UTR=FakeUTR(permitted=frozenset({metre, foot})))
        assert unitFor(taxonomy, concept) == min(metre, foot)

    def test_widely_ambiguous_type_with_no_preferred_id_still_raises(self) -> None:
        concept = FakeConcept(
            qname=FakeQName("esrs", "C"),
            isNumeric=True,
            dataTypeLocalName="lengthItemType",
        )
        permitted = frozenset(FakeQName("utr", f"u{i}") for i in range(6))
        taxonomy = FakeTaxonomy(UTR=FakeUTR(permitted=permitted))
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


class TestExtraUnitsFor:
    def test_non_numeric_concept_has_no_extra_units(self) -> None:
        concept = FakeConcept(qname=FakeQName("esrs", "C"), isNumeric=False)
        assert extraUnitsFor(FakeTaxonomy(UTR=FakeUTR()), concept) == []

    def test_required_units_return_every_one_but_the_lowest(self) -> None:
        kg, t = FakeQName("utr", "kg"), FakeQName("utr", "t")
        concept = FakeConcept(
            qname=FakeQName("esrs", "C"),
            isNumeric=True,
            dataTypeLocalName="massItemType",
            requiredUnits=frozenset({kg, t}),
        )
        taxonomy = FakeTaxonomy(UTR=FakeUTR(permitted=frozenset({kg, t})))
        assert extraUnitsFor(taxonomy, concept) == [max(kg, t)]

    def test_single_required_unit_has_no_extra_units(self) -> None:
        kg = FakeQName("utr", "kg")
        concept = FakeConcept(
            qname=FakeQName("esrs", "C"),
            isNumeric=True,
            dataTypeLocalName="massItemType",
            requiredUnits=frozenset({kg}),
        )
        taxonomy = FakeTaxonomy(UTR=FakeUTR(permitted=frozenset({kg})))
        assert extraUnitsFor(taxonomy, concept) == []

    def test_curated_preference_has_no_extra_units(self) -> None:
        concept = FakeConcept(
            qname=FakeQName("esrs", "C"),
            isNumeric=True,
            dataTypeLocalName="monetaryItemType",
        )
        eur, usd = FakeQName("iso4217", "EUR"), FakeQName("iso4217", "USD")
        taxonomy = FakeTaxonomy(
            UTR=FakeUTR(permitted=frozenset({eur, usd}), unitByPreferredId={"EUR": eur})
        )
        assert extraUnitsFor(taxonomy, concept) == []

    def test_single_permitted_unit_has_no_extra_units(self) -> None:
        concept = FakeConcept(
            qname=FakeQName("esrs", "C"),
            isNumeric=True,
            dataTypeLocalName="lengthItemType",
        )
        taxonomy = FakeTaxonomy(
            UTR=FakeUTR(permitted=frozenset({FakeQName("utr", "m")}))
        )
        assert extraUnitsFor(taxonomy, concept) == []

    def test_narrowly_ambiguous_type_returns_every_unit_but_the_lowest(self) -> None:
        concept = FakeConcept(
            qname=FakeQName("esrs", "C"),
            isNumeric=True,
            dataTypeLocalName="lengthItemType",
        )
        metre, foot = FakeQName("utr", "m"), FakeQName("utr", "ft")
        taxonomy = FakeTaxonomy(UTR=FakeUTR(permitted=frozenset({metre, foot})))
        assert extraUnitsFor(taxonomy, concept) == [max(metre, foot)]

    def test_widely_ambiguous_type_has_no_extra_units(self) -> None:
        concept = FakeConcept(
            qname=FakeQName("esrs", "C"),
            isNumeric=True,
            dataTypeLocalName="lengthItemType",
        )
        permitted = frozenset(FakeQName("utr", f"u{i}") for i in range(6))
        taxonomy = FakeTaxonomy(UTR=FakeUTR(permitted=permitted))
        assert extraUnitsFor(taxonomy, concept) == []


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
            explicitOverrides={axis: chosen},
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
            typedOverrides={typedAxis: "chosen value"},
        )
        assert dimensions["esrs:TypedAxis"] == "chosen value"

    def test_multiple_overrides_apply_simultaneously(self) -> None:
        concept = FakeConcept(qname=FakeQName("esrs", "C"))
        axis = member("esrs", "Axis")
        default, chosen = member("esrs", "DefaultMember"), member("esrs", "OtherMember")
        typedAxis = member("esrs", "TypedAxis")
        dimensions, used = buildFactDimensions(
            FakeTaxonomy(UTR=FakeUTR()),
            concept,
            SAMPLE_PERIOD,
            FakeEffectiveHypercube(),
            {axis: default},
            {axis: default},
            {typedAxis: "placeholder"},
            explicitOverrides={axis: chosen},
            typedOverrides={typedAxis: "chosen value"},
        )
        assert dimensions["esrs:Axis"] == "esrs:OtherMember"
        assert dimensions["esrs:TypedAxis"] == "chosen value"
        assert used == {concept.qname, axis.qname, chosen.qname, typedAxis.qname}

    def test_unit_override_replaces_computed_unit(self) -> None:
        concept = FakeConcept(
            qname=FakeQName("esrs", "C"),
            isNumeric=True,
            dataTypeLocalName="monetaryItemType",
        )
        eur = FakeQName("iso4217", "EUR")
        usd = FakeQName("iso4217", "USD")
        dimensions, used = buildFactDimensions(
            FakeTaxonomy(
                UTR=FakeUTR(permitted=frozenset({eur}), unitByPreferredId={"EUR": eur})
            ),
            concept,
            SAMPLE_PERIOD,
            FakeEffectiveHypercube(),
            {},
            {},
            {},
            unitOverride=usd,
        )
        assert dimensions["unit"] == "iso4217:USD"
        assert used == {concept.qname, usd}

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
