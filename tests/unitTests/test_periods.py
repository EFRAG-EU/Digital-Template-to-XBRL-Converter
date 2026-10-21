"""Periods are OIM's: datetimes, a duration running up to (not including) its end."""

from __future__ import annotations

from datetime import date, datetime

import pytest

from mireport.exceptions import InlineReportException
from mireport.report.periods import DurationPeriodHolder, InstantPeriodHolder

CUR = DurationPeriodHolder.fromDays(date(2025, 1, 1), date(2025, 12, 31))
PRIOR = DurationPeriodHolder.fromDays(date(2024, 1, 1), date(2024, 12, 31))


def test_a_duration_from_days_runs_to_the_start_of_the_day_after() -> None:
    assert CUR.start == datetime(2025, 1, 1)
    assert CUR.end == datetime(2026, 1, 1)


def test_first_and_last_day_are_the_days_it_was_made_from() -> None:
    assert (CUR.firstDay, CUR.lastDay) == (date(2025, 1, 1), date(2025, 12, 31))


def test_the_start_of_one_year_is_the_end_of_the_year_before() -> None:
    assert (
        CUR.startInstant
        == PRIOR.endInstant
        == InstantPeriodHolder(datetime(2025, 1, 1))
    )
    assert CUR.startInstant != CUR.endInstant


def test_a_leap_day_is_a_day() -> None:
    leap = DurationPeriodHolder.fromDays(date(2024, 2, 29), date(2024, 2, 29))
    assert leap.end - leap.start == datetime(2024, 3, 1) - datetime(2024, 2, 29)
    assert leap.firstDay == leap.lastDay == date(2024, 2, 29)


def test_a_plain_date_is_refused() -> None:
    with pytest.raises(InlineReportException, match="datetime"):
        DurationPeriodHolder(date(2025, 1, 1), date(2025, 12, 31))  # type: ignore[arg-type]
    with pytest.raises(InlineReportException, match="datetime"):
        InstantPeriodHolder(date(2025, 1, 1))  # type: ignore[arg-type]


def test_a_duration_must_have_length() -> None:
    with pytest.raises(InlineReportException, match="end after it starts"):
        DurationPeriodHolder(datetime(2025, 1, 1), datetime(2025, 1, 1))


def test_only_whole_days_can_be_written_as_dates() -> None:
    part_day = DurationPeriodHolder(datetime(2025, 1, 1, 9), datetime(2025, 1, 2))
    with pytest.raises(InlineReportException, match="midnight"):
        _ = part_day.firstDay
    assert part_day.lastDay == date(2025, 1, 1)


def test_periods_are_written_as_oim_does() -> None:
    assert str(CUR) == "2025-01-01T00:00:00/2026-01-01T00:00:00"
    assert str(CUR.startInstant) == "2025-01-01T00:00:00"


@pytest.mark.parametrize(
    ("first", "last", "name"),
    [
        (date(2025, 1, 1), date(2025, 12, 31), "2025"),
        (date(2024, 7, 1), date(2025, 6, 30), "2024–2025"),
        (date(2024, 1, 1), date(2025, 6, 30), None),  # eighteen months
        (date(2025, 1, 1), date(2025, 6, 30), None),
        (date(2024, 2, 29), date(2025, 2, 28), None),
    ],
)
def test_a_year_to_the_day_is_named_by_its_year(
    first: date, last: date, name: str | None
) -> None:
    assert DurationPeriodHolder.fromDays(first, last).yearName == name
