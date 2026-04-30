from __future__ import annotations

from abc import ABC
from dataclasses import dataclass
from datetime import date, datetime


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
    start: datetime | date
    end: datetime | date


@dataclass(slots=True, frozen=True, eq=True)
class InstantPeriodHolder(PeriodHolder):
    instant: datetime | date


_Period = InstantPeriodHolder | DurationPeriodHolder
