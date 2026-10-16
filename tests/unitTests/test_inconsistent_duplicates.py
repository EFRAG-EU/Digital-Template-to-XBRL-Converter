from __future__ import annotations

from collections import defaultdict
from types import SimpleNamespace

from mireport.report.inlinereport import InlineReport


def _fact(concept, value, aspects, provenance=None):
    return SimpleNamespace(
        concept=concept, value=value, aspects=aspects, provenance=provenance
    )


def _report_with(facts):
    """Minimal stand-in exposing only what the method under test touches."""
    byConcept = defaultdict(list)
    for f in facts:
        byConcept[f.concept].append(f)
    return SimpleNamespace(_factsByConcept=byConcept)


def _collect(report):
    conflicts = []
    InlineReport.reportInconsistentDuplicateFacts(
        report, lambda concept, facts: conflicts.append((concept, list(facts)))
    )
    return conflicts


def test_inconsistent_duplicates_are_reported():
    aspects = {"dim": "memberA"}
    facts = [
        _fact("conceptA", "250", aspects, provenance="Sheet1!$B$2"),
        _fact("conceptA", "310", aspects, provenance="Sheet1!$B$3"),
    ]
    conflicts = _collect(_report_with(facts))

    assert len(conflicts) == 1
    concept, group = conflicts[0]
    assert concept == "conceptA"
    assert {f.value for f in group} == {"250", "310"}


def test_identical_duplicates_are_not_reported():
    aspects = {"dim": "memberA"}
    facts = [
        _fact("conceptA", "250", aspects),
        _fact("conceptA", "250", aspects),
    ]
    assert _collect(_report_with(facts)) == []


def test_same_value_different_dimensions_is_not_a_conflict():
    facts = [
        _fact("conceptA", "250", {"dim": "memberA"}),
        _fact("conceptA", "310", {"dim": "memberB"}),
    ]
    assert _collect(_report_with(facts)) == []


def test_single_fact_per_concept_is_not_a_conflict():
    facts = [
        _fact("conceptA", "250", {"dim": "memberA"}),
        _fact("conceptB", "310", {"dim": "memberA"}),
    ]
    assert _collect(_report_with(facts)) == []
