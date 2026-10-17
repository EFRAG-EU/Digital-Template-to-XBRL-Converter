"""Helpers exposed to Jinja templates."""

from datetime import timedelta

import pytest

from digital_converter_webapp.template_helpers import format_timedelta


@pytest.mark.parametrize(
    ("delta", "expected"),
    [
        (timedelta(seconds=1), "1 second"),
        (timedelta(seconds=2), "2 seconds"),
        (timedelta(minutes=1), "1 minute"),
        (timedelta(hours=1), "1 hour"),
        (timedelta(hours=1, minutes=30), "1 hour 30 minutes"),
        (timedelta(days=2, hours=3), "2 days 3 hours"),
        (
            timedelta(days=1, hours=1, minutes=1, seconds=1),
            "1 day 1 hour 1 minute 1 second",
        ),
        (timedelta(0), ""),
    ],
)
def test_format_timedelta(delta: timedelta, expected: str) -> None:
    assert format_timedelta(delta) == expected
