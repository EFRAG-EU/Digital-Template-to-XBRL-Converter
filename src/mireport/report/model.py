"""The value types a Fact is made of.

A fact used to hold a dict of aoix-flavoured strings (``'"a/b"'``, ``'"<w>x</w>"'``) that several
places re-parsed. These are the same things as types: a unit is measures, a typed dimension is a
dimension and its text, a period is a name for a duration. aoix's syntax is produced from them in
one place (``mireport.report.aoix``) and nowhere else.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass

from mireport.exceptions import InlineReportException
from mireport.report.periods import DurationPeriodHolder
from mireport.taxonomy import Concept, QName
from mireport.typealiases import FactValue
from mireport.xml import ISO4217_NS

# The characters a typed dimension's text has never been allowed to carry: they would not survive
# being written into a context.
_TYPED_VALUE_STRIPPED = str.maketrans("", "", "\v\t\f\r\n")


def _measures(measures: QName | Iterable[QName]) -> tuple[QName, ...]:
    """Measures in a canonical order, so two units naming the same measures are equal."""
    found = (measures,) if isinstance(measures, QName) else tuple(measures)
    return tuple(sorted(found, key=str))


@dataclass(frozen=True, slots=True)
class Unit:
    """An XBRL unit: one or more measures, optionally divided by one or more measures.

    ``Unit.simple(utr:tCO2e)``, ``Unit.divide(utr:tCO2e, iso4217:EUR)``, or from text with
    ``Unit.parse("(a*b)/c", resolver)``.
    """

    numerator: tuple[QName, ...]
    denominator: tuple[QName, ...] = ()

    def __post_init__(self) -> None:
        if not self.numerator:
            raise InlineReportException("A unit needs at least one numerator measure.")
        # Frozen, so go round it: normalise to the canonical order.
        object.__setattr__(self, "numerator", _measures(self.numerator))
        object.__setattr__(self, "denominator", _measures(self.denominator))

    @classmethod
    def simple(cls, measure: QName) -> Unit:
        return cls((measure,))

    @classmethod
    def divide(
        cls, numerator: QName | Iterable[QName], denominator: QName | Iterable[QName]
    ) -> Unit:
        denominator = _measures(denominator)
        if not denominator:
            raise InlineReportException(
                "A divide unit needs at least one denominator measure."
            )
        return cls(_measures(numerator), denominator)

    @classmethod
    def parse(cls, text: str, resolve: Callable[[str], QName]) -> Unit:
        """Read ``a``, ``a*b``, ``a/b`` or ``(a*b)/(c*d)`` (OIM and aoix both write units so)."""

        def side(part: str) -> tuple[QName, ...]:
            part = part.strip().removeprefix("(").removesuffix(")")
            return tuple(resolve(m) for m in part.split("*") if m.strip())

        numerator, slash, denominator = text.partition("/")
        if slash and "/" in denominator:
            raise InlineReportException(f"Unit {text!r} has more than one '/'.")
        return cls(side(numerator), side(denominator) if slash else ())

    @property
    def is_divide(self) -> bool:
        return bool(self.denominator)

    @property
    def is_currency(self) -> bool:
        """A single ISO 4217 measure and nothing else."""
        return (
            len(self.numerator) == 1
            and not self.denominator
            and self.numerator[0].namespace == ISO4217_NS
        )

    @property
    def measure(self) -> QName:
        """The measure of a simple unit."""
        if len(self.numerator) != 1 or self.denominator:
            raise InlineReportException(f"{self} is not a simple unit.")
        return self.numerator[0]

    def __str__(self) -> str:
        def side(measures: tuple[QName, ...], bracket: bool) -> str:
            joined = "*".join(str(m) for m in measures)
            return f"({joined})" if bracket and len(measures) > 1 else joined

        if self.denominator:
            return f"{side(self.numerator, True)}/{side(self.denominator, True)}"
        return side(self.numerator, False)


@dataclass(frozen=True, slots=True)
class ExplicitDimensionValue:
    dimension: Concept
    member: Concept


@dataclass(frozen=True, slots=True)
class TypedDimensionValue:
    """A typed dimension's member: the text inside its wrapper element, as the author gave it.

    Not XML-escaped (aoix escapes it when it writes the context) and without the characters a
    context cannot carry. The wrapper element is the dimension's own ``typedElement``.
    """

    dimension: Concept
    value: str

    @classmethod
    def of(cls, dimension: Concept, value: FactValue) -> TypedDimensionValue:
        text = str(value).lower() if isinstance(value, bool) else str(value)
        return cls(dimension, text.translate(_TYPED_VALUE_STRIPPED))

    @property
    def wrapper(self) -> QName:
        element = self.dimension.typedElement
        if element is None:
            raise InlineReportException(
                f"{self.dimension.qname} is not a typed dimension."
            )
        return element.qname


@dataclass(frozen=True, slots=True)
class ReportPeriod:
    """A named duration a report declares, which facts refer to by this object, not its name."""

    name: str
    duration: DurationPeriodHolder
