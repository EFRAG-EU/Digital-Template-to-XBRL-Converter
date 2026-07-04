"""Resolution of cell text to enumeration (EE) domain members and other
labelled concepts.

resolveMemberByLabel is the single implementation of the lookup chain that
fact creation uses everywhere a cell holds a member label:

  exact standard label -> configured cell-value alias -> closest EE-domain match

The exact and alias lookups are scoped to the relevant domain when the caller
supplies one (an EE concept's own domain, or an explicit dimension's maximum
permitted members), so out-of-domain concepts can never win.

Callers decide what messages to emit from the LabelMatch flags; the chain
itself is silent (AmbiguousComponentException propagates for callers to
report).
"""

from __future__ import annotations

import difflib
from functools import lru_cache
from typing import TYPE_CHECKING, NamedTuple, Optional

if TYPE_CHECKING:
    from mireport.taxonomy import Concept, Taxonomy
    from mireport.xlsx_template_reader._config import ConverterConfig

from mireport.stringutil import stripLabelSuffix


@lru_cache(maxsize=100)
def eeDomainByLabel(eeConcept: Concept) -> dict[str, tuple[Concept, str]]:
    if not (eeConcept.isEnumerationSet or eeConcept.isEnumerationSingle):
        raise ValueError(
            f"Concept {eeConcept} with data-type {eeConcept.dataType} is not of enumeration type."
        )

    eeDomainLabels: dict[str, tuple[Concept, str]] = dict()
    for eeMember in eeConcept.getEEDomain():
        all_labels = eeMember.getAllStandardLabels()
        for actual_label in all_labels:
            result = (eeMember, actual_label)
            eeDomainLabels[actual_label] = result
            label_no_suffix = stripLabelSuffix(actual_label)
            eeDomainLabels[label_no_suffix] = result
    return eeDomainLabels


def getClosestEEMemberMatch(
    eeConcept: Concept, text: str
) -> Optional[tuple[Concept, str]]:
    eeDomainLabels = eeDomainByLabel(eeConcept)
    closest_matches = difflib.get_close_matches(
        text, eeDomainLabels.keys(), n=1, cutoff=0.6
    )
    if closest_matches:
        return eeDomainLabels[closest_matches[0]]
    return None


class LabelMatch(NamedTuple):
    """How a cell text resolved to a concept."""

    concept: Concept
    viaConfiguredAlias: bool
    closestLabel: Optional[str]
    """The domain-member label actually matched, when fuzzy matching was used."""


def resolveMemberByLabel(
    taxonomy: Taxonomy,
    config: ConverterConfig,
    text: str,
    *,
    ee_concept: Optional[Concept] = None,
    dimension: Optional[Concept] = None,
) -> Optional[LabelMatch]:
    """Resolve cell text to a concept by trying, in order: exact standard
    label, the configured cell-value alias, and — only when ee_concept is
    given — the closest domain-member label. None if nothing matches.

    ee_concept and dimension are mutually exclusive routes to a domain that
    scopes the exact-label and alias lookups (the closest-match fallback is
    only defined for enumeration domains, so it never runs for a plain
    dimension): an enumeration concept's domain comes from its own allowed
    fact values (getEEDomain), while an explicit dimension's domain is the
    union of members declared for it across every hypercube that uses it
    (getDomainMembersForExplicitDimension) — which of those are actually
    valid in a given cube is left to fact building. Unscoped when neither is
    given.

    Raises AmbiguousComponentException when, even after scoping, several
    domain members share the text — callers must report this rather than
    have fuzzy matching pick one.
    """
    if ee_concept is not None and dimension is not None:
        raise ValueError("Specify at most one of ee_concept and dimension")

    domain: Optional[frozenset[Concept]] = None
    if ee_concept is not None:
        domain = frozenset(ee_concept.getEEDomain())
    elif dimension is not None:
        domain = taxonomy.getDomainMembersForExplicitDimension(dimension)
    predicate = None if domain is None else (lambda c: c in domain)

    if (
        concept := taxonomy.resolveConcept(
            text, by_label=True, only_reportable=False, predicate=predicate
        )
    ) is not None:
        return LabelMatch(concept, viaConfiguredAlias=False, closestLabel=None)

    alias = config.cellValuesToTaxonomyLabels.get(text)
    if (
        alias is not None
        and (
            concept := taxonomy.resolveConcept(
                alias, by_label=True, only_reportable=False, predicate=predicate
            )
        )
        is not None
    ):
        return LabelMatch(concept, viaConfiguredAlias=True, closestLabel=None)

    if ee_concept is not None and (result := getClosestEEMemberMatch(ee_concept, text)):
        member, label_matched = result
        return LabelMatch(member, viaConfiguredAlias=False, closestLabel=label_matched)

    return None
