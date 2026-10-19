"""A fact's value in its canonical lexical form, which is how xBRL-JSON writes it when the document
declares ``xbrl:canonicalValues``.

Canonical form is a property of the XML Schema type the value has: an xs:decimal always has a digit
either side of its point (``1.0``, never ``1``), an xs:integer never has a point, an xs:double is
written ``1.0E0``, a boolean ``true`` or ``false``. An enumeration's value is its members' QNames,
sorted as strings (what Arelle checks), and a number is the XBRL value, so a percentage shown as
12.5 with scale -2 is ``0.125``.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from mireport.exceptions import InlineReportException
from mireport.report.aoix import coerce_boolean
from mireport.report.fact import Fact
from mireport.taxonomy import Concept

# The xbrli item types whose values are xs:integer or derived from it.
_INTEGER_ITEM_TYPES = frozenset(
    f"{name}ItemType"
    for name in (
        "integer",
        "nonNegativeInteger",
        "nonPositiveInteger",
        "positiveInteger",
        "negativeInteger",
        "long",
        "int",
        "short",
        "byte",
        "unsignedLong",
        "unsignedInt",
        "unsignedShort",
        "unsignedByte",
    )
)
_DOUBLE_ITEM_TYPES = frozenset({"floatItemType", "doubleItemType"})


def canonicalValue(fact: Fact) -> str | None:
    """The fact's value as canonical xBRL-JSON writes it; None for a nil fact."""
    value = fact.value
    if value is None:
        return None
    if (enumeration := fact.enumeration) is not None:
        return " ".join(sorted(str(member.qname) for member in enumeration))
    concept = fact.concept
    if concept.isBoolean:
        if (truth := coerce_boolean(value)) is None:
            raise InlineReportException(
                f"{concept.qname} has a value that is not a boolean: {value!r}."
            )
        return "true" if truth else "false"
    if concept.isNumeric:
        return canonicalNumber(Decimal(str(value)).scaleb(fact.scale or 0), concept)
    match value:
        case datetime() | date():
            return value.isoformat()
    return str(value)


def canonicalNumber(number: Decimal, concept: Concept) -> str:
    """``number`` in the canonical form of ``concept``'s base type."""
    if not number.is_finite():
        raise InlineReportException(f"{concept.qname} has a value that is not finite.")
    if number.is_zero():
        number = Decimal(0)  # no "-0"
    baseType = concept.baseDataType.localName
    integral = number == number.to_integral_value()
    if baseType in _INTEGER_ITEM_TYPES and integral:
        return format(number.to_integral_value(), "f")
    if baseType in _DOUBLE_ITEM_TYPES:
        return _canonicalDouble(number)
    if integral:
        return f"{format(number.to_integral_value(), 'f')}.0"
    return format(number.normalize(), "f")


def _canonicalDouble(number: Decimal) -> str:
    """xs:double's canonical form: one digit before the point, at least one after, and an
    exponent (``1.25E2``, ``0.0E0``)."""
    if number.is_zero():
        return "0.0E0"
    sign, digits, exponent = number.normalize().as_tuple()
    assert isinstance(exponent, int)
    mantissa = "".join(map(str, digits))
    power = exponent + len(digits) - 1
    return f"{'-' if sign else ''}{mantissa[0]}.{mantissa[1:] or '0'}E{power}"
