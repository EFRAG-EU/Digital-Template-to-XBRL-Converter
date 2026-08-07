"""Tests for Taxonomy.resolveConcept predicate filtering.

The predicate composes with only_reportable and is applied BEFORE the
ambiguity check, so a caller's type/domain constraint participates in
disambiguation instead of being a post-check that never runs because
AmbiguousComponentException was raised first.
"""

import pytest

from mireport.exceptions import AmbiguousComponentException
from mireport.stringutil import normalizeLabelText, stripLabelSuffix
from mireport.taxonomy import (
    Concept,
    Taxonomy,
    getTaxonomy,
    listTaxonomies,
    loadBuiltInTaxonomyJSON,
)


@pytest.fixture(scope="module")
def taxonomy() -> Taxonomy:
    if not listTaxonomies():
        loadBuiltInTaxonomyJSON()
    entry_point = next(ep for ep in listTaxonomies() if "vsme" in ep.lower())
    return getTaxonomy(entry_point)


def _labelVariants(taxonomy: Taxonomy) -> set[str]:
    """Every text a label lookup could be attempted with: exact standard labels
    plus the normalized / suffix-stripped / lowercased variants."""
    variants: set[str] = set()
    for concept in taxonomy.concepts:
        for label in concept.getAllStandardLabels():
            normalized = normalizeLabelText(label)
            no_suffix = stripLabelSuffix(normalized)
            variants.update((label, normalized, no_suffix, no_suffix.lower()))
    return variants


@pytest.fixture(scope="module")
def ambiguous_label(taxonomy) -> tuple[str, set[Concept]]:
    """A label text that resolveConcept refuses to resolve unaided, plus the
    candidate concepts the exception reported."""
    for text in sorted(_labelVariants(taxonomy)):
        try:
            taxonomy.resolveConcept(text, by_label=True, only_reportable=False)
        except AmbiguousComponentException as exc:
            concepts = set(exc.candidates)
            assert len(concepts) > 1, "fixture self-check"
            return text, concepts
    pytest.skip("vsme taxonomy has no ambiguous label text")


@pytest.fixture(scope="module")
def unique_reportable(taxonomy) -> tuple[str, Concept]:
    """A (label, concept) pair where the label maps to exactly that concept."""
    for concept in sorted(taxonomy.concepts, key=str):
        if not concept.isReportable:
            continue
        for label in concept.getAllStandardLabels():
            try:
                resolved = taxonomy.resolveConcept(label, by_label=True)
            except AmbiguousComponentException:
                continue
            if resolved is concept:
                return label, concept
    pytest.skip("vsme taxonomy has no uniquely-labelled reportable concept")


class TestPredicateDisambiguation:
    def test_ambiguous_label_raises_without_predicate(self, taxonomy, ambiguous_label):
        label, _ = ambiguous_label
        with pytest.raises(AmbiguousComponentException):
            taxonomy.resolveConcept(label, by_label=True, only_reportable=False)

    def test_exception_carries_structured_candidates(self, taxonomy, ambiguous_label):
        label, concepts = ambiguous_label
        with pytest.raises(AmbiguousComponentException) as excinfo:
            taxonomy.resolveConcept(label, by_label=True, only_reportable=False)
        assert excinfo.value.candidates == tuple(sorted(concepts))
        assert all(isinstance(c, Concept) for c in excinfo.value.candidates)

    def test_exception_candidates_default_empty(self):
        assert AmbiguousComponentException("bare message").candidates == ()

    def test_predicate_pins_each_candidate(self, taxonomy, ambiguous_label):
        label, concepts = ambiguous_label
        for target in sorted(concepts):
            resolved = taxonomy.resolveConcept(
                label,
                by_label=True,
                only_reportable=False,
                predicate=lambda c, target=target: c.qname == target.qname,
            )
            assert resolved is target

    def test_all_pass_predicate_still_raises(self, taxonomy, ambiguous_label):
        label, _ = ambiguous_label
        with pytest.raises(AmbiguousComponentException):
            taxonomy.resolveConcept(
                label,
                by_label=True,
                only_reportable=False,
                predicate=lambda c: True,
            )

    def test_predicate_rejecting_all_returns_none(self, taxonomy, ambiguous_label):
        label, _ = ambiguous_label
        assert (
            taxonomy.resolveConcept(
                label,
                by_label=True,
                only_reportable=False,
                predicate=lambda c: False,
            )
            is None
        )


class TestPredicateSemantics:
    def test_predicate_rejecting_sole_candidate_returns_none(
        self, taxonomy, unique_reportable
    ):
        label, _ = unique_reportable
        assert (
            taxonomy.resolveConcept(label, by_label=True, predicate=lambda c: False)
            is None
        )

    def test_passing_predicate_leaves_match_intact(self, taxonomy, unique_reportable):
        label, concept = unique_reportable
        assert (
            taxonomy.resolveConcept(label, by_label=True, predicate=lambda c: True)
            is concept
        )

    def test_qname_fast_path_respects_predicate(self, taxonomy, unique_reportable):
        _, concept = unique_reportable
        qname = str(concept.qname)
        assert taxonomy.resolveConcept(qname, by_qname=True) is concept
        assert (
            taxonomy.resolveConcept(qname, by_qname=True, predicate=lambda c: False)
            is None
        )

    def test_predicate_composes_with_only_reportable(self, taxonomy):
        abstract = next(
            (c for c in sorted(taxonomy.concepts, key=str) if not c.isReportable),
            None,
        )
        if abstract is None:
            pytest.skip("vsme taxonomy has no abstract concept")
        qname = str(abstract.qname)
        pin = lambda c: c.qname == abstract.qname
        # predicate passes but the reportable filter still applies...
        assert (
            taxonomy.resolveConcept(
                qname, by_qname=True, only_reportable=True, predicate=pin
            )
            is None
        )
        # ...and relaxing only_reportable lets the predicate match through.
        assert (
            taxonomy.resolveConcept(
                qname, by_qname=True, only_reportable=False, predicate=pin
            )
            is abstract
        )

    def test_no_strategy_still_raises_value_error(self, taxonomy):
        with pytest.raises(ValueError):
            taxonomy.resolveConcept("anything", predicate=lambda c: True)
