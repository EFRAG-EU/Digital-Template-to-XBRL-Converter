"""Duplicate facts, in the terms of the XBRL OIM and the "Handling Duplicate Facts" WGN (2025-01-14).

*Duplicate facts* are facts equal in every aspect that is not the value: concept, entity, period,
unit and the taxonomy-defined dimensions (OIM, "Duplicate facts"). Two duplicates are

* **complete** when their values are equal and they have the same ``decimals`` (or neither has one);
* **consistent** when they are complete, or numeric with overlapping intervals (WGN 3.3): a reported
  value stands for the closed interval ``value +/- 0.5 * 10^-decimals`` (a point for infinite
  precision), every pair must overlap, and facts stated to the *same* accuracy must have the same
  value, so 2,000 and 3,000 (both -3) are inconsistent although their closed intervals touch;
* **inconsistent** otherwise.

Facts that differ only in unit are *alternative facts*, not duplicates (``duplicateClass`` is None).
Multi-language alternatives cannot arise: a Fact has no language aspect.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from enum import StrEnum
from typing import TYPE_CHECKING

from mireport.report.aoix import coerce_boolean

if TYPE_CHECKING:
    from mireport.report.fact import Fact


class DuplicateClass(StrEnum):
    COMPLETE = "complete"
    CONSISTENT = "consistent"
    INCONSISTENT = "inconsistent"


def _decimals(fact: Fact) -> int | None:
    """The fact's decimals, None meaning infinite (no accuracy was claimed, so every digit counts)."""
    match fact.decimals:
        case None | "INF":
            return None
        case int() as places:
            return places
        case other:
            return int(other)


def precision(fact: Fact) -> float:
    """How precise the fact claims to be: its decimals, infinity for none."""
    places = _decimals(fact)
    return float("inf") if places is None else places


def _number(fact: Fact) -> Decimal | None:
    try:
        return Decimal(str(fact.value))
    except InvalidOperation:
        return None


def _interval(value: Decimal, places: int | None) -> tuple[Decimal, Decimal]:
    if places is None:
        return value, value
    half = Decimal(10) ** -places / 2
    return value - half, value + half


def _same_value(a: Fact, b: Fact) -> bool:
    concept = a.concept
    if (
        concept.isNumeric
        and (x := _number(a)) is not None
        and (y := _number(b)) is not None
    ):
        return x == y
    if a.enumeration is not None or b.enumeration is not None:
        return frozenset(a.enumeration or ()) == frozenset(b.enumeration or ())
    if concept.isBoolean:
        x_bool, y_bool = coerce_boolean(a.value), coerce_boolean(b.value)
        return x_bool is not None and x_bool == y_bool
    return a.value == b.value


def duplicateClass(a: Fact, b: Fact) -> DuplicateClass | None:
    """How ``a`` and ``b`` relate as duplicates, or None if they are not duplicate facts."""
    if a.duplicateKey != b.duplicateKey:
        return None
    if _same_value(a, b):
        return (
            DuplicateClass.COMPLETE
            if _decimals(a) == _decimals(b) or not a.concept.isNumeric
            else DuplicateClass.CONSISTENT
        )
    if not a.concept.isNumeric:
        return DuplicateClass.INCONSISTENT
    x, y = _number(a), _number(b)
    if x is None or y is None or _decimals(a) == _decimals(b):
        return DuplicateClass.INCONSISTENT
    (low_a, high_a), (low_b, high_b) = (
        _interval(x, _decimals(a)),
        _interval(y, _decimals(b)),
    )
    if max(low_a, low_b) <= min(high_a, high_b):
        return DuplicateClass.CONSISTENT
    return DuplicateClass.INCONSISTENT
