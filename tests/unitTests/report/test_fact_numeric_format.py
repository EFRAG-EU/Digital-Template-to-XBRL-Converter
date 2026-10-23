"""How a numeric fact is shown: never rounded, its decimals (less any scale) the least places shown."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from mireport.report.fact import Fact
from mireport.typealiases import DecimalPlaces


def _shown(value: float, decimals: DecimalPlaces | None, scale: int | None) -> str:
    report = MagicMock(outputLocale=None)
    fact = Fact(
        MagicMock(), value, report, period=MagicMock(), decimals=decimals, scale=scale
    )
    return str(fact._format_numeric_value())


@pytest.mark.parametrize(
    ("value", "decimals", "scale", "shown"),
    [
        (12, 0, None, "12"),
        (1235, 0, 3, "1,235.000"),  # 0 decimals is not INF, and the scale applies
        (1235, -3, 3, "1,235"),
        (12.346, 2, None, "12.346"),  # more places than its decimals: shown whole
        (12.5, 0, None, "12.5"),
        (12.3, 2, None, "12.30"),  # fewer: padded
        (12, 4, -2, "12.00"),  # a percentage: 0.1200 at decimals 4
        (12.5, "INF", None, "12.5"),
        (12.5, None, None, "12.5"),
    ],
)
def test_a_number_is_shown_whole_and_padded_to_its_decimals(
    value: float, decimals: DecimalPlaces | None, scale: int | None, shown: str
) -> None:
    assert _shown(value, decimals, scale) == shown
