"""FactBuilder's two ways to set a percentage.

setPercentageFact takes what the XBRL fact holds (a fraction and XBRL decimals); setPercentageValue
takes a spreadsheet's display decimals. Both show the fraction x100 and tag it ix:scale -2.
"""

from __future__ import annotations

from decimal import Decimal
from unittest.mock import MagicMock

import pytest

from mireport.report.factbuilder import FactBuilder


def _builder() -> FactBuilder:
    return FactBuilder(MagicMock())


def _held(fb: FactBuilder) -> tuple[object, dict]:
    """The value and the typed scale and decimals the builder holds, as a dict by name."""
    return fb._value, {"numeric-scale": fb._scale, "decimals": fb._decimals}


def test_a_percentage_fact_shows_the_fraction_x100_and_keeps_xbrl_decimals() -> None:
    value, aspects = _held(_builder().setPercentageFact(Decimal("0.1250"), 4))
    assert value == 12.5  # what the page shows: 12.50
    assert aspects["numeric-scale"] == -2
    assert aspects["decimals"] == 4  # XBRL's own; not 6


def test_decimals_inf_is_kept() -> None:
    _, aspects = _held(_builder().setPercentageFact(Decimal("0.125"), "INF"))
    assert aspects["decimals"] == "INF"


def test_a_whole_percentage_is_shown_as_an_integer() -> None:
    value, _ = _held(_builder().setPercentageFact(Decimal("0.5"), 2))
    assert value == 50
    assert isinstance(value, int)


def test_a_decimal_input_avoids_float_noise() -> None:
    value, _ = _held(_builder().setPercentageFact(Decimal("0.07"), 2))
    assert value == 7 and isinstance(value, int)
    assert 0.07 * 100 != 7  # the noise a float would have brought


def test_the_display_decimals_route_adds_the_scale_amount() -> None:
    """The Excel reader's route, unchanged: 0.125 shown to 2 decimals is 0.1250 in XBRL."""
    value, aspects = _held(_builder().setPercentageValue(0.125, 2))
    assert value == 12.5
    assert aspects["numeric-scale"] == -2
    assert aspects["decimals"] == 4


def test_the_display_decimals_route_leaves_inf_alone() -> None:
    _, aspects = _held(_builder().setPercentageValue(0.125, "INF"))
    assert aspects["decimals"] == "INF"


def test_the_whole_number_form_is_not_scaled() -> None:
    value, aspects = _held(
        _builder().setPercentageValue(12.5, 2, inputIsDecimalForm=False)
    )
    assert value == 12.5
    assert aspects["numeric-scale"] is None
    assert aspects["decimals"] == 2


@pytest.mark.parametrize("float_input", [0.07, 0.29, 0.1234])
def test_the_float_route_is_the_legacy_arithmetic(float_input: float) -> None:
    """setPercentageValue keeps float x100 so Excel-derived facts stay byte-for-byte as before."""
    value, _ = _held(_builder().setPercentageValue(float_input, 2))
    assert value == float_input * 10**2
