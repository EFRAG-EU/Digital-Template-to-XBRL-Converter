"""A Fact as aoix text, from its typed fields.

aoix deprecated the 'typed' keyword and the 'complex-units' aspect. Nothing here may write either.
"""

from __future__ import annotations

from datetime import date
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from mireport.exceptions import InlineReportException
from mireport.report.aoix import (
    coerce_boolean,
    fact_aspects,
    fact_to_aoix,
    unit_aspect,
)
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
M = _Q.fromString("utr:m")
S = _Q.fromString("utr:s")
PERIOD = ReportPeriod("cur", DurationPeriodHolder(date(2025, 1, 1), date(2025, 12, 31)))


def _concept(
    name: str = "xbrli:Thing",
    *,
    period_type: str = "duration",
    boolean: bool = False,
    textblock: bool = False,
    monetary: bool = False,
    numeric: bool = False,
) -> Concept:
    concept = MagicMock(spec=Concept)
    concept.qname = _Q.fromString(name)
    concept.periodType.value = period_type
    concept.isBoolean = boolean
    concept.isTextblock = textblock
    concept.isMonetary = monetary
    concept.isNumeric = numeric or monetary
    return concept


def _dimension(name: str, wrapper: str | None = None) -> Concept:
    dimension = MagicMock(spec=Concept)
    dimension.qname = _Q.fromString(name)
    dimension.typedElement = (
        None if wrapper is None else SimpleNamespace(qname=_Q.fromString(wrapper))
    )
    return dimension


def _fact(concept: Concept | None = None, **fields: object) -> SimpleNamespace:
    base: dict[str, object] = {
        "concept": concept or _concept(),
        "value": "x",
        "period": PERIOD,
        "unit": None,
        "decimals": None,
        "scale": None,
        "explicit_values": (),
        "typed_values": (),
        "enumeration": None,
        "footnotes": [],
    }
    base.update(fields)
    return SimpleNamespace(**base)


def test_a_fact_always_says_its_period_and_period_type() -> None:
    assert fact_aspects(_fact()) == ["period=cur", "period-type=duration"]
    instant = _fact(_concept(period_type="instant"))
    assert fact_aspects(instant)[1] == "period-type=instant"


class TestUnits:
    def test_a_simple_unit(self) -> None:
        assert unit_aspect(Unit.simple(T)) == "units=utr:tCO2e"

    def test_a_currency_is_monetary_units_with_a_bare_code(self) -> None:
        assert unit_aspect(Unit.simple(EUR)) == "monetary-units=EUR"

    def test_a_divide_unit_is_units_not_the_deprecated_complex_units(self) -> None:
        assert unit_aspect(Unit.divide(T, EUR)) == "units=utr:tCO2e/iso4217:EUR"

    def test_several_measures(self) -> None:
        assert unit_aspect(Unit.divide([M, T], S)) == "units=(utr:m*utr:tCO2e)/utr:s"

    def test_the_fact_carries_its_unit(self) -> None:
        fact = _fact(_concept(numeric=True), unit=Unit.divide(T, EUR))
        assert "units=utr:tCO2e/iso4217:EUR" in fact_aspects(fact)


class TestNumbers:
    def test_decimals_and_scale_are_quoted(self) -> None:
        aspects = fact_aspects(_fact(decimals=4, scale=-2))
        assert 'decimals="4"' in aspects
        assert 'numeric-scale="-2"' in aspects

    def test_infinite_decimals(self) -> None:
        assert 'decimals="INF"' in fact_aspects(_fact(decimals="INF"))

    def test_zero_decimals_are_written(self) -> None:
        assert 'decimals="0"' in fact_aspects(_fact(decimals=0))

    def test_unset_decimals_and_scale_are_left_out(self) -> None:
        aspects = " ".join(fact_aspects(_fact()))
        assert "decimals" not in aspects and "numeric-scale" not in aspects


class TestDimensions:
    def test_an_explicit_dimension(self) -> None:
        dimension, member = _dimension("xbrli:Axis"), _concept("xbrli:Member")
        fact = _fact(explicit_values=(ExplicitDimensionValue(dimension, member),))
        assert "xbrli:Axis=xbrli:Member" in fact_aspects(fact)

    def test_a_typed_dimension_names_its_wrapper_then_gives_the_plain_value(
        self,
    ) -> None:
        dimension = _dimension("xbrli:Axis", "xbrli:Wrapper")
        fact = _fact(typed_values=(TypedDimensionValue.of(dimension, "TGT-001"),))
        aspects = fact_aspects(fact)
        assert aspects[-2:] == [
            "typed-value-wrapper=xbrli:Wrapper",
            'xbrli:Axis="TGT-001"',
        ]

    def test_text_is_not_escaped_because_aoix_escapes_it(self) -> None:
        dimension = _dimension("xbrli:Axis", "xbrli:Wrapper")
        fact = _fact(typed_values=(TypedDimensionValue.of(dimension, "R&D <1>"),))
        assert 'xbrli:Axis="R&D <1>"' in fact_aspects(fact)

    def test_each_typed_dimension_gets_its_own_wrapper_before_it(self) -> None:
        a = _dimension("xbrli:AxisA", "xbrli:WrapA")
        b = _dimension("xbrli:AxisB", "xbrli:WrapB")
        fact = _fact(
            typed_values=(
                TypedDimensionValue.of(a, "1"),
                TypedDimensionValue.of(b, "2"),
            )
        )
        assert fact_aspects(fact)[-4:] == [
            "typed-value-wrapper=xbrli:WrapA",
            'xbrli:AxisA="1"',
            "typed-value-wrapper=xbrli:WrapB",
            'xbrli:AxisB="2"',
        ]

    def test_a_value_aoix_cannot_quote_is_refused_not_written_deprecated(self) -> None:
        dimension = _dimension("xbrli:Axis", "xbrli:Wrapper")
        fact = _fact(typed_values=(TypedDimensionValue.of(dimension, 'say "hi"'),))
        with pytest.raises(InlineReportException, match="double quote"):
            fact_aspects(fact)


class TestEnumerations:
    @staticmethod
    def _member(expanded: str) -> Concept:
        member = MagicMock(spec=Concept)
        member.expandedName = expanded
        return member

    def test_members_are_written_by_expanded_name_sorted(self) -> None:
        fact = _fact(enumeration=(self._member("ns#B"), self._member("ns#A")))
        assert 'hidden-value="ns#A ns#B"' in fact_aspects(fact)

    def test_an_empty_set_is_an_empty_hidden_value(self) -> None:
        assert 'hidden-value=""' in fact_aspects(_fact(enumeration=()))

    def test_no_enumeration_writes_no_hidden_value(self) -> None:
        assert "hidden-value" not in " ".join(fact_aspects(_fact()))


class TestDerivedAspects:
    def test_a_boolean_says_which(self) -> None:
        boolean = _concept(boolean=True)
        assert "transform=fixed-true" in fact_aspects(_fact(boolean, value=True))
        assert "transform=fixed-false" in fact_aspects(_fact(boolean, value="no"))

    def test_a_boolean_that_reads_as_neither_is_refused(self) -> None:
        with pytest.raises(InlineReportException, match="boolean"):
            fact_aspects(_fact(_concept(boolean=True), value="maybe"))

    def test_a_text_block_is_escaped(self) -> None:
        assert "escape=true" in fact_aspects(_fact(_concept(textblock=True)))
        assert "escape=true" not in fact_aspects(_fact())

    def test_footnote_references(self) -> None:
        fact = _fact(footnotes=[SimpleNamespace(id=1), SimpleNamespace(id=3)])
        assert 'fn-refs="1|3"' in fact_aspects(fact)


def test_nothing_deprecated_is_ever_written() -> None:
    dimension = _dimension("xbrli:Axis", "xbrli:Wrapper")
    fact = _fact(
        _concept(numeric=True),
        unit=Unit.divide(T, EUR),
        typed_values=(TypedDimensionValue.of(dimension, "1"),),
    )
    text = fact_to_aoix(fact, "1")
    assert "complex-units" not in text
    assert "typed " not in text.replace("typed-value-wrapper", "")


def test_the_verb_follows_the_concept() -> None:
    assert fact_to_aoix(_fact(), "v").startswith("{{ string xbrli:Thing[")
    assert fact_to_aoix(_fact(_concept(numeric=True)), "1").startswith("{{ num ")
    assert fact_to_aoix(_fact(_concept(monetary=True)), "1").startswith("{{ monetary ")
    assert fact_to_aoix(_fact(), "v").endswith("] }}v{{ end }}")


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (True, True),
        (False, False),
        ("true", True),
        (" YES ", True),
        ("1", True),
        ("false", False),
        ("No", False),
        ("0", False),
        ("maybe", None),
        ("", None),
    ],
)
def test_coerce_boolean(value: object, expected: bool | None) -> None:
    assert coerce_boolean(value) is expected  # type: ignore[arg-type]
