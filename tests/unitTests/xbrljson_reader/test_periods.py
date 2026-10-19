"""How an xBRL-JSON report's periods are declared: from its facts, with nothing inferred.

In OIM every fact says its own period. The reader declares each duration the facts use, makes the
one with the most facts the default, and never calls any of them the prior one.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import date
from typing import Any

import pytest
from tests.unitTests.synthetic_taxonomy import (
    GROUP_ENTRY_POINT,
    document,
    fact,
    group_taxonomy_json,
    qname,
)

from mireport.conversionresults import ConversionResultsBuilder
from mireport.report import InlineReport
from mireport.report.model import PeriodRole
from mireport.taxonomy import Taxonomy
from mireport.xbrljson_reader import XbrlJsonProcessor


@pytest.fixture(autouse=True)
def _group_taxonomy(
    register_taxonomy: Callable[[dict[str, Any]], Taxonomy],
) -> None:
    register_taxonomy(group_taxonomy_json())


def _period(start: str, end: str) -> str:
    return f"{start}T00:00:00/{end}T00:00:00"


YEAR_2025 = _period("2025-01-01", "2026-01-01")
YEAR_2024 = _period("2024-01-01", "2025-01-01")
YEAR_2022 = _period("2022-01-01", "2023-01-01")


def _facts_in(*periods: tuple[str, int]) -> dict[str, Any]:
    """A document with that many facts in each period, every fact with a value of its own."""
    facts = {
        f"{period}-{n}": fact(qname("Total"), str(n + 1), period=period)
        for period, count in periods
        for n in range(count)
    }
    return document(GROUP_ENTRY_POINT, facts)


def _report(doc: dict[str, Any]) -> InlineReport:
    return XbrlJsonProcessor(doc, ConversionResultsBuilder()).createReport()


def _last_years(report: InlineReport) -> list[int]:
    return [p.duration.lastDay.year for p in report.reportingPeriods]


def test_one_period_is_declared_and_is_the_default() -> None:
    report = _report(_facts_in((YEAR_2025, 2)))
    assert _last_years(report) == [2025]
    assert report.defaultPeriod.lastDay == date(2025, 12, 31)


def test_the_period_with_the_most_facts_is_the_default() -> None:
    report = _report(_facts_in((YEAR_2024, 3), (YEAR_2025, 1)))
    assert report.defaultPeriod.lastDay == date(2024, 12, 31)


def test_a_tie_goes_to_the_period_that_ends_last() -> None:
    report = _report(_facts_in((YEAR_2024, 2), (YEAR_2025, 2)))
    assert report.defaultPeriod.lastDay == date(2025, 12, 31)


def test_every_period_is_declared_the_default_first_then_earliest_first() -> None:
    report = _report(_facts_in((YEAR_2022, 1), (YEAR_2025, 3), (YEAR_2024, 1)))
    assert _last_years(report) == [2025, 2022, 2024]


def test_no_period_is_ever_the_prior_one() -> None:
    # 2024 ends exactly a year before 2025 does, and still is not called the prior period.
    report = _report(_facts_in((YEAR_2025, 2), (YEAR_2024, 1)))
    assert report.priorReportPeriod is None
    assert {
        p.duration.lastDay.year: report.periodRole(p) for p in report.reportingPeriods
    } == {2025: PeriodRole.CURRENT, 2024: PeriodRole.OTHER}


def test_two_spellings_of_one_period_are_one_period() -> None:
    next_day = "2024-12-31T24:00:00/2025-12-31T24:00:00"
    report = _report(_facts_in((YEAR_2025, 1), (next_day, 1)))
    assert _last_years(report) == [2025]


def test_the_names_follow_from_the_dates() -> None:
    report = _report(_facts_in((YEAR_2025, 1), (YEAR_2024, 1)))
    assert [p.name for p in report.reportingPeriods] == [
        "p20250101_20251231",
        "p20240101_20241231",
    ]


def test_the_document_makes_no_claim_about_a_prior_period() -> None:
    html = (
        _report(_facts_in((YEAR_2025, 1), (YEAR_2024, 1)))
        .getInlineReport()
        .fileContent.decode("utf-8")
    )
    assert "Prior period" not in html
