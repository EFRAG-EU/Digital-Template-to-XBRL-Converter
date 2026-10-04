"""A Fact as aoix template text.

This is the only place aoix's syntax is produced. A Fact holds typed values (units, dimension values,
a period); here they become ``{{ num concept[period=cur, units=utr:t, ...] }}1,234{{ end }}``.
Only the current, non-deprecated forms are written: ``units=a/b`` and ``typed-value-wrapper=``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from mireport.exceptions import InlineReportException
from mireport.report.model import TypedDimensionValue, Unit
from mireport.typealiases import FactValue

if TYPE_CHECKING:
    from mireport.report.fact import Fact

_TRUE = frozenset({"true", "1", "yes"})
_FALSE = frozenset({"false", "0", "no"})


def coerce_boolean(value: FactValue) -> bool | None:
    """A boolean fact's value as a bool, or None if it does not read as one."""
    match value:
        case bool():
            return value
    match str(value).strip().lower():
        case text if text in _TRUE:
            return True
        case text if text in _FALSE:
            return False
        case _:
            return None


def unit_aspect(unit: Unit) -> str:
    """A single currency is aoix's ``monetary-units`` (a bare code); everything else is ``units``."""
    match unit:
        case Unit(numerator=(measure,), denominator=()) if unit.is_currency:
            return f"monetary-units={measure.localName}"
        case _:
            return f"units={unit}"


def check_typed_value_expressible(typed: TypedDimensionValue) -> None:
    """aoix writes a typed value inside a quoted string, which cannot hold a double quote."""
    if '"' in typed.value:
        raise InlineReportException(
            f"Typed dimension {typed.dimension.qname} has the value {typed.value!r}, which has a "
            "double quote in it; aoix cannot write that without its deprecated 'typed' keyword."
        )


def _typed_aspects(typed: TypedDimensionValue) -> list[str]:
    check_typed_value_expressible(typed)
    return [
        f"typed-value-wrapper={typed.wrapper}",
        f'{typed.dimension.qname}="{typed.value}"',
    ]


def fact_aspects(fact: Fact) -> list[str]:
    """Every aspect of the fact, as aoix text, in a fixed order."""
    concept = fact.concept
    aspects = [
        f"period={fact.period.name}",
        f"period-type={concept.periodType.value}",
    ]
    if (unit := fact.unit) is not None:
        aspects.append(unit_aspect(unit))
    if (decimals := fact.decimals) is not None:
        aspects.append(f'decimals="{decimals}"')
    if (scale := fact.scale) is not None:
        aspects.append(f'numeric-scale="{scale}"')
    aspects += [f"{d.dimension.qname}={d.member.qname}" for d in fact.explicit_values]
    for typed in fact.typed_values:
        aspects += _typed_aspects(typed)
    if (enumeration := fact.enumeration) is not None:
        members = " ".join(sorted(m.expandedName for m in enumeration))
        aspects.append(f'hidden-value="{members}"')
    if concept.isBoolean:
        if (value := coerce_boolean(fact.value)) is None:
            raise InlineReportException(
                f"Unable to determine boolean value for {fact.value!r}."
            )
        aspects.append("transform=fixed-true" if value else "transform=fixed-false")
    if concept.isTextblock:
        aspects.append("escape=true")
    if fact.footnotes:
        aspects.append(f'fn-refs="{"|".join(str(fn.id) for fn in fact.footnotes)}"')
    return aspects


def fact_to_aoix(fact: Fact, formatted_value: str) -> str:
    match fact.concept:
        case concept if concept.isMonetary:
            verb = "monetary"
        case concept if concept.isNumeric:
            verb = "num"
        case _:
            verb = "string"
    aspects = ", ".join(fact_aspects(fact))
    return f"{{{{ {verb} {fact.concept.qname}[{aspects}] }}}}{formatted_value}{{{{ end }}}}"
