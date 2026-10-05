"""Dimensional validity of a fact, checked wherever a fact enters a report."""

from __future__ import annotations

from mireport.exceptions import InlineReportException
from mireport.report.fact import Fact
from mireport.taxonomy import Concept, Taxonomy


def validateDimensions(
    taxonomy: Taxonomy,
    concept: Concept,
    explicitDims: dict[Concept, Concept],
    typedDims: dict[Concept, str],
) -> None:
    """Easy checks for XBRL validity to avoid mistakes. Still possible to create invalid facts.

    A fact is dimensionally valid if its dimension values match at least one
    EffectiveHypercube for the concept (XBRL Dimensions 1.0 section 3.1.1: OR
    across base sets) -- never the union of every one's dimensions, since a
    concept can participate in multiple hypercubes/base-sets with different
    (even unrelated) dimensional requirements. A declared typed dimension that is
    missing makes a fact invalid for that hypercube.

    Known gap: a typed dimension's *value* is only checked to be present, not against
    the typed element's datatype.
    """
    effectiveHypercubes = taxonomy.getEffectiveHypercubesForPrimaryItem(concept)
    if not effectiveHypercubes:
        if explicitDims or typedDims:
            dim_list = ", ".join(str(d.qname) for d in (*explicitDims, *typedDims))
            raise InlineReportException(
                f"Unexpected dimension(s) [{dim_list}] set on {concept}, which does not participate in any hypercube"
            )
        return

    if any(
        effective.matches(explicitDims, typedDims) for effective in effectiveHypercubes
    ):
        return

    chosen_ed = ", ".join(f"{d.qname}={v.qname}" for d, v in explicitDims.items())
    chosen_td = ", ".join(f"{d.qname}={v!r}" for d, v in typedDims.items())
    raise InlineReportException(
        f"No valid dimensional combination for {concept} matches the "
        f"dimensions set (explicit: [{chosen_ed}], typed: [{chosen_td}])"
    )


def validateFactDimensions(taxonomy: Taxonomy, fact: Fact) -> None:
    """Check an already-built fact against the taxonomy's hypercubes."""
    validateDimensions(
        taxonomy,
        fact.concept,
        {d.dimension: d.member for d in fact.explicit_values},
        {t.dimension: t.value for t in fact.typed_values},
    )
