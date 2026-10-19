"""OIM periods: an instant, or a duration ``start/end``, each an xs:dateTime.

A date alone is not an OIM period, and the end of a day may be written ``T24:00:00`` (the start of
the next). The report model has no time zones, so a period that names one is refused rather than
shifted.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta

from mireport.exceptions import InlineReportException
from mireport.report.periods import DurationPeriodHolder, InstantPeriodHolder, _Period
from mireport.xbrljson_reader.document import INVALID_PERIOD, FactProblem

_DATE_TIME = re.compile(
    r"(?P<date>-?\d{4,}-\d{2}-\d{2})T(?P<time>\d{2}:\d{2}:\d{2}(?:\.\d+)?)"
    r"(?P<zone>Z|[+-]\d{2}:\d{2})?"
)


def parseDateTime(text: str) -> datetime:
    if (match := _DATE_TIME.fullmatch(text)) is None:
        raise FactProblem(
            INVALID_PERIOD, f"{text!r} is not a dateTime (a date and a time)."
        )
    if match["zone"]:
        raise FactProblem(
            INVALID_PERIOD, f"{text!r} has a time zone, which is not supported."
        )
    date, time = match["date"], match["time"]
    try:
        if time.startswith("24:") and float(time[3:].replace(":", "")) == 0:
            return datetime.fromisoformat(f"{date}T00:00:00") + timedelta(days=1)
        return datetime.fromisoformat(f"{date}T{time}")
    except ValueError as e:
        raise FactProblem(INVALID_PERIOD, f"{text!r} is not a dateTime: {e}") from e


def parsePeriod(text: str) -> _Period:
    """Any OIM period: a duration, or an instant."""
    if "/" not in text:
        return InstantPeriodHolder(parseDateTime(text))
    startText, endText = text.split("/", 1)
    try:
        return DurationPeriodHolder(parseDateTime(startText), parseDateTime(endText))
    except InlineReportException as e:
        raise FactProblem(INVALID_PERIOD, f"{text!r}: {e}") from e


def parseDuration(text: str) -> DurationPeriodHolder:
    """A report period: a duration of whole days (aoix cannot say anything else)."""
    if not isinstance(period := parsePeriod(text), DurationPeriodHolder):
        raise FactProblem(INVALID_PERIOD, f"{text!r} is an instant, not a duration.")
    try:
        period.firstDay, period.lastDay  # noqa: B018 - checks they are whole days
    except InlineReportException as e:
        raise FactProblem(
            INVALID_PERIOD, f"Report periods must be whole days; got {text!r}: {e}"
        ) from e
    return period
