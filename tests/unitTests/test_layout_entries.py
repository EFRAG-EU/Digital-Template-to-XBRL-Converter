"""The lines of a list section: a concept's facts in several periods become one line."""

from __future__ import annotations

from datetime import date
from unittest.mock import MagicMock

from mireport.report.fact import Fact
from mireport.report.layout.entries import list_entries
from mireport.report.model import FactPeriod, PeriodPart, ReportPeriod
from mireport.report.periods import DurationPeriodHolder
from mireport.taxonomy import Concept, Relationship

CUR = ReportPeriod(
    "cur", DurationPeriodHolder.fromDays(date(2025, 1, 1), date(2025, 12, 31))
)
PRI = ReportPeriod(
    "pri", DurationPeriodHolder.fromDays(date(2024, 1, 1), date(2024, 12, 31))
)
OLD = ReportPeriod(
    "old", DurationPeriodHolder.fromDays(date(2020, 1, 1), date(2020, 12, 31))
)


def _rank(period: ReportPeriod) -> int:
    return {"cur": 0, "pri": 1}.get(period.name, 2)


def _fact(
    period: ReportPeriod, value: str = "v", part: PeriodPart = PeriodPart.Whole
) -> Fact:
    fact = MagicMock(spec=Fact)
    fact.period = FactPeriod(period, part)
    fact.value = value
    return fact


def _rel(name: str) -> Relationship:
    concept = MagicMock(spec=Concept)
    concept.name = name
    return Relationship("role", 1, concept)


def test_a_concept_in_one_period_gets_a_line_per_fact_as_ever() -> None:
    rel = _rel("a")
    first, second = _fact(CUR, "1"), _fact(CUR, "2")
    entries = list_entries({rel: [first, second]}, _rank)
    assert [e.fact for e in entries] == [first, second]
    assert all(e.others == () for e in entries)


def test_current_and_prior_are_one_line_with_the_prior_beside_the_current() -> None:
    rel = _rel("a")
    cur, pri = _fact(CUR), _fact(PRI)
    (entry,) = list_entries({rel: [pri, cur]}, _rank)  # whichever order they arrived in
    assert entry.fact is cur
    assert entry.others == (pri,)
    assert entry.relationship is rel


def test_a_concept_with_only_a_prior_fact_leads_with_it() -> None:
    rel = _rel("a")
    pri = _fact(PRI)
    (entry,) = list_entries({rel: [pri]}, _rank)
    assert entry.fact is pri and entry.others == ()


def test_comparatives_are_in_the_reports_period_order() -> None:
    rel = _rel("a")
    cur, pri, old = _fact(CUR), _fact(PRI), _fact(OLD)
    (entry,) = list_entries({rel: [old, pri, cur]}, _rank)
    assert entry.fact is cur
    assert entry.others == (pri, old)


def test_a_second_fact_in_the_same_period_is_a_line_of_its_own() -> None:
    rel = _rel("a")
    cur1, cur2, pri = _fact(CUR, "1"), _fact(CUR, "2"), _fact(PRI)
    first, second = list_entries({rel: [cur1, cur2, pri]}, _rank)
    assert (first.fact, first.others) == (cur1, (pri,))
    assert (second.fact, second.others) == (cur2, ())


def test_lines_of_one_relationship_share_a_group_and_each_relationship_has_its_own() -> (
    None
):
    a, b = _rel("a"), _rel("b")
    entries = list_entries(
        {a: [_fact(CUR), _fact(CUR)], b: [_fact(CUR)]},
        _rank,
    )
    assert [e.group for e in entries] == [0, 0, 1]


def test_no_facts_no_lines() -> None:
    assert list_entries({}, _rank) == ()


def test_the_closing_balance_leads_and_the_opening_one_is_its_comparative() -> None:
    rel = _rel("a")
    opening = _fact(CUR, "o", PeriodPart.Start)
    closing = _fact(CUR, "c", PeriodPart.End)
    prior = _fact(PRI, "p", PeriodPart.End)
    (entry,) = list_entries({rel: [prior, opening, closing]}, _rank)
    assert entry.fact is closing
    assert entry.others == (opening, prior)
