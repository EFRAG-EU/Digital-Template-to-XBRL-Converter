"""The value types a Fact is made of: units, typed and explicit dimension values, report periods."""

from __future__ import annotations

from datetime import date
from unittest.mock import MagicMock

import pytest

from mireport.exceptions import InlineReportException
from mireport.report.model import (
    ExplicitDimensionValue,
    ReportPeriod,
    TypedDimensionValue,
    Unit,
)
from mireport.report.periods import DurationPeriodHolder
from mireport.taxonomy import Concept
from mireport.xml import getBootstrapQNameMaker

_Q = getBootstrapQNameMaker()
T = _Q.fromString("utr:tCO2e")
EUR = _Q.fromString("iso4217:EUR")
GBP = _Q.fromString("iso4217:GBP")
M = _Q.fromString("utr:m")
S = _Q.fromString("utr:s")
PURE = _Q.fromString("xbrli:pure")


class TestUnit:
    def test_a_simple_unit(self) -> None:
        unit = Unit.simple(T)
        assert str(unit) == "utr:tCO2e"
        assert unit.measure == T
        assert not unit.is_divide
        assert not unit.is_currency

    def test_a_currency_is_a_single_iso_4217_measure(self) -> None:
        assert Unit.simple(EUR).is_currency
        assert not Unit.simple(T).is_currency
        assert not Unit.divide(EUR, EUR).is_currency

    def test_a_divide_unit(self) -> None:
        unit = Unit.divide(T, EUR)
        assert str(unit) == "utr:tCO2e/iso4217:EUR"
        assert unit.is_divide
        assert unit.numerator == (T,) and unit.denominator == (EUR,)

    def test_several_measures_are_bracketed_when_divided(self) -> None:
        # each side is in canonical order, with brackets only past one measure
        assert str(Unit.divide([M, T], S)) == "(utr:m*utr:tCO2e)/utr:s"
        assert str(Unit.divide([T, M], S)) == "(utr:m*utr:tCO2e)/utr:s"
        assert str(Unit.divide(T, [M, S])) == "utr:tCO2e/(utr:m*utr:s)"
        assert str(Unit.divide([M, T], [S, EUR])) == (
            "(utr:m*utr:tCO2e)/(iso4217:EUR*utr:s)"
        )

    def test_a_product_unit_with_no_denominator_is_not_bracketed(self) -> None:
        assert str(Unit((T, M))) == "utr:m*utr:tCO2e"

    def test_measure_order_does_not_matter_for_equality(self) -> None:
        assert Unit.divide([T, M], S) == Unit.divide([M, T], S)
        assert hash(Unit.divide([T, M], S)) == hash(Unit.divide([M, T], S))
        assert Unit.simple(T) != Unit.simple(M)

    def test_a_unit_needs_a_numerator(self) -> None:
        with pytest.raises(InlineReportException, match="numerator"):
            Unit(())

    def test_a_divide_unit_needs_a_denominator(self) -> None:
        with pytest.raises(InlineReportException, match="denominator"):
            Unit.divide(T, [])

    def test_a_non_simple_unit_has_no_single_measure(self) -> None:
        with pytest.raises(InlineReportException, match="not a simple unit"):
            _ = Unit.divide(T, EUR).measure

    @pytest.mark.parametrize(
        "text",
        [
            "utr:tCO2e",
            "utr:tCO2e/iso4217:EUR",
            "(utr:m*utr:tCO2e)/utr:s",
            "utr:tCO2e/(utr:m*utr:s)",
            "utr:m*utr:tCO2e",
        ],
    )
    def test_parse_and_print_round_trip(self, text: str) -> None:
        assert str(Unit.parse(text, _Q.fromString)) == text

    def test_parse_accepts_unbracketed_and_unsorted_input(self) -> None:
        assert Unit.parse("utr:tCO2e*utr:m/utr:s", _Q.fromString) == Unit.divide(
            [M, T], S
        )

    def test_parse_refuses_two_slashes(self) -> None:
        with pytest.raises(InlineReportException, match="more than one"):
            Unit.parse("utr:m/utr:s/utr:t", _Q.fromString)


def _typed_dimension() -> Concept:
    dimension = MagicMock(spec=Concept)
    dimension.qname = _Q.fromString("xbrli:Axis")
    dimension.typedElement.qname = _Q.fromString("xbrli:Wrapper")
    return dimension


class TestTypedDimensionValue:
    def test_the_value_is_kept_as_given(self) -> None:
        value = TypedDimensionValue.of(_typed_dimension(), "R&D <1> it's")
        assert value.value == "R&D <1> it's"  # aoix escapes; we do not

    def test_control_characters_a_context_cannot_carry_are_dropped(self) -> None:
        assert (
            TypedDimensionValue.of(_typed_dimension(), "a\tb\nc\r\x0bd\x0ce").value
            == "abcde"
        )

    def test_non_text_values_are_written_as_text(self) -> None:
        d = _typed_dimension()
        assert TypedDimensionValue.of(d, 7).value == "7"
        assert TypedDimensionValue.of(d, 0.5).value == "0.5"
        assert TypedDimensionValue.of(d, True).value == "true"
        assert TypedDimensionValue.of(d, False).value == "false"
        assert TypedDimensionValue.of(d, date(2025, 12, 31)).value == "2025-12-31"

    def test_the_wrapper_is_the_dimensions_own_element(self) -> None:
        assert TypedDimensionValue.of(_typed_dimension(), "x").wrapper == _Q.fromString(
            "xbrli:Wrapper"
        )

    def test_a_dimension_with_no_wrapper_is_not_typed(self) -> None:
        explicit = MagicMock(spec=Concept)
        explicit.qname = _Q.fromString("xbrli:Axis")
        explicit.typedElement = None
        with pytest.raises(InlineReportException, match="not a typed dimension"):
            _ = TypedDimensionValue.of(explicit, "x").wrapper

    def test_equal_values_are_equal_and_hash_alike(self) -> None:
        d = _typed_dimension()
        assert TypedDimensionValue.of(d, "1") == TypedDimensionValue.of(d, 1)
        assert len({TypedDimensionValue.of(d, "1"), TypedDimensionValue.of(d, 1)}) == 1


def test_explicit_dimension_values_compare_by_dimension_and_member() -> None:
    a, b = MagicMock(spec=Concept), MagicMock(spec=Concept)
    assert ExplicitDimensionValue(a, b) == ExplicitDimensionValue(a, b)
    assert ExplicitDimensionValue(a, b) != ExplicitDimensionValue(b, a)


def test_a_report_period_is_a_name_for_a_duration() -> None:
    duration = DurationPeriodHolder(date(2025, 1, 1), date(2025, 12, 31))
    period = ReportPeriod("cur", duration)
    assert period.name == "cur" and period.duration == duration
    assert period == ReportPeriod("cur", duration)
    assert period != ReportPeriod("prior", duration)
