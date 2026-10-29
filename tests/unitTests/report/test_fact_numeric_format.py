"""How a numeric fact is shown: rounded to its decimals (less any scale) and padded to them, as its
source rounds."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from mireport.localise import Rounding
from mireport.report.fact import Fact
from mireport.typealiases import DecimalPlaces


def _shown(
    value: float,
    decimals: DecimalPlaces | None,
    scale: int | None,
    rounding: Rounding | None = None,
) -> str:
    report = MagicMock(outputLocale=None)
    fact = Fact(
        MagicMock(),
        value,
        report,
        period=MagicMock(),
        decimals=decimals,
        scale=scale,
        rounding=rounding,
    )
    return str(fact._format_numeric_value())


@pytest.mark.parametrize(
    ("value", "decimals", "scale", "shown"),
    [
        (12, 0, None, "12"),
        (1235, 0, 3, "1,235.000"),  # 0 decimals is not INF, and the scale applies
        (1234.567, -3, 3, "1,235"),  # a cell shown in thousands
        (12.3456, 2, None, "12.35"),  # rounded to its decimals
        (12.5, 0, None, "12"),  # half to even
        (13.5, 0, None, "14"),
        (12.3, 2, None, "12.30"),  # padded
        (1234567, -3, None, "1,235,000"),  # negative decimals round too
        (12.345, 4, -2, "12.34"),  # a percentage: 0.12345 at decimals 4, half to even
        (12.5, "INF", None, "12.5"),
        (12.5, None, None, "12.5"),
    ],
)
def test_a_number_is_rounded_and_padded_to_its_decimals(
    value: float, decimals: DecimalPlaces | None, scale: int | None, shown: str
) -> None:
    assert _shown(value, decimals, scale) == shown


@pytest.mark.parametrize(
    ("value", "decimals", "rounding", "shown"),
    [
        (12.5, 0, Rounding.HALF_UP, "13"),  # as Excel shows it
        (12.5, 0, Rounding.HALF_EVEN, "12"),
        (-12.5, 0, Rounding.HALF_UP, "-13"),  # away from zero
        (1.005, 2, Rounding.HALF_UP, "1.01"),  # the value written, not its float
    ],
)
def test_a_number_rounds_as_its_source_does(
    value: float, decimals: DecimalPlaces, rounding: Rounding, shown: str
) -> None:
    assert _shown(value, decimals, None, rounding) == shown
