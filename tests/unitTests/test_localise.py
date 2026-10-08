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
        (Decimal(1234), "1,234"),
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


@pytest.mark.parametrize(
    "number,decimal_places,expected",
    [
        # Values needing more than 28 significant digits used to raise
        # decimal.InvalidOperation from babel's quantize (default decimal
        # context precision) when a locale was supplied.
        (1e28, 0, "10,000,000,000,000,000,000,000,000,000"),
        (1e30, 0, "1,000,000,000,000,000,000,000,000,000,000"),
        (
            "123456789012345678901234567890",
            0,
            "123,456,789,012,345,678,901,234,567,890",
        ),
        (1e30, 2, "1,000,000,000,000,000,000,000,000,000,000.00"),
        # Modest value but many decimal places also exceeds 28 digits total
        (Decimal(1234567), 25, "1,234,567." + "0" * 25),
    ],
)
def test_huge_values_with_locale(number, decimal_places, expected):
    result = localise_and_format_number(
        number, decimal_places, locale=Locale("en", "US")
    )
    assert result == expected


@pytest.mark.parametrize(
    "number,expected",
    [
        (1e30, "1,000,000,000,000,000,000,000,000,000,000"),
        (
            Decimal("123456789012345678901234567890.5"),
            "123,456,789,012,345,678,901,234,567,890.5",
        ),
    ],
)
def test_huge_values_inf_with_locale(number, expected):
    result = localise_and_format_number(number, "INF", locale=Locale("en", "US"))
    assert result == expected


@pytest.mark.parametrize(
    "number,decimal_places,expected",
    [
        # No-locale path uses plain f-string formatting; pin its behaviour
        # for the same huge values.
        (1e30, 0, "1,000,000,000,000,000,000,000,000,000,000"),
        (
            "123456789012345678901234567890",
            0,
            "123,456,789,012,345,678,901,234,567,890",
        ),
        (Decimal(1234567), 25, "1,234,567." + "0" * 25),
    ],
)
def test_huge_values_no_locale(number, decimal_places, expected):
    assert localise_and_format_number(number, decimal_places) == expected


@pytest.mark.parametrize(
    "non_finite",
    [
        Decimal("NaN"),
        Decimal("Infinity"),
        Decimal("-Infinity"),
        float("nan"),
        float("inf"),
        float("-inf"),
        "NaN",
        "Infinity",
    ],
)
@pytest.mark.parametrize("locale", [None, Locale("en", "US")])
@pytest.mark.parametrize("decimal_places", [2, "INF"])
def test_non_finite_values_raise(non_finite, locale, decimal_places):
    with pytest.raises(ValueError):
        localise_and_format_number(non_finite, decimal_places, locale=locale)
