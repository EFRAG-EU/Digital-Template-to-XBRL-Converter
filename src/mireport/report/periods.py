"""XBRL periods as OIM has them: datetimes, with a duration running up to (not including) its end.

``2025-01-01`` to ``2025-12-31`` as people say it is ``2025-01-01T00:00`` to ``2026-01-01T00:00`` here, so
the start of one year *is* the end of the one before, and an instant is just a datetime. The only
day arithmetic is in this module: ``DurationPeriodHolder.fromDays`` going in, ``firstDay``/``lastDay``
coming out for whoever (aoix, a reader of the page) can only say whole days.
"""

from __future__ import annotations

from abc import ABC
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta

from mireport.exceptions import InlineReportException

_ONE_DAY = timedelta(days=1)


def _require_datetime(value: object, what: str) -> None:
    if not isinstance(value, datetime):
        raise InlineReportException(
            f"{what} must be a datetime (a date is a whole day, so say which end of it), "
            f"not {value!r}."
        )


def _whole_day(value: datetime, what: str) -> date:
    if value.time() != time.min:
        raise InlineReportException(
            f"{what} {value.isoformat()} is not at midnight; only whole days can be written as dates."
        )
    return value.date()


class PeriodHolder(ABC):
    @property
    def isInstant(self) -> bool:
        """
        Returns True if this period holder is an InstantPeriodHolder.
        """
        return isinstance(self, InstantPeriodHolder)

    @property
    def isDuration(self) -> bool:
        """
        Returns True if this period holder is a DurationPeriodHolder.
        """
        return isinstance(self, DurationPeriodHolder)


@dataclass(slots=True, frozen=True, eq=True)
class DurationPeriodHolder(PeriodHolder):
    start: datetime
    end: datetime

    def __post_init__(self) -> None:
        _require_datetime(self.start, "A duration's start")
        _require_datetime(self.end, "A duration's end")
        if self.end <= self.start:
            raise InlineReportException(
                f"A duration must end after it starts, not {self.start} to {self.end}."
            )

    def __str__(self) -> str:
        return f"{self.start.isoformat()}/{self.end.isoformat()}"

    @classmethod
    def fromDays(cls, first: date, last: date) -> DurationPeriodHolder:
        """The duration that includes ``first`` and ``last``, both whole days."""
        return cls(
            datetime.combine(first, time.min),
            datetime.combine(last, time.min) + _ONE_DAY,
        )

    @property
    def startInstant(self) -> InstantPeriodHolder:
        return InstantPeriodHolder(self.start)

    @property
    def endInstant(self) -> InstantPeriodHolder:
        return InstantPeriodHolder(self.end)

    @property
    def isWholeDays(self) -> bool:
        """Whether it starts and ends at midnight, so it can be written as days."""
        return self.start.time() == time.min and self.end.time() == time.min

    @property
    def firstDay(self) -> date:
        """The first day included, for aoix and for people."""
        return _whole_day(self.start, "The start")

    @property
    def lastDay(self) -> date:
        """The last day included, for aoix and for people."""
        return _whole_day(self.end, "The end") - _ONE_DAY

    @property
    def yearName(self) -> str | None:
        """What people call the duration when it is a year to the day: ``2025`` for a calendar
        year, ``2024–2025`` for one that starts later in the year. None for any other length."""
        first, last = self.firstDay, self.lastDay
        try:
            yearOn = first.replace(year=first.year + 1)
        except ValueError:  # from 29 February
            return None
        if last != yearOn - _ONE_DAY:
            return None
        if (first.month, first.day) == (1, 1):
            return str(first.year)
        return f"{first.year}\u2013{last.year}"

    @property
    def startInstantDay(self) -> date:
        """The day the start instant ends: the last day before this period (2024-12-31 for
        2025), as XBRL dates an opening balance."""
        return self.firstDay - _ONE_DAY

    @property
    def startName(self) -> str:
        """What people call the instant this period starts at, as the end of the period
        before: the year before for a calendar year, else the day before."""
        if self._isCalendarYear:
            return str(self.firstDay.year - 1)
        return self.startInstantDay.isoformat()

    @property
    def endName(self) -> str:
        """What people call the instant this period ends at: its year for a calendar year,
        else its last day (``2024–2025`` would read as a span, not a day). Never the same as
        its ``startName``, since a period is at least a day long."""
        if self._isCalendarYear:
            return str(self.firstDay.year)
        return self.lastDay.isoformat()

    @property
    def _isCalendarYear(self) -> bool:
        first = self.firstDay
        return self.yearName is not None and (first.month, first.day) == (1, 1)


@dataclass(slots=True, frozen=True, eq=True)
class InstantPeriodHolder(PeriodHolder):
    instant: datetime

    def __post_init__(self) -> None:
        _require_datetime(self.instant, "An instant")

    def __str__(self) -> str:
        return self.instant.isoformat()


_Period = InstantPeriodHolder | DurationPeriodHolder
