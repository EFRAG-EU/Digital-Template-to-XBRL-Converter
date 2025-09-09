from decimal import Decimal

import pytest
from babel.core import Locale

from mireport.localise import localise_and_format_number  # adjust import as needed


@pytest.mark.parametrize(
    "number,decimal_places,expected",
    [
        # Finite decimal places, ROUND_HALF_EVEN
        (1234.5678, 2, "1,234.57"),  # normal rounding
        (1234.5, 0, "1,234"),  # 1234.5 rounds down to even
        (1235.5, 0, "1,236"),  # 1235.5 rounds up to even
        (Decimal("2.345"), 2, "2.34"),
        (Decimal("2.355"), 2, "2.36"),
        (-1234.5, 0, "-1,234"),
        (-1235.5, 0, "-1,236"),
    ],
)
def test_finite_decimal_places_no_locale(number, decimal_places, expected):
    assert localise_and_format_number(number, decimal_places) == expected


@pytest.mark.parametrize(
    "number,expected",
    [
        # INF facts: preserve all digits, no rounding, no trimming
        (1234.5678, "1,234.5678"),
        (Decimal("1234.5678900"), "1,234.5678900"),
        ("1234.5678900", "1,234.5678900"),
        (1234, "1,234"),  # integer
        (Decimal("1234"), "1,234"),
        (0.000123, "0.000123"),
        ("1e-6", "0.000001"),  # scientific notation expanded
        (Decimal("1e-6"), "0.000001"),
        (-1200.0, "-1,200.0"),  # negative integer as float
    ],
)
def test_inf_no_locale(number, expected):
    assert localise_and_format_number(number, "INF") == expected


@pytest.mark.parametrize(
    "number,decimal_places,expected",
    [
        # Finite decimal places with locale (en_US)
        (1234.5678, 2, "1,234.57"),
        (1234.5, 0, "1,234"),
        (1235.5, 0, "1,236"),
        (-1234.5, 0, "-1,234"),
    ],
)
def test_finite_decimal_places_with_locale(number, decimal_places, expected):
    result = localise_and_format_number(
        number, decimal_places, locale=Locale("en", "US")
    )
    assert result == expected


@pytest.mark.parametrize(
    "number,expected",
    [
        # INF with locale (preserve digits exactly)
        (1234.5678, "1,234.5678"),
        pytest.param(
            Decimal("1234.5678900"),
            "1,234.5678900",
            marks=pytest.mark.xfail(
                reason="Trailing zeros are truncated unexpectedly by babel"
            ),
        ),
        ("1e-6", "0.000001"),
        (-1200.0, "-1,200"),
    ],
)
def test_inf_with_locale(number, expected):
    result = localise_and_format_number(number, "INF", locale=Locale("en", "US"))
    assert result == expected


@pytest.mark.parametrize(
    "invalid_input",
    [
        None,
        [],
        {},
        object(),
    ],
)
def test_invalid_types(invalid_input):
    with pytest.raises(TypeError):
        localise_and_format_number(invalid_input, 2)
