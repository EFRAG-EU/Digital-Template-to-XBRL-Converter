"""
Generate a maximal, dimensionally-valid xBRL-JSON (OIM) sample report for an
arbitrary taxonomy: one fact per reportable concept, plus further facts that
individually exercise every hypercube dimension (explicit or typed) and every
enumeration domain a concept carries, so a consumer can be shown every reportable
concept and dimension combination is actually usable -- not just declared.

Nothing here parses a schema or a linkbase, or hardcodes what a taxonomy's concepts
or dimensions are: everything comes from a Taxonomy already baked by
mireport.arelle.taxonomy_info.callArelleForTaxonomyInfo() and loaded via
mireport.taxonomy.loadTaxonomyJSON(). The entity, period(s) and output shape a sample
report needs are inherently choices about the *sample*, not something this module can
sensibly default, so callers supply them via SampleEntityPeriod.
"""

from __future__ import annotations

import functools
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from mireport.exceptions import SampleGenerationException
from mireport.taxonomy import HypercubeType, PeriodType

if TYPE_CHECKING:
    from collections.abc import Iterable

    from mireport.taxonomy import (
        Concept,
        EffectiveHypercube,
        HypercubeDeclaration,
        Taxonomy,
    )
    from mireport.xml import QName

# OIM reserved alias for the standard xBRL namespace; declaring it is what makes the
# documentInfo/features entry legal.
NS_XBRL = "https://xbrl.org/2021"

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


def unitFor(taxonomy: Taxonomy, concept: Concept) -> QName | None:
    """
    The unit to report *concept* with, or None if it takes no unit dimension.

    A numeric concept whose data type the UTR does not constrain is dimensionless, and
    OIM requires the unit dimension to be *omitted* for those rather than written as
    xbrli:pure. percentItemType is the case that matters here: it has no UTR entries.
    """
    if not concept.isNumeric:
        return None
    permitted = taxonomy.UTR.getUnitsForDataType(concept.dataType)
    if not permitted:
        return None

    dataTypeName = concept.dataType.localName
    if (unitId := PREFERRED_UNIT_IDS.get(dataTypeName)) is None:
        raise SampleGenerationException(
            f"No preferred unit for {dataTypeName} (concept {concept.qname}). "
            f"The UTR permits {len(permitted)}; add one to PREFERRED_UNIT_IDS."
        )
    unit = taxonomy.UTR.getQNameForUnitId(unitId)
    if unit is None or not taxonomy.UTR.valid(concept.dataType, unit):
        raise SampleGenerationException(
            f"Preferred unit {unitId!r} is not valid for {dataTypeName} "
            f"(concept {concept.qname})."
        )
    return unit


def valueFor(concept: Concept) -> tuple[str | None, int | None, frozenset[QName]]:
    """
    The sample value, decimals, and QNames used for *concept*, or (None, None, empty)
    to leave it nil.

    Enumerations resolve first: their base type is a token, so a base-type value would
    be nonsense. An enumeration's value is itself a domain member's QName, so its
    namespace must be reported back too -- it need not be the concept's own namespace,
    and omitting it would leave that prefix undeclared in documentInfo.namespaces.

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
        return " ".join(qnames), None, frozenset(member.qname for member in chosen)

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
    rather than read off the taxonomy.
    """
    if row == 0:
        return f"{dim.qname.localName} sample value"
    return f"{dim.qname.localName} typed member {row}"


def valueAndDecimalsFor(
    concept: Concept, *, nil: bool
) -> tuple[str | None, int | None, frozenset[QName]]:
    """
    The (value, decimals, QNames used) triple for one fact of *concept*, or
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
    some other dimension is the one under test: the taxonomy default if there is one,
    else the first member by QName. A dimension with no members at all is omitted --
    there is no representative value to offer, and the caller tracks that separately
    (an empty domain, not a representative choice).
    """
    return {
        dim: (defaultByDim[dim] or members[0])
        for dim, members in membersByDim.items()
        if members
    }


def buildDimensionValues(
    explicitChosen: dict[Concept, Concept],
    defaultByDim: dict[Concept, Concept | None],
    typedChosen: dict[Concept, str],
) -> tuple[dict[str, str], set[QName]]:
    """
    The dimensions dict (as OIM writes them) and QNames used for one fact's explicit
    and typed dimension choices.

    An explicit dimension chosen at its own taxonomy default is omitted rather than
    written out: OIM (xBRL-JSON) forbids writing a taxonomy-defined dimension
    explicitly as its own default member (oime:invalidDimensionValue) -- it must be
    represented by omitting the dimension instead.
    """
    dimensions: dict[str, str] = {}
    used: set[QName] = set()
    for dim, chosen in explicitChosen.items():
        if chosen == defaultByDim[dim]:
            continue
        dimensions[str(dim.qname)] = str(chosen.qname)
        used.add(dim.qname)
        used.add(chosen.qname)
    for dim, value in typedChosen.items():
        dimensions[str(dim.qname)] = value
        used.add(dim.qname)
    return dimensions, used


def namespacesFor(qnames: Iterable[QName]) -> dict[str, str]:
    """Build a namespaces map (prefix -> URI) for every QName actually used."""
    return {qname.prefix: qname.namespace for qname in qnames}


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


def coreDimensionsFor(
    taxonomy: Taxonomy, concept: Concept, samplePeriod: SampleEntityPeriod
) -> tuple[dict[str, str], set[QName]]:
    """
    The concept/entity/period/unit dimensions common to every fact for *concept*, and
    the QNames it uses (concept, and unit if any) -- collected rather than written
    straight into a shared namespaces dict, since the same concept/unit recurs across
    many facts and there is no reason to overwrite the same entry repeatedly.
    """
    qnames = {concept.qname}
    dimensions: dict[str, str] = {
        "concept": str(concept.qname),
        "entity": samplePeriod.entity,
        "period": (
            samplePeriod.periodInstant
            if concept.periodType is PeriodType.Instant
            else samplePeriod.periodDuration
        ),
    }
    if (unit := unitFor(taxonomy, concept)) is not None:
        dimensions["unit"] = str(unit)
        qnames.add(unit)
    return dimensions, qnames


def buildFactDimensions(
    taxonomy: Taxonomy,
    concept: Concept,
    samplePeriod: SampleEntityPeriod,
    effective: EffectiveHypercube,
    explicitRepresentative: dict[Concept, Concept],
    defaultByDim: dict[Concept, Concept | None],
    typedPlaceholder: dict[Concept, str],
    *,
    variedExplicit: Concept | None = None,
    explicitValue: Concept | None = None,
    variedTyped: Concept | None = None,
    typedValue: str | None = None,
) -> tuple[dict[str, str], set[QName]]:
    """
    One fact's dimensions: the dimension under test (if any) at its test value,
    every other explicit dimension in *explicitRepresentative* and typed dimension
    in *typedPlaceholder* -- both scoped to *effective*, a single EffectiveHypercube
    -- at its representative/placeholder value.

    Confirms the result is actually valid against *effective* via
    EffectiveHypercube.matches() rather than trusting the representative-value
    picker in isolation -- if a negative (notAll) hypercube's exclusion zone catches
    a representative value the picker chose, this raises loudly with a clear message
    instead of silently returning an invalid fixture.
    """
    explicitChosen: dict[Concept, Concept] = {
        dim: (
            explicitValue
            if dim is variedExplicit and explicitValue is not None
            else defaultMember
        )
        for dim, defaultMember in explicitRepresentative.items()
    }
    typedChosen: dict[Concept, str] = {
        dim: (
            typedValue if dim is variedTyped and typedValue is not None else placeholder
        )
        for dim, placeholder in typedPlaceholder.items()
    }
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

    dimensions, used = coreDimensionsFor(taxonomy, concept, samplePeriod)
    extraDimensions, extraUsed = buildDimensionValues(
        explicitChosen, defaultByDim, typedChosen
    )
    dimensions.update(extraDimensions)
    used.update(extraUsed)
    return dimensions, used


def buildFacts(
    taxonomy: Taxonomy,
    concepts: list[Concept],
    samplePeriod: SampleEntityPeriod,
    *,
    nil: bool,
) -> tuple[dict[str, Any], dict[str, str], list[str], list[str]]:
    """
    Build the facts object for one sample.

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
    typed) are pinned to one representative value each -- varying every attached
    dimension together would multiply the fact count rather than add to it, and is
    not needed to prove each dimension individually valid. A (dimension, member) or
    typed dimension already exercised via one EffectiveHypercube is not repeated for
    another that happens to share it.

    An enumeration *set* concept whose domain is not empty also gets one further fact
    with the empty-set value (see valueFor()), on top of whatever a plain fact would
    otherwise carry -- an empty selection is a distinct, deliberately-valid shape, not
    something a concept falls back to only when its domain happens to be empty.

    Returns (facts, namespaces used -- including the reserved xbrl alias and the
    sample entity's own namespace, "concept" entries left nil because an enumeration
    *single*'s domain resolved to no members, "concept" entries that are an
    enumeration *set* whose domain resolved to no members -- reported via the
    empty-set value rather than left nil, see valueFor() -- or "concept (dimension)"
    entries skipped because an explicit dimension's domain resolved to no members).
    """
    facts: dict[str, Any] = {}
    qnames: set[QName] = set()
    emptyDomains: list[str] = []
    emptySetDomains: list[str] = []
    seenEmptyValue: set[Concept] = set()
    index = 1

    def addFact(
        dimensions: dict[str, str],
        used: set[QName],
        *,
        valueOverride: tuple[str | None, int | None, frozenset[QName]] | None = None,
    ) -> None:
        nonlocal index
        qnames.update(used)
        if valueOverride is not None:
            value, decimals, usedByValue = valueOverride
        else:
            value, decimals, usedByValue = valueAndDecimalsFor(concept, nil=nil)
            if not nil and value is None and concept not in seenEmptyValue:
                # An enumeration *single* whose domain resolved to no members -- a
                # property of the concept itself, not of whichever dimension this
                # fact happens to vary, so it is only worth recording once per
                # concept.
                seenEmptyValue.add(concept)
                emptyDomains.append(str(concept.qname))
        qnames.update(usedByValue)
        fact: dict[str, Any] = {"dimensions": dimensions, "value": value}
        if decimals is not None:
            fact["decimals"] = decimals
        facts[f"f{index}"] = fact
        index += 1

    for concept in concepts:
        # Whether to add the extra empty-set fact below: only for the valued report,
        # only for a set concept, and only when its domain has members -- a domain
        # with none already gets the empty-set value from its one and only fact
        # (valueFor()), so adding it again here would just be a duplicate.
        eeSetMembers = concept.getEEDomain() if concept.isEnumerationSet else None
        addEmptySetFact = not nil and bool(eeSetMembers)
        if not nil and eeSetMembers is not None and not eeSetMembers:
            emptySetDomains.append(str(concept.qname))

        effectiveHypercubes = sorted(
            taxonomy.getEffectiveHypercubesForPrimaryItem(concept),
            key=lambda eh: (
                tuple(sorted(hc.hypercube for hc in eh.hypercubes)),
                tuple(
                    sorted(
                        ed.dimension
                        for hc in eh.hypercubes
                        for ed in hc.explicitDimensions
                    )
                ),
                tuple(sorted(td for hc in eh.hypercubes for td in hc.typedDimensions)),
            ),
        )
        if not effectiveHypercubes:
            # No hypercube attaches to this concept at all, so there is no XBRL
            # Dimensions constraint to satisfy: every dimension is simply absent.
            addFact(*coreDimensionsFor(taxonomy, concept, samplePeriod))
            if addEmptySetFact:
                addFact(
                    *coreDimensionsFor(taxonomy, concept, samplePeriod),
                    valueOverride=("", None, frozenset()),
                )

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
                buildFactDimensions,
                taxonomy,
                concept,
                samplePeriod,
                effective,
                explicitRepresentative,
                defaultByDim,
                typedPlaceholder,
            )

            if effectiveIndex == 0:
                # The proof-of-reportability fact for this concept: every
                # dimension in this EffectiveHypercube at its representative/
                # placeholder value, nothing varied.
                addFact(*dimensionsWith())
                if addEmptySetFact:
                    addFact(*dimensionsWith(), valueOverride=("", None, frozenset()))

            for variedDim in explicitDims:
                members = membersByDim[variedDim]
                if not members:
                    if variedDim not in seenEmptyDomains:
                        seenEmptyDomains.add(variedDim)
                        emptyDomains.append(f"{concept.qname} ({variedDim.qname})")
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
                    addFact(
                        *dimensionsWith(variedExplicit=variedDim, explicitValue=member)
                    )

            for variedDim in typedDims:
                if variedDim in seenTyped:
                    continue
                seenTyped.add(variedDim)
                for row in range(1, TYPED_DIMENSION_SAMPLE_ROWS + 1):
                    value = typedDimensionSampleValue(variedDim, row)
                    addFact(*dimensionsWith(variedTyped=variedDim, typedValue=value))

    namespaces = {
        "xbrl": NS_XBRL,
        samplePeriod.entityPrefix: samplePeriod.entityNamespace,
        **namespacesFor(qnames),
    }
    return facts, namespaces, emptyDomains, emptySetDomains


def makeDocument(
    facts: dict[str, Any],
    namespaces: dict[str, str],
    entry_point_documents: Iterable[str],
) -> dict[str, Any]:
    """Wrap *facts* in an xBRL-JSON document referencing every document
    *entry_point_documents* names -- an entry point may name more than one, which
    together form a single DTS."""
    return {
        "documentInfo": {
            "documentType": "https://xbrl.org/2021/xbrl-json",
            "features": {"xbrl:canonicalValues": True},
            "namespaces": dict(sorted(namespaces.items())),
            "taxonomy": list(entry_point_documents),
        },
        "facts": facts,
    }
