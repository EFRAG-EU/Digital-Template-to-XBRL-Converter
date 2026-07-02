"""Resolution of cell text to enumeration (EE) domain members and other
labelled concepts.

resolveMemberByLabel is the single implementation of the lookup chain that
fact creation uses everywhere a cell holds a member label:

  exact standard label -> configured cell-value alias -> closest EE-domain match

Callers decide what messages to emit from the LabelMatch flags; the chain
itself is silent.
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
) -> Optional[LabelMatch]:
    """Resolve cell text to a concept: exact standard label, then the
    configured cell-value alias, then (when ee_concept is given) the closest
    EE domain-member label. None if nothing matches."""
    if (concept := taxonomy.getConceptForLabel(text)) is not None:
        return LabelMatch(concept, viaConfiguredAlias=False, closestLabel=None)

    alias = config.cellValuesToTaxonomyLabels.get(text)
    if (
        alias is not None
        and (concept := taxonomy.getConceptForLabel(alias)) is not None
    ):
        return LabelMatch(concept, viaConfiguredAlias=True, closestLabel=None)

    if ee_concept is not None and (result := getClosestEEMemberMatch(ee_concept, text)):
        member, label_matched = result
        return LabelMatch(member, viaConfiguredAlias=False, closestLabel=label_matched)

    return None
