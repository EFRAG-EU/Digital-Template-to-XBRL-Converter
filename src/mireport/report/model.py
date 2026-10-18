"""The value types a Fact is made of.

A fact used to hold a dict of aoix-flavoured strings (``'"a/b"'``, ``'"<w>x</w>"'``) that several
places re-parsed. These are the same things as types: a unit is measures, a typed dimension is a
dimension and its text, a period is a name for a duration. aoix's syntax is produced from them in
one place (``mireport.report.aoix``) and nowhere else.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from enum import Enum, StrEnum

from mireport.exceptions import InlineReportException
from mireport.report.periods import DurationPeriodHolder, _Period
from mireport.taxonomy import Concept, PeriodType, QName
from mireport.typealiases import FactValue
from mireport.xml import ISO4217_NS

# The characters a typed dimension's text has never been allowed to carry: they would not survive
# being written into a context.
_TYPED_VALUE_STRIPPED = str.maketrans("", "", "\v\t\f\r\n")


def _measures(measures: QName | Iterable[QName]) -> tuple[QName, ...]:
    """Measures in a canonical order, so two units naming the same measures are equal."""
    match measures:
        case QName():
            found: tuple[QName, ...] = (measures,)
        case _:
            found = tuple(measures)
    return tuple(sorted(found, key=str))


@dataclass(frozen=True, slots=True)
class Unit:
    """An XBRL unit: one or more measures, optionally divided by one or more measures.

    ``Unit.simple(utr:tCO2e)``, ``Unit.divide(utr:tCO2e, iso4217:EUR)``, or from text with
    ``Unit.fromUnitString("(a*b)/c", resolver)``.
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
    def fromUnitString(cls, text: str, resolve: Callable[[str], QName]) -> Unit:
        """Read a unit in the OIM Common "unit string representation".

        ``a``, ``a*b``, ``a/b``, ``(a*b)/c``: measures joined by ``*``, at most one ``/``, no
        whitespace, and brackets round a side only when it has more than one measure *and* there is a
        denominator. Measure order is not enforced on the way in (a unit's measures are always held
        sorted). ``resolve`` turns ``prefix:local`` into a QName and raises for an unbound prefix.
        Anything else raises ``InlineReportException`` naming the OIM error code.
        """

        def invalid(why: str) -> InlineReportException:
            return InlineReportException(
                f"oimce:invalidUnitStringRepresentation: {text!r} {why}."
            )

        def side(part: str, divided: bool) -> tuple[QName, ...]:
            if bracketed := part.startswith("("):
                if not part.endswith(")"):
                    raise invalid("has an unclosed bracket")
                part = part[1:-1]
            names = part.split("*")
            if any(not n or any(c in n for c in "()/ ") for n in names):
                raise invalid("has an empty or malformed measure")
            if bracketed and not (divided and len(names) > 1):
                raise invalid(
                    "brackets a side that is a single measure or has no denominator"
                )
            if len(names) > 1 and not bracketed and divided:
                raise invalid("needs brackets round a list of measures beside a '/'")
            try:
                return tuple(resolve(n) for n in names)
            except (InlineReportException, KeyError, ValueError) as e:
                raise InlineReportException(
                    f"oimce:unboundPrefix: in unit {text!r}: {e}"
                ) from e

        if not text or any(c.isspace() for c in text):
            raise invalid("is empty or contains whitespace")
        match text.split("/"):
            case [numerator]:
                return cls(side(numerator, False))
            case [numerator, denominator]:
                return cls(side(numerator, True), side(denominator, True))
            case _:
                raise invalid("has more than one '/'")

    def toUnitString(self) -> str:
        """The OIM Common unit string: sorted measures joined by ``*``, brackets round a list of
        more than one measure when there is a denominator, no whitespace."""

        def side(measures: tuple[QName, ...], bracket: bool) -> str:
            joined = "*".join(str(m) for m in measures)
            return f"({joined})" if bracket and len(measures) > 1 else joined

        if self.denominator:
            return f"{side(self.numerator, True)}/{side(self.denominator, True)}"
        return side(self.numerator, False)

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
        return self.toUnitString()


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
        match value:
            case bool():
                text = str(value).lower()
            case _:
                text = str(value)
        return cls(dimension, text.translate(_TYPED_VALUE_STRIPPED))

    @property
    def wrapper(self) -> QName:
        if (element := self.dimension.typedElement) is None:
            raise InlineReportException(
                f"{self.dimension.qname} is not a typed dimension."
            )
        return element.qname


@dataclass(frozen=True, slots=True)
class ReportPeriod:
    """A named duration a report declares, which facts refer to by this object, not its name."""

    name: str
    duration: DurationPeriodHolder


class PeriodPart(Enum):
    """Which part of a report period a fact is about: all of it, or the instant it starts or ends at."""

    Whole = "whole"
    Start = "start"
    End = "end"


@dataclass(frozen=True, slots=True)
class FactPeriod:
    """Where a fact was reported: a report period, and the part of it that was meant.

    The start of one year and the end of the year before are the same OIM instant, but a producer
    that says "opening balance" and one that says "closing balance" mean different things, and a
    page shows them differently. ``oim`` is what XBRL calls the period, ``part`` is what the producer
    meant by it: two FactPeriods can differ and still have the same ``oim``.
    """

    reportPeriod: ReportPeriod
    part: PeriodPart = PeriodPart.Whole

    @property
    def oim(self) -> _Period:
        duration = self.reportPeriod.duration
        match self.part:
            case PeriodPart.Whole:
                return duration
            case PeriodPart.Start:
                return duration.startInstant
            case PeriodPart.End:
                return duration.endInstant

    @property
    def partRank(self) -> int:
        """Within one report period: its opening instant, then the period as a whole, then its
        closing instant."""
        return (PeriodPart.Start, PeriodPart.Whole, PeriodPart.End).index(self.part)

    @property
    def isInstant(self) -> bool:
        return self.part is not PeriodPart.Whole

    @property
    def isStart(self) -> bool:
        return self.part is PeriodPart.Start

    @property
    def isEnd(self) -> bool:
        return self.part is PeriodPart.End

    @property
    def aoixPeriodType(self) -> str:
        """aoix's ``period-type``: the start of a period is its own instant, one day before the
        period's first day, which aoix works out itself."""
        match self.part:
            case PeriodPart.Whole:
                return PeriodType.Duration.value
            case PeriodPart.Start:
                return "instant-start"
            case PeriodPart.End:
                return PeriodType.Instant.value

    def fitsConcept(self, concept: Concept) -> bool:
        """A duration concept is reported for the whole period, an instant one at a point in it."""
        return self.isInstant == (concept.periodType is PeriodType.Instant)


class PeriodRole(StrEnum):
    """What a period is to the report as a whole. The report decides (InlineReport.periodRole), so
    a ReportPeriod, and the facts that refer to it, never change."""

    CURRENT = "current"
    """The period the report is about; where a fact with no period of its own goes."""
    PRIOR = "prior"
    """The period before, for comparatives."""
    OTHER = "other"
    """Any other period a report declares, such as a baseline or target year."""
