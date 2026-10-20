"""
Generate a maximal, dimensionally-valid sample report for an arbitrary taxonomy, as a
Report that mireport.xbrljson_writer writes as xBRL-JSON (OIM): one fact per
reportable concept, plus further facts that individually exercise every hypercube
dimension (explicit or typed) and every enumeration domain a concept carries, so a
consumer can be shown every reportable concept and dimension combination is actually
usable -- not just declared.

Nothing here parses a schema or a linkbase, or hardcodes what a taxonomy's concepts
or dimensions are: everything comes from a Taxonomy already baked by
mireport.arelle.taxonomy_info.callArelleForTaxonomyInfo() and loaded via
mireport.taxonomy.loadTaxonomyJSON(). The entity, period(s) and output shape a sample
report needs are inherently choices about the *sample*, not something this module can
sensibly default, so callers supply them via SampleEntityPeriod.
"""

from __future__ import annotations

import functools
from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta
from decimal import Decimal
from types import MappingProxyType
from typing import TYPE_CHECKING, NamedTuple

from mireport.exceptions import InlineReportException, SampleGenerationException
from mireport.report.factbuilder import FactBuilder, enumerationText
from mireport.report.model import Unit
from mireport.report.periods import (
    DurationPeriodHolder,
    InstantPeriodHolder,
    _Period,
)
from mireport.report.report import Report
from mireport.taxonomy import HypercubeType, PeriodType
from mireport.typealiases import DecimalPlaces
from mireport.xml import XBRLI_NS

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Mapping

    from mireport.taxonomy import (
        Concept,
        EffectiveHypercube,
        HypercubeDeclaration,
        Taxonomy,
    )
    from mireport.xml import QName

# Preferred UTR unit per item type, for the types the UTR constrains. Asserted against
# the UTR before use, so this expresses a preference and never a claim about validity:
# monetaryItemType alone admits 185 units, so there is nothing to infer and picking
# arbitrarily would be meaningless. A numeric type the UTR constrains but that is
# missing here raises SampleGenerationException rather than guessing.
PREFERRED_UNIT_IDS: dict[str, str] = {
    "monetaryItemType": "EUR",
    "ghgEmissionsItemType": "tCO2e",
    "massItemType": "t",
    "energyItemType": "MWh",
    "volumeItemType": "m3",
    "areaItemType": "ha",
}

# Above this many UTR-permitted units, a data type with no PREFERRED_UNIT_IDS
# entry still needs one curated rather than guessed at: monetaryItemType alone
# admits 185, and enumerating "one extra fact per unit" for that many would be
# the combinatorial explosion this module otherwise avoids, not a proof of
# usability. At or below it, unitFor()/extraUnitsFor() pick for themselves.
UNIT_AMBIGUITY_CUTOFF = 5

# Sample value keyed on the *base* data type, because canonical lexical form is a
# property of the base type: canonical xs:decimal requires a fractional digit, so
# decimal-derived types cannot use a bare "1". A report built with these values and
# xbrl:canonicalValues on will have Arelle enforce this.
VALUE_BY_BASE_TYPE: dict[str, str] = {
    "monetaryItemType": "1.0",
    "decimalItemType": "1.0",
    "pureItemType": "1.0",
    "integerItemType": "1",
    "booleanItemType": "true",
    "dateItemType": "2026-12-31",
    "gYearItemType": "2026",
    "stringItemType": "Sample disclosure text.",
}

# Overrides keyed on the *declared* type, where the base-type value is legal but reads
# oddly. 1.0 as a percentage means 100%; 0.5 is easier to recognise in a sample.
VALUE_BY_DECLARED_TYPE: dict[str, tuple[str, int]] = {
    "percentItemType": ("0.5", 2),
}

DEFAULT_DECIMALS = 0

# A typed dimension has no domain to enumerate -- its value is arbitrary content
# conforming to its typed element, not a domain member -- so this many synthetic
# sample values are generated per typed dimension instead of one per member. More
# than one proves that distinct instances (e.g. two different sites) are
# dimensionally valid at once, not just that the dimension accepts *a* value.
TYPED_DIMENSION_SAMPLE_ROWS = 2


def _stringTypedDomainSample(dim: Concept, row: int) -> str:
    if row == 0:
        return f"{dim.qname.localName} sample value"
    return f"{dim.qname.localName} typed member {row}"


# Keyed on TypedDomainWrapperElement.baseDataType's XML Schema primitive local
# name, not an xbrli item type -- hence a separate table from VALUE_BY_BASE_TYPE.
# boolean has only two legal values, so its placeholder (row 0) unavoidably
# repeats one of the two varied rows.
TYPED_DOMAIN_SAMPLE_BY_BASE_TYPE: dict[str, Callable[[Concept, int], str]] = {
    "string": _stringTypedDomainSample,
    "boolean": lambda dim, row: "true" if row % 2 == 0 else "false",
    "date": lambda dim, row: (date(2026, 1, 1) + timedelta(days=row)).isoformat(),
    "decimal": lambda dim, row: f"{row + 1}.0",
    "integer": lambda dim, row: str(row),
    "gYear": lambda dim, row: str(2026 + row),
    "anyURI": lambda dim, row: f"https://example.org/{dim.qname.localName}/{row}",
}

# Same reasoning for an enumeration *set*: one member would leave it indistinguishable
# from an enumeration-single fact, and would never exercise the canonical ordering that
# xbrl:canonicalValues turns on for enumeration-set values (Arelle's oim/Load.py checks
# the written QName tokens are already in ascending string order -- see valueFor()).
ENUMERATION_SET_SAMPLE_MEMBERS = 2


@dataclass(frozen=True)
class SampleEntityPeriod:
    """
    The entity and period(s) to stamp on every generated fact, and the namespace that
    entity's prefix resolves to -- sample-report content, not something this module
    can sensibly default, so every caller supplies its own.

    entity is a full OIM entity identifier string, e.g. "lei:529900T8BM49AURSDO55";
    entityPrefix/entityNamespace declare that prefix (here "lei") against its scheme
    namespace, e.g. "http://standards.iso.org/iso/17442", so the generated document's
    namespaces map is self-consistent.
    """

    entity: str
    entityPrefix: str
    entityNamespace: str
    periodInstant: str
    periodDuration: str

    @property
    def entityIdentifier(self) -> str:
        """The identifier in *entity*, which has to be written with *entityPrefix*."""
        prefix, colon, identifier = self.entity.partition(":")
        if prefix != self.entityPrefix or not (colon and identifier):
            raise SampleGenerationException(
                f"The entity {self.entity!r} is not written as "
                f"{self.entityPrefix}:<identifier>."
            )
        return identifier

    def periods(self) -> tuple[InstantPeriodHolder, DurationPeriodHolder]:
        """The instant and the duration, as periods."""
        try:
            start, end = (
                datetime.fromisoformat(part) for part in self.periodDuration.split("/")
            )
            return (
                InstantPeriodHolder(datetime.fromisoformat(self.periodInstant)),
                DurationPeriodHolder(start, end),
            )
        except (ValueError, InlineReportException) as e:
            raise SampleGenerationException(
                f"Not an OIM instant and duration: {self.periodInstant!r}, "
                f"{self.periodDuration!r} ({e})."
            ) from e

    def shifted(self, years: int) -> SampleEntityPeriod:
        """The same entity and period ``years`` earlier (a 29 February becomes the 28th).

        Variant facts, which must not share a context with the fact they vary, go in
        these earlier periods: see buildFacts()."""
        if years == 0:
            return self
        return replace(
            self,
            periodInstant=_shiftYears(self.periodInstant, years),
            periodDuration="/".join(
                _shiftYears(part, years) for part in self.periodDuration.split("/")
            ),
        )


def _shiftYears(moment: str, years: int) -> str:
    """An OIM dateTime moved ``years`` back, in the same ``YYYY-MM-DDThh:mm:ss`` form."""
    when = datetime.fromisoformat(moment)
    try:
        moved = when.replace(year=when.year - years)
    except ValueError:  # 29 February
        moved = when.replace(year=when.year - years, day=28)
    return moved.isoformat(timespec="seconds")


def unitFor(taxonomy: Taxonomy, concept: Concept) -> QName | None:
    """
    The unit to report *concept* with, or None if it takes no unit dimension.

    A numeric concept whose data type the UTR does not constrain is dimensionless: its
    facts are in xbrli:pure, which OIM writes by omitting the unit dimension.
    percentItemType is the case that matters here: it has no UTR entries.

    Concept.getRequiredUnitQNames() -- the taxonomy's own per-concept measurement
    guidance, parsed from its documentation label, the same signal
    mireport.xlsx_template_reader._units.UnitResolver consults for real
    Excel-sourced facts -- takes priority over anything this module decides for
    itself: it is the taxonomy author's own answer for this concept specifically,
    not an inference from the data type as a whole (in VSME 2026-05-01, roughly
    half of numeric concepts have one). Only when a concept has none does
    PREFERRED_UNIT_IDS's curated default apply, and only when neither applies does
    a narrowly-ambiguous UTR-permitted set (UNIT_AMBIGUITY_CUTOFF or fewer) get
    auto-picked (the lowest by QName's sort order, for a stable choice) -- see
    extraUnitsFor() for how the *other* candidates in either set still get proven
    usable. A wide-open UTR-permitted set with no guidance of any kind still
    raises: guessing among that many is not a reasonable default.
    """
    if not concept.isNumeric:
        return None

    if (required := concept.getRequiredUnitQNames()) is not None:
        return min(required)

    if not taxonomy.UTR.getUnitsForDataType(concept.dataType):
        # Either the UTR does not constrain this type (dimensionless), or it is a
        # type that takes a complex (divide) unit (see complexUnitFor()).
        return None
    return _preferredUnit(taxonomy, concept.dataType, concept)


def _preferredUnit(taxonomy: Taxonomy, dataType: QName, concept: Concept) -> QName:
    """The curated, else the only sensible, UTR unit for *dataType*; raises rather than
    guessing among many. *concept* is only named in the messages."""
    permitted = taxonomy.UTR.getUnitsForDataType(dataType)
    dataTypeName = dataType.localName
    if (unitId := PREFERRED_UNIT_IDS.get(dataTypeName)) is not None:
        unit = taxonomy.UTR.getQNameForUnitId(unitId)
        if unit is None or not taxonomy.UTR.valid(dataType, unit):
            raise SampleGenerationException(
                f"Preferred unit {unitId!r} is not valid for {dataTypeName} "
                f"(concept {concept.qname})."
            )
        return unit

    if not permitted:
        raise SampleGenerationException(
            f"The UTR permits no unit for {dataTypeName} (concept {concept.qname})."
        )
    if len(permitted) > UNIT_AMBIGUITY_CUTOFF:
        raise SampleGenerationException(
            f"No preferred unit for {dataTypeName} (concept {concept.qname}). "
            f"The UTR permits {len(permitted)}; add one to PREFERRED_UNIT_IDS."
        )
    return min(permitted)


def complexUnitFor(taxonomy: Taxonomy, concept: Concept) -> tuple[QName, QName] | None:
    """
    The (numerator, denominator) units to report *concept* in, if its type is a UTR
    complex (emissions per monetary unit, say) and the taxonomy gives it no unit of its
    own; else None.

    Such a type has no named units, so unitFor() returns None for it, but that is not
    "dimensionless": OIM reads a missing unit as xbrli:pure, which the UTR forbids for
    these types. Each side takes the preferred unit of its own item type (see
    PREFERRED_UNIT_IDS), and a side with none still raises rather than guessing.
    """
    if not concept.isNumeric or concept.getRequiredUnitQNames() is not None:
        return None
    if not taxonomy.UTR.requiresComplexUnit(concept.dataType):
        return None
    complexUnit = taxonomy.UTR.complexUnitFor(concept.dataType)
    assert complexUnit is not None
    return (
        _preferredUnit(taxonomy, complexUnit.numerator, concept),
        _preferredUnit(taxonomy, complexUnit.denominator, concept),
    )


def extraUnitsFor(taxonomy: Taxonomy, concept: Concept) -> list[QName]:
    """
    Every unit for *concept* other than the one unitFor() already uses on every
    one of its facts -- for proving each of them individually usable too via one
    extra fact apiece, the same reasoning as TYPED_DIMENSION_SAMPLE_ROWS and the
    explicit-dimension variation loop in buildFacts().

    Mirrors unitFor()'s own precedence: every other unit
    Concept.getRequiredUnitQNames() names, when the taxonomy documents more than
    one for this concept specifically -- not capped by UNIT_AMBIGUITY_CUTOFF,
    since the taxonomy author already curated that set, not this module. Only
    with no such guidance does the UTR-permitted set apply instead, empty when a
    curated PREFERRED_UNIT_IDS entry already stands in for the whole type, or
    when that set is a single unit or too wide-open to enumerate.
    """
    if not concept.isNumeric:
        return []

    if (required := concept.getRequiredUnitQNames()) is not None:
        return sorted(required)[1:]

    dataTypeName = concept.dataType.localName
    if dataTypeName in PREFERRED_UNIT_IDS:
        return []
    permitted = taxonomy.UTR.getUnitsForDataType(concept.dataType)
    if len(permitted) <= 1 or len(permitted) > UNIT_AMBIGUITY_CUTOFF:
        return []
    return sorted(permitted)[1:]


def valueFor(concept: Concept) -> tuple[str | None, int | None, frozenset[Concept]]:
    """
    The sample value, decimals, and enumeration members for *concept*, or (None, None,
    empty) to leave it nil.

    Enumerations resolve first: their base type is a token, so a base-type value would
    be nonsense. An enumeration's value is its domain members' QNames, and the members
    themselves are what the fact holds.

    An enumeration *set* gets ENUMERATION_SET_SAMPLE_MEMBERS members, not one -- a
    single-member value is legal xBRL-JSON but indistinguishable from an
    enumeration-single fact, and Arelle only checks the written value is canonically
    ordered when there is more than one member to order. The QNames are sorted by
    their string form right before joining -- not by however getEEDomain()'s members
    themselves sort -- because Arelle's canonical-order check
    (arelle/oim/Load.py, xbrlje:nonCanonicalValue) compares the written
    prefix:localName tokens as plain strings, which need not agree with a
    namespace-then-local-name ordering if the domain ever spans more than one prefix.

    A domain that resolves to no members is a dead end for an enumeration *single*,
    which has no legal empty value, so it stays nil. An enumeration *set* has no such
    problem: an empty selection is itself a valid, non-nil value (see buildFacts(),
    which adds an extra empty-set fact for concepts whose domain is *not* empty,
    exercising the same value deliberately rather than as a fallback).
    """
    if concept.isEnumerationSingle or concept.isEnumerationSet:
        if not (members := sorted(concept.getEEDomain())):
            if concept.isEnumerationSet:
                return "", None, frozenset()
            return None, None, frozenset()
        sampleSize = ENUMERATION_SET_SAMPLE_MEMBERS if concept.isEnumerationSet else 1
        chosen = members[:sampleSize]
        qnames = sorted(str(member.qname) for member in chosen)
        return " ".join(qnames), None, frozenset(chosen)

    declared = concept.dataType.localName
    if (override := VALUE_BY_DECLARED_TYPE.get(declared)) is not None:
        value, decimals = override
        return value, decimals, frozenset()

    base = concept.baseDataType.localName
    if (baseValue := VALUE_BY_BASE_TYPE.get(base)) is None:
        raise SampleGenerationException(
            f"No sample value for base type {base} (declared {declared}, concept "
            f"{concept.qname}). Add it to VALUE_BY_BASE_TYPE."
        )
    return baseValue, (DEFAULT_DECIMALS if concept.isNumeric else None), frozenset()


def typedDimensionSampleValue(dim: Concept, row: int) -> str:
    """
    The synthetic sample value for typed dimension *dim* at *row* -- 0 for the
    fixed placeholder used whenever *dim* is a co-dimension rather than the one
    under test, else 1..TYPED_DIMENSION_SAMPLE_ROWS for the values that
    individually vary it (see that constant). A typed dimension has no domain to
    enumerate, so unlike an explicit dimension's members these are synthesised
    rather than read off the taxonomy -- but they must still be legal content
    for the dimension's own declared type (XBRL Dimensions 1.0 section 3.1.9.2's
    typed domain element), which is why this dispatches on *dim*.typedElement's
    base type rather than assuming every typed dimension is textual.

    Every base type, including xs:string, is looked up in
    TYPED_DOMAIN_SAMPLE_BY_BASE_TYPE; one with no entry raises
    SampleGenerationException rather than guessing at legal content for a type
    this module has never been told how to synthesise.
    """
    if (element := dim.typedElement) is None:
        raise SampleGenerationException(
            f"Typed dimension {dim.qname} has no typed domain element -- "
            "XBRL Dimensions 1.0 section 3.1.9.2 requires one."
        )
    baseType = element.baseDataType.localName
    if (generator := TYPED_DOMAIN_SAMPLE_BY_BASE_TYPE.get(baseType)) is None:
        raise SampleGenerationException(
            f"No sample value for typed dimension base type {baseType} "
            f"(dimension {dim.qname}). Add it to TYPED_DOMAIN_SAMPLE_BY_BASE_TYPE."
        )
    return generator(dim, row)


def valueAndDecimalsFor(
    concept: Concept, *, nil: bool
) -> tuple[str | None, int | None, frozenset[Concept]]:
    """
    The (value, decimals, enumeration members) triple for one fact of *concept*, or
    (None, None, empty) if nil.
    """
    if nil:
        return None, None, frozenset()
    return valueFor(concept)


def domainForDimension(
    dim: Concept, positives: Iterable[HypercubeDeclaration]
) -> list[Concept]:
    """
    The members valid for *dim* within one EffectiveHypercube: the intersection of
    every positive hypercube in *positives* that declares it (XBRL Dimensions 1.0
    section 3.1.2 conjoins them), not their union.

    Assumes at least one hypercube in *positives* declares *dim* -- true by
    construction at the one call site, where *dim* always comes from the union of
    explicit dimensions those same hypercubes declare.
    """
    domains = [
        ed.domain
        for hc in positives
        for ed in hc.explicitDimensions
        if ed.dimension is dim
    ]
    result = domains[0]
    for other in domains[1:]:
        result = result & other
    return sorted(result)


def pickExplicitRepresentative(
    membersByDim: dict[Concept, list[Concept]],
    defaultByDim: dict[Concept, Concept | None],
) -> dict[Concept, Concept]:
    """
    One representative member per explicit dimension, for use as a co-dimension when
    some other dimension is the one under test: the taxonomy default if there is one
    *and* it is actually a member of this dimension's domain here, else the first
    member by QName. A dimension with no members at all is omitted -- there is no
    representative value to offer, and the caller tracks that separately (an empty
    domain, not a representative choice).

    The default is taxonomy-wide, but *members* is already scoped to one
    EffectiveHypercube (domainForDimension() intersects every positive hypercube
    declaring this dimension there) -- a dimension can be declared in more than one
    base set with a different, non-overlapping domain in each, so the taxonomy
    default is not guaranteed to be a member of *this* one. Using it anyway would
    hand buildFactDimensions() a representative value EffectiveHypercube.matches()
    then rejects, for a dimension that was never actually left at its default here.
    """
    return {
        dim: (
            default
            if (default := defaultByDim[dim]) is not None and default in members
            else members[0]
        )
        for dim, members in membersByDim.items()
        if members
    }


def buildDimensionValues(
    explicitChosen: dict[Concept, Concept],
    defaultByDim: dict[Concept, Concept | None],
    typedChosen: dict[Concept, str],
) -> tuple[dict[Concept, Concept], dict[Concept, str]]:
    """
    The explicit and typed dimensions one fact is written with, from its explicit and
    typed dimension choices.

    An explicit dimension chosen at its own taxonomy default is omitted rather than
    written out: OIM (xBRL-JSON) forbids writing a taxonomy-defined dimension
    explicitly as its own default member (oime:invalidDimensionValue) -- it must be
    represented by omitting the dimension instead.
    """
    explicit = {
        dim: chosen
        for dim, chosen in explicitChosen.items()
        if chosen != defaultByDim[dim]
    }
    return explicit, dict(typedChosen)


def reportableConcepts(taxonomy: Taxonomy) -> list[Concept]:
    """
    Every concept that can carry a fact, in presentation order.

    Groups are ordered by (definition, role URI), as mireport itself orders report
    sections, and concepts keep their order within each group. A concept presented in
    more than one group takes its first position; one presented nowhere is appended,
    sorted by QName.

    isReportable is `not isAbstract`, which is sufficient on its own: XBRL Dimensions
    requires hypercube and dimension declarations to be abstract, so they cannot
    appear here.
    """
    ordered = list(
        dict.fromkeys(
            relationship.concept
            for group in sorted(taxonomy.presentation)
            for relationship in group.relationships
            if relationship.concept.isReportable
        )
    )
    presented = set(ordered)
    ordered.extend(
        sorted(
            concept
            for concept in taxonomy.concepts
            if concept.isReportable and concept not in presented
        )
    )
    return ordered


class FactShape(NamedTuple):
    """Everything about one fact but its value: its concept, unit (None for none), the
    taxonomy dimensions it is written with, and how many years back its period is (see
    SampleEntityPeriod.shifted())."""

    concept: Concept
    unit: Unit | None = None
    explicit: Mapping[Concept, Concept] = MappingProxyType({})
    typed: Mapping[Concept, str] = MappingProxyType({})
    periodShift: int = 0

    @property
    def context(self) -> tuple[object, ...]:
        """What identifies the fact apart from its concept, value and unit: facts that
        differ only in unit share it."""
        return (
            self.periodShift,
            frozenset(self.explicit.items()),
            frozenset(self.typed.items()),
        )


def coreShapeFor(
    taxonomy: Taxonomy,
    concept: Concept,
    *,
    unitOverride: QName | None = None,
    periodShift: int = 0,
) -> FactShape:
    """
    A fact for *concept* with no taxonomy dimension, in its unit.

    *unitOverride* reports the fact with that unit instead of unitFor()'s own choice --
    for exercising a UTR-permitted unit other than the one every other fact uses, not
    for anything unitFor() itself wouldn't already consider valid.

    *periodShift* puts the fact that many years earlier (see SampleEntityPeriod.shifted()).
    The unit is checked against the UTR here, so a fact the UTR would refuse is never
    written.
    """
    unit = unitOverride if unitOverride is not None else unitFor(taxonomy, concept)
    if unit is not None:
        if not taxonomy.UTR.valid(concept.dataType, unit):
            raise SampleGenerationException(
                f"Unit {unit} is not valid in the UTR for {concept.dataType.localName} "
                f"(concept {concept.qname})."
            )
        return FactShape(concept, Unit.simple(unit), periodShift=periodShift)
    if (complexUnit := complexUnitFor(taxonomy, concept)) is not None:
        numerator, denominator = complexUnit
        if not taxonomy.UTR.validComplexUnit(concept.dataType, numerator, denominator):
            raise SampleGenerationException(
                f"Unit {numerator}/{denominator} is not valid in the UTR for "
                f"{concept.dataType.localName} (concept {concept.qname})."
            )
        return FactShape(
            concept, Unit((numerator,), (denominator,)), periodShift=periodShift
        )
    return FactShape(concept, periodShift=periodShift)


def buildFactShape(
    taxonomy: Taxonomy,
    concept: Concept,
    effective: EffectiveHypercube,
    explicitRepresentative: dict[Concept, Concept],
    defaultByDim: dict[Concept, Concept | None],
    typedPlaceholder: dict[Concept, str],
    *,
    explicitOverrides: dict[Concept, Concept] | None = None,
    typedOverrides: dict[Concept, str] | None = None,
    unitOverride: QName | None = None,
    periodShift: int = 0,
) -> FactShape:
    """
    One fact: every explicit dimension in *explicitRepresentative* and typed dimension
    in *typedPlaceholder* -- both scoped to *effective*, a single EffectiveHypercube --
    at its representative/placeholder value, except for whichever dimensions
    *explicitOverrides*/*typedOverrides* name, which take the value given there
    instead. A single-entry override tests one dimension in isolation; more than one
    tests them varying together. *unitOverride* and *periodShift* pass straight through
    to coreShapeFor() -- they play no part in hypercube validity, so they are not part
    of the matches() check below.

    Confirms the result is actually valid against *effective* via
    EffectiveHypercube.matches() rather than trusting the representative-value
    picker in isolation -- if a negative (notAll) hypercube's exclusion zone catches
    a representative value the picker chose, this raises loudly with a clear message
    instead of silently returning an invalid fixture.
    """
    explicitChosen: dict[Concept, Concept] = {
        **explicitRepresentative,
        **(explicitOverrides or {}),
    }
    typedChosen: dict[Concept, str] = {**typedPlaceholder, **(typedOverrides or {})}
    if not effective.matches(explicitChosen, typedChosen):
        raise SampleGenerationException(
            "Generated dimensions for "
            f"{concept.qname} are not dimensionally valid against "
            f"{effective.roleUri} -- explicit="
            f"{ {str(d.qname): str(v.qname) for d, v in explicitChosen.items()} }, "
            f"typed={ {str(d.qname): v for d, v in typedChosen.items()} }. "
            "The representative-value picker in buildFacts() cannot "
            "satisfy a negative (notAll) hypercube's exclusion here on "
            "its own; it needs updating for this shape."
        )

    core = coreShapeFor(
        taxonomy, concept, unitOverride=unitOverride, periodShift=periodShift
    )
    explicit, typed = buildDimensionValues(explicitChosen, defaultByDim, typedChosen)
    return core._replace(explicit=explicit, typed=typed)


class _Variant(NamedTuple):
    """One more shape a concept's facts must show: a unit other than its usual one, or an
    enumeration set's empty selection."""

    unit: QName | None = None
    emptySet: bool = False


def variantsFor(taxonomy: Taxonomy, concept: Concept, *, nil: bool) -> list[_Variant]:
    """
    Every shape *concept*'s facts have to show, the usual one first: each unit
    extraUnitsFor() names, or, for an enumeration set with members, its empty selection
    (a valued report only; a nil fact has no value to be empty). A concept is one kind or
    the other.
    """
    variants = [_Variant()]
    variants.extend(_Variant(unit=unit) for unit in extraUnitsFor(taxonomy, concept))
    if not nil and concept.isEnumerationSet and concept.getEEDomain():
        variants.append(_Variant(emptySet=True))
    return variants


class _Rotation:
    """Which variant the next fact for one concept shows, and which dimensions it already
    has facts for.

    A concept's facts are the proof-of-reportability fact and then one per dimension value
    varied. The first takes the usual variant; each later one takes the next, round and
    round, so units and the empty selection are shown at the same time as the dimension
    values, not by facts of their own. What the facts did not reach is left over for
    ``uncovered()``. A fact for dimensions already covered is skipped: two facts for one
    context are duplicates, or alternatives, which a coverage report must not contain.
    """

    def __init__(self, variants: list[_Variant]) -> None:
        self.variants = variants
        self.count = 0
        self._contexts: set[tuple[object, ...]] = set()

    def take(self, shape: FactShape) -> _Variant | None:
        """The variant for the fact *shape* describes, or None if there is one for its
        context already (see FactShape.context)."""
        context = shape.context
        if context in self._contexts:
            return None
        self._contexts.add(context)
        variant = self.variants[self.count % len(self.variants)]
        self.count += 1
        return variant

    def peek(self) -> _Variant:
        return self.variants[self.count % len(self.variants)]

    def uncovered(self) -> list[_Variant]:
        return self.variants[self.count :]


def newReport(taxonomy: Taxonomy, samplePeriod: SampleEntityPeriod) -> Report:
    """An empty report for *samplePeriod*'s entity, with its period as the default."""
    report = Report(taxonomy)
    report.setEntity(
        samplePeriod.entityNamespace,
        samplePeriod.entityIdentifier,
        prefix=samplePeriod.entityPrefix,
    )
    _, duration = samplePeriod.periods()
    report.addPeriod("p0", duration)
    report.setDefaultPeriodName("p0")
    return report


class _FactSink:
    """The report buildFacts() builds, and what it noticed."""

    def __init__(
        self, taxonomy: Taxonomy, samplePeriod: SampleEntityPeriod, *, nil: bool
    ) -> None:
        self.nil = nil
        self.samplePeriod = samplePeriod
        self.report = newReport(taxonomy, samplePeriod)
        self.emptyDomains: list[str] = []
        self.emptySetDomains: list[str] = []
        self.seenEmptyValue: set[Concept] = set()
        # For each period shift used by a variant fact, whether it holds an instant fact
        # and whether a duration one: see anchorShiftedPeriods().
        self.shiftKinds: dict[int, set[bool]] = {}
        self._pure = taxonomy.QNameMaker.fromNamespaceAndLocalName(XBRLI_NS, "pure")

    def _period(self, concept: Concept, shift: int) -> _Period:
        """The fact's period, *shift* years back, declared in the report: the duration,
        or for an instant concept the instant, at the start or end of a declared period
        as a Report needs it to be."""
        instant, duration = self.samplePeriod.shifted(shift).periods()
        self.report.addPeriod(f"p{shift}", duration)
        if concept.periodType is not PeriodType.Instant:
            return duration
        if instant not in (duration.startInstant, duration.endInstant):
            self.report.addPeriod(
                f"p{shift}-instant",
                DurationPeriodHolder(
                    instant.instant - timedelta(days=1), instant.instant
                ),
            )
        return instant

    def add(self, shape: FactShape, *, emptySet: bool = False) -> None:
        concept = shape.concept
        if emptySet:
            value: str | None = ""
            decimals: int | None = None
            members: frozenset[Concept] = frozenset()
        else:
            value, decimals, members = valueAndDecimalsFor(concept, nil=self.nil)
            if not self.nil and value is None and concept not in self.seenEmptyValue:
                # An enumeration *single* whose domain resolved to no members -- a
                # property of the concept itself, not of whichever dimension this
                # fact happens to vary, so it is only worth recording once per
                # concept.
                self.seenEmptyValue.add(concept)
                self.emptyDomains.append(str(concept.qname))
        try:
            fb = (
                self.report.getFactBuilder()
                .setConcept(concept)
                .setPeriod(self._period(concept, shape.periodShift))
            )
            for dim, chosen in shape.explicit.items():
                fb.setExplicitDimension(dim, chosen)
            for dim, typedValue in shape.typed.items():
                fb.setTypedDimension(dim, typedValue)
            if shape.unit is not None:
                fb.setUnit(shape.unit)
            elif concept.isNumeric:
                fb.setSimpleUnit(self._pure)
            _setValue(fb, concept, value, decimals, members)
            self.report.addFact(fb.buildFact())
        except InlineReportException as e:
            raise SampleGenerationException(
                f"The sample fact for {concept.qname} is not a valid fact: {e}"
            ) from e
        if shape.periodShift:
            self.shiftKinds.setdefault(shape.periodShift, set()).add(
                concept.periodType is PeriodType.Instant
            )


def _setValue(
    fb: FactBuilder,
    concept: Concept,
    value: str | None,
    decimals: int | None,
    members: frozenset[Concept],
) -> None:
    """Give the fact the sample value valueFor() chose, as the report model holds it."""
    if value is None:
        fb.setNil()
    elif concept.isEnumerationSingle:
        (member,) = members
        fb.setEnumerationValue(member).setValue(enumerationText(members))
    elif concept.isEnumerationSet:
        ordered = sorted(members, key=lambda m: str(m.qname))
        fb.setEnumerationSet(ordered).setValue(enumerationText(ordered))
    elif concept.isNumeric:
        number = Decimal(value)
        places: DecimalPlaces = decimals if decimals is not None else "INF"
        if concept.dataType.localName == "percentItemType":
            fb.setPercentageFact(number, places)
        else:
            whole = number == number.to_integral_value()
            fb.setValue(int(number) if whole else float(number)).setDecimals(places)
    else:
        fb.setValue(value)


def effectiveHypercubeOrder(effective: EffectiveHypercube) -> tuple[object, ...]:
    """The order buildFacts() takes a concept's EffectiveHypercubes in: by hypercubes, then
    dimensions, then base set (role). The role comes last but must be there: two base sets can
    hold the same hypercube with the same dimensions (different domains), and the taxonomy gives
    them as a set, so without it which comes first -- and so which facts are written -- would
    change from run to run with Python's string hashing."""
    return (
        tuple(sorted(hc.hypercube for hc in effective.hypercubes)),
        tuple(
            sorted(
                ed.dimension
                for hc in effective.hypercubes
                for ed in hc.explicitDimensions
            )
        ),
        tuple(sorted(td for hc in effective.hypercubes for td in hc.typedDimensions)),
        effective.roleUri,
    )


def anchorShiftedPeriods(
    sink: _FactSink, anchor: Callable[..., FactShape] | None
) -> None:
    """
    Give every shifted period that holds an instant fact a duration fact too.

    A shifted instant is the end of the shifted year, and the xBRL-JSON reader needs an
    instant to sit at the start or end of a duration the report declares. The first
    shift always does (it is the start of the report's own year); a later one only if
    some duration fact lives there. *anchor* builds the proof-of-reportability fact of a
    duration concept; None when the taxonomy has no duration concept, in which case
    there is nothing to anchor with.
    """
    if anchor is None:
        return
    for shift, kinds in sorted(sink.shiftKinds.items()):
        if shift >= 2 and True in kinds and False not in kinds:
            sink.add(anchor(periodShift=shift))


def buildFacts(
    taxonomy: Taxonomy,
    concepts: list[Concept],
    samplePeriod: SampleEntityPeriod,
    *,
    nil: bool,
) -> tuple[Report, list[str], list[str]]:
    """
    Build the report for one sample.

    Every reportable concept gets one fact proving it can be reported at all, plus
    further facts on top of that proving each of its hypercube dimensions,
    explicit or typed, per the definition linkbase, is individually usable -- a
    single fact is dimensionally valid only against whichever one dimension it
    varies, so it never by itself proves the base set's *other* dimension values
    are usable too.

    A primary item can be valid against more than one EffectiveHypercube -- e.g.
    two different base sets, possibly with entirely unrelated dimensions, or the
    same dimension restricted to different domains in each (XBRL Dimensions 1.0
    section 3.1.1: a fact only has to satisfy *one*, never their union). Facts are
    therefore built per-EffectiveHypercube: co-dimensions pinned to a
    representative value always come from the *same* EffectiveHypercube as the
    dimension under test, never mixed across base sets.

    Within one EffectiveHypercube, every hypercube the primary item is declared in
    for that base set is conjoined (section 3.1.2), so a dimension can be declared
    by more than one hypercube at once, in which case its valid domain is their
    intersection, not their union -- see domainForDimension(). Only *positive*
    hypercubes contribute dimensions to vary or enumerate: a negative (notAll) one
    only ever excludes a region, so it has nothing to prove usable. Every generated
    fact's dimensions are additionally confirmed valid via EffectiveHypercube.matches()
    before being emitted rather than trusting the representative-value picker in
    isolation -- if a negative hypercube's exclusion zone catches a representative
    value this picker chose, this raises loudly with a clear message instead of
    silently emitting an invalid fixture.

    The proof-of-reportability fact comes from one EffectiveHypercube only (the
    first, by a stable sort), with every dimension at its representative/
    placeholder value and nothing varied. It is not simply "every dimension
    omitted": an explicit dimension can have a taxonomy default that lets it be
    omitted, but a typed dimension never can, so a concept whose every
    EffectiveHypercube needs a typed dimension has no all-omitted state to fall
    back on, and must carry one.

    Explicit dimensions get one additional fact per (dimension, non-default domain
    member): every member of every explicit dimension attached to the concept in
    turn, other than the one that equals the default (already covered by the
    proof-of-reportability fact, since a default member can only be represented by
    omitting the dimension, never by writing it explicitly). Typed dimensions
    instead get TYPED_DIMENSION_SAMPLE_ROWS synthetic sample values, since they have
    no domain to enumerate and no default to skip -- see that constant.

    Either way, the dimension under test varies while its co-dimensions (explicit or
    typed) are pinned to one representative value each, proving each dimension
    individually valid without multiplying the fact count. A (dimension, member) or
    typed dimension already exercised via one EffectiveHypercube is not repeated for
    another that happens to share it.

    On top of that, each EffectiveHypercube with more than one dimension able to vary
    gets exactly one further fact with every one of them varied away from its
    representative/default value at once, proving the combination is valid together
    and not just each dimension in isolation -- one fact regardless of how many
    dimensions there are, not their cross product. A dimension whose only member is
    its own default has nothing to vary it to and sits out of this fact rather than
    suppressing it.

    No two facts share a context (concept, period and dimensions): a repeat of one
    already emitted is skipped, and a coverage report must hold no duplicates and no
    alternative facts. What each fact *shows* is a per-concept rotation (see
    variantsFor() and _Rotation): the proof fact takes the usual unit and value, and
    each further fact takes the next variant, so the other permitted units of a numeric
    concept, and an enumeration set's empty selection, are shown by the same facts that
    vary the dimensions. A variant no fact reached (a concept with no dimensions, or
    fewer varied facts than variants) gets its own fact at the proof-of-reportability
    dimensions in an earlier period, one further year back for each (see
    SampleEntityPeriod.shifted()), so its context is its own.

    Returns (the report, "concept" entries left nil because an enumeration
    *single*'s domain resolved to no members, "concept" entries that are an
    enumeration *set* whose domain resolved to no members -- reported via the
    empty-set value rather than left nil, see valueFor() -- or "concept (dimension)"
    entries skipped because an explicit dimension's domain resolved to no members).
    """
    sink = _FactSink(taxonomy, samplePeriod, nil=nil)
    durationAnchor: Callable[..., FactShape] | None = None

    for concept in concepts:
        eeSetMembers = concept.getEEDomain() if concept.isEnumerationSet else None
        if not nil and eeSetMembers is not None and not eeSetMembers:
            sink.emptySetDomains.append(str(concept.qname))

        rotation = _Rotation(variantsFor(taxonomy, concept, nil=nil))

        def place(
            make: Callable[..., FactShape], *, _rotation: _Rotation = rotation
        ) -> None:
            """Add the fact *make* builds, showing whichever variant is next, unless
            there is a fact for those dimensions already."""
            shape = make(unitOverride=_rotation.peek().unit)
            if (variant := _rotation.take(shape)) is None:
                return
            sink.add(shape, emptySet=variant.emptySet)

        effectiveHypercubes = sorted(
            taxonomy.getEffectiveHypercubesForPrimaryItem(concept),
            key=effectiveHypercubeOrder,
        )
        proof: Callable[..., FactShape]
        if not effectiveHypercubes:
            # No hypercube attaches to this concept at all, so there is no XBRL
            # Dimensions constraint to satisfy: every dimension is simply absent.
            proof = functools.partial(coreShapeFor, taxonomy, concept)
            place(proof)

        seenExplicit: set[tuple[Concept, Concept]] = set()
        seenTyped: set[Concept] = set()
        seenEmptyDomains: set[Concept] = set()

        for effectiveIndex, effective in enumerate(effectiveHypercubes):
            positives = [
                hc for hc in effective.hypercubes if hc.type is HypercubeType.Positive
            ]
            explicitDims = sorted(
                {ed.dimension for hc in positives for ed in hc.explicitDimensions}
            )
            typedDims = sorted({td for hc in positives for td in hc.typedDimensions})

            membersByDim = {
                dim: domainForDimension(dim, positives) for dim in explicitDims
            }
            defaultByDim = {
                dim: taxonomy.getDimensionDefault(dim) for dim in explicitDims
            }
            explicitRepresentative = pickExplicitRepresentative(
                membersByDim, defaultByDim
            )
            # A fixed placeholder per typed dimension, used whenever it is a
            # co-dimension rather than the one under test -- content is arbitrary,
            # so there is no "default" to reason about the way there is for
            # explicit dims.
            typedPlaceholder = {
                dim: typedDimensionSampleValue(dim, 0) for dim in typedDims
            }

            # functools.partial, not a nested def/lambda: its arguments are bound
            # eagerly at this point in the loop, so there is no closure capturing
            # (and no B023 risk of) whatever effective/explicitRepresentative/
            # defaultByDim/typedPlaceholder are bound to by a *later* iteration.
            dimensionsWith = functools.partial(
                buildFactShape,
                taxonomy,
                concept,
                effective,
                explicitRepresentative,
                defaultByDim,
                typedPlaceholder,
            )

            if effectiveIndex == 0:
                # The proof-of-reportability fact for this concept: every
                # dimension in this EffectiveHypercube at its representative/
                # placeholder value, nothing varied.
                proof = dimensionsWith
                place(proof)

            for variedDim in explicitDims:
                members = membersByDim[variedDim]
                if not members:
                    if variedDim not in seenEmptyDomains:
                        seenEmptyDomains.add(variedDim)
                        sink.emptyDomains.append(f"{concept.qname} ({variedDim.qname})")
                    continue

                variedDefault = defaultByDim[variedDim]
                for member in members:
                    if member == variedDefault:
                        # A default member can only be represented by omitting the
                        # dimension, which the proof-of-reportability fact already
                        # does whenever this dimension is a co-dimension there --
                        # so there is nothing distinct left to test for this member.
                        continue
                    key = (variedDim, member)
                    if key in seenExplicit:
                        # Already exercised via another EffectiveHypercube that
                        # shares this (dimension, member) -- no need to repeat it.
                        continue
                    seenExplicit.add(key)
                    place(
                        functools.partial(
                            dimensionsWith, explicitOverrides={variedDim: member}
                        )
                    )

            for variedDim in typedDims:
                if variedDim in seenTyped:
                    continue
                seenTyped.add(variedDim)
                for row in range(1, TYPED_DIMENSION_SAMPLE_ROWS + 1):
                    value = typedDimensionSampleValue(variedDim, row)
                    place(
                        functools.partial(
                            dimensionsWith, typedOverrides={variedDim: value}
                        )
                    )

            explicitVariedAll: dict[Concept, Concept] = {}
            for variedDim in explicitDims:
                nonDefault = next(
                    (
                        m
                        for m in membersByDim[variedDim]
                        if m != defaultByDim[variedDim]
                    ),
                    None,
                )
                if nonDefault is not None:
                    explicitVariedAll[variedDim] = nonDefault
            typedVariedAll = {
                variedDim: typedDimensionSampleValue(variedDim, 1)
                for variedDim in typedDims
            }
            if len(explicitVariedAll) + len(typedVariedAll) > 1:
                place(
                    functools.partial(
                        dimensionsWith,
                        explicitOverrides=explicitVariedAll,
                        typedOverrides=typedVariedAll,
                    )
                )

        # Variants the dimension facts did not reach, each in its own earlier period.
        for shift, variant in enumerate(rotation.uncovered(), start=1):
            sink.add(
                proof(unitOverride=variant.unit, periodShift=shift),
                emptySet=variant.emptySet,
            )

        if durationAnchor is None and concept.periodType is not PeriodType.Instant:
            durationAnchor = proof

    anchorShiftedPeriods(sink, durationAnchor)
    return sink.report, sink.emptyDomains, sink.emptySetDomains
