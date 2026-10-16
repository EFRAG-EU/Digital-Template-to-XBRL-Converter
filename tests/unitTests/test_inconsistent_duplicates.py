from __future__ import annotations

from types import SimpleNamespace

import pytest

from mireport.report.inlinereport import InlineReport


class _Concept:
    def __init__(self, numeric: bool = False) -> None:
        self.isNumeric = numeric
        self.isBoolean = False


def _fact(concept, value, key=("dim", "memberA"), provenance=None, decimals=None):
    """Stand-in carrying only what duplicate detection reads: ``key`` is what makes two
    facts duplicates (concept, period, unit, dimensions); ``decimals`` is how accurate."""
    return SimpleNamespace(
        concept=concept,
        value=value,
        duplicateKey=(concept, key),
        decimals=decimals,
        enumeration=None,
        provenance=provenance,
    )


def _report_with(facts):
    """Minimal stand-in exposing only what the method under test touches."""
    report = SimpleNamespace(_facts=list(facts))
    report.duplicates = lambda: InlineReport.duplicates(report)
    return report


def _collect(report):
    conflicts = []
    InlineReport.reportInconsistentDuplicateFacts(
        report, lambda concept, facts: conflicts.append((concept, list(facts)))
    )
    return conflicts


def test_inconsistent_duplicates_are_reported():
    concept = _Concept()
    facts = [
        _fact(concept, "250", provenance="Sheet1!$B$2"),
        _fact(concept, "310", provenance="Sheet1!$B$3"),
    ]
    conflicts = _collect(_report_with(facts))

    assert len(conflicts) == 1
    reported, group = conflicts[0]
    assert reported is concept
    assert {f.value for f in group} == {"250", "310"}


def test_identical_duplicates_are_not_reported():
    concept = _Concept()
    facts = [_fact(concept, "250"), _fact(concept, "250")]
    assert _collect(_report_with(facts)) == []


def test_same_concept_different_dimensions_is_not_a_conflict():
    concept = _Concept()
    facts = [
        _fact(concept, "250", key=("dim", "memberA")),
        _fact(concept, "310", key=("dim", "memberB")),
    ]
    assert _collect(_report_with(facts)) == []


def test_single_fact_per_concept_is_not_a_conflict():
    facts = [_fact(_Concept(), "250"), _fact(_Concept(), "310")]
    assert _collect(_report_with(facts)) == []


def test_each_set_is_reported_once_whole():
    concept = _Concept()
    facts = [_fact(concept, v) for v in ("1", "2", "3")]
    [(_, group)] = _collect(_report_with(facts))
    assert group == facts


@pytest.mark.parametrize(
    "first, second, reported",
    [
        # Same accuracy, different values: inconsistent even though the intervals touch.
        ((2000, -3), (3000, -3), True),
        # Different accuracy and the intervals do not overlap: inconsistent (WGN 3.3).
        ((100, 0), (200, 1), True),
        # Different accuracy but the intervals overlap: consistent, so not reported.
        ((100, 0), (100.4, 1), False),
        # Same value, different accuracy: consistent.
        ((100, 0), (100, 2), False),
    ],
)
def test_numbers_are_inconsistent_by_the_wgn_rules(first, second, reported):
    concept = _Concept(numeric=True)
    facts = [_fact(concept, v, decimals=d) for v, d in (first, second)]
    assert bool(_collect(_report_with(facts))) is reported
