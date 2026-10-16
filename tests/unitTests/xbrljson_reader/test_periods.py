"""Which of an xBRL-JSON report's periods is the current one, which the prior."""

from __future__ import annotations

import re
from collections.abc import Iterator
from datetime import date
from typing import Any

import pytest
from tests.unitTests.xbrljson_reader import test_dimensional_fallback as base

import mireport.taxonomy as _taxonomy_module
from mireport.conversionresults import ConversionResultsBuilder
from mireport.report import InlineReport
from mireport.report.model import PeriodRole
from mireport.xbrljson_reader import XbrlJsonProcessor


@pytest.fixture(autouse=True)
def _isolated_taxonomy_registry() -> Iterator[None]:
    before = set(_taxonomy_module._TAXONOMIES)
    yield
    for entryPoint in set(_taxonomy_module._TAXONOMIES) - before:
        del _taxonomy_module._TAXONOMIES[entryPoint]


def _period(start: str, end: str) -> str:
    return f"{start}T00:00:00/{end}T00:00:00"


def _facts_in(*periods: tuple[str, int]) -> dict[str, Any]:
    """A document whose facts (tp:Total, which has no dimension) are in the given periods; the
    integer is how many facts each period has."""
    doc = base._document()
    doc["facts"] = {}
    for period, count in periods:
        for n in range(count):
            fact = base._fact("tp:Total", str(n + 1))
            fact["dimensions"]["period"] = period
            # one fact per value would collide as duplicates; give each its own value
            doc["facts"][f"{period}-{n}"] = fact
    return doc


def _report(doc: dict[str, Any]) -> InlineReport:
    base._load()
    return XbrlJsonProcessor(doc, ConversionResultsBuilder()).createReport()


CURRENT = _period("2025-01-01", "2026-01-01")
PRIOR = _period("2024-01-01", "2025-01-01")
OLDER = _period("2022-01-01", "2023-01-01")


def test_one_period_is_the_current_one() -> None:
    report = _report(_facts_in((CURRENT, 2)))
    assert report.defaultReportPeriod.name == "cur"
    assert report.priorReportPeriod is None


def test_the_current_period_is_the_latest_not_the_most_frequent() -> None:
    report = _report(_facts_in((PRIOR, 3), (CURRENT, 1)))
    assert report.defaultPeriod.lastDay == date(2025, 12, 31)
    assert report.priorPeriod is not None and report.priorPeriod.lastDay == date(
        2024, 12, 31
    )


def test_the_prior_period_is_the_one_ending_a_year_earlier() -> None:
    report = _report(_facts_in((CURRENT, 1), (PRIOR, 1)))
    roles = {p.name: report.periodRole(p) for p in report.reportingPeriods}
    assert roles == {"cur": PeriodRole.CURRENT, "prior": PeriodRole.PRIOR}


def test_an_older_unrelated_period_is_declared_but_is_neither() -> None:
    report = _report(_facts_in((CURRENT, 1), (PRIOR, 1), (OLDER, 1)))
    roles = {p.name: report.periodRole(p) for p in report.reportingPeriods}
    assert roles == {
        "cur": PeriodRole.CURRENT,
        "prior": PeriodRole.PRIOR,
        "other1": PeriodRole.OTHER,
    }


def test_with_no_period_a_year_earlier_there_is_no_prior() -> None:
    report = _report(_facts_in((CURRENT, 1), (OLDER, 1)))
    assert report.priorReportPeriod is None
    assert {p.name for p in report.reportingPeriods} == {"cur", "other1"}


def test_a_year_ending_28_february_follows_one_ending_29() -> None:
    current = _period("2024-03-01", "2025-03-01")  # ends 28 Feb 2025
    prior = _period("2023-03-01", "2024-03-01")  # ends 29 Feb 2024 (leap)
    report = _report(_facts_in((current, 1), (prior, 1)))
    assert report.priorReportPeriod is not None


def test_a_period_of_a_different_length_ending_a_year_earlier_is_not_the_prior() -> (
    None
):
    half_year = _period(
        "2024-07-01", "2025-01-01"
    )  # ends when PRIOR does, starts half way
    report = _report(_facts_in((CURRENT, 1), (half_year, 1)))
    assert report.priorReportPeriod is None


def test_the_names_do_not_depend_on_how_many_facts_each_period_has() -> None:
    a = _report(_facts_in((CURRENT, 1), (PRIOR, 5)))
    assert [p.name for p in a.reportingPeriods] == ["cur", "prior"]


def test_the_document_says_both_periods() -> None:
    base._load()
    html = (
        XbrlJsonProcessor(
            _facts_in((CURRENT, 1), (PRIOR, 1)), ConversionResultsBuilder()
        )
        .createReport()
        .getInlineReport()
        .fileContent.decode("utf-8")
    )
    assert "Prior period" in html
    assert re.search(r"Comparative figures are for the prior period", html)
