from __future__ import annotations

from collections.abc import Iterable
from decimal import Decimal
from typing import TYPE_CHECKING, NamedTuple, Protocol, runtime_checkable

from markupsafe import Markup, escape

from mireport.exceptions import InlineReportException
from mireport.localise import localise_and_format_number
from mireport.report.aoix import fact_to_aoix
from mireport.report.model import (
    ExplicitDimensionValue,
    FactPeriod,
    TypedDimensionValue,
    Unit,
)
from mireport.stringutil import str_to_markupsafe, unicodeSpaceNormalize
from mireport.taxonomy import Concept
from mireport.typealiases import DecimalPlaces, FactValue

if TYPE_CHECKING:
    from mireport.report.footnote import Footnote
    from mireport.report.inlinereport import InlineReport


def numeric_string_key(value: str) -> tuple[int, str | int]:
    try:
        return (0, int(value))  # numeric values get priority
    except ValueError:
        return (1, value)  # fallback to lexicographic


class Symbol(NamedTuple):
    symbol: str
    name: str


@runtime_checkable
class Provenance(Protocol):
    """
    Describes where a Fact came from (e.g. a source cell reference).

    The report package is source-agnostic, so callers supply any object whose
    ``__str__`` yields a human-readable location. A plain ``str`` satisfies this.
    """

    def __str__(self) -> str: ...


class Fact:
    """
    A fact in an XBRL instance document: a concept's value in a period, with the unit, accuracy and
    dimensions that qualify it. Build one with a FactBuilder, which does the validation.
    """

    def __init__(
        self,
        concept: Concept,
        value: FactValue,
        report: InlineReport,
        *,
        period: FactPeriod,
        unit: Unit | None = None,
        decimals: DecimalPlaces | None = None,
        scale: int | None = None,
        explicit_dimensions: Iterable[ExplicitDimensionValue] = (),
        typed_dimensions: Iterable[TypedDimensionValue] = (),
        enumeration: Iterable[Concept] | None = None,
        provenance: Provenance | None = None,
    ):
        self.concept: Concept = concept
        self.value: FactValue = value
        self.provenance: Provenance | None = provenance
        self._report = report
        if not period.fitsConcept(concept):
            raise InlineReportException(
                f"{concept.qname} is a {concept.periodType.value} concept, which cannot be "
                f"reported for {period.part.value!r} of period {period.reportPeriod.name!r}."
            )
        self.period: FactPeriod = period
        self.unit: Unit | None = unit
        self.decimals: DecimalPlaces | None = decimals
        self.scale: int | None = scale
        # In the order they were given: that is the order they are written in, so reordering
        # them would change the output. Identity (context_key) does not depend on the order.
        self.explicit_values: tuple[ExplicitDimensionValue, ...] = tuple(
            explicit_dimensions
        )
        self.typed_values: tuple[TypedDimensionValue, ...] = tuple(typed_dimensions)
        # The members an enumeration fact holds: one for a single, any number for a set.
        self.enumeration: tuple[Concept, ...] | None = (
            None if enumeration is None else tuple(enumeration)
        )
        self.footnotes: list[Footnote] = []

    def __repr__(self) -> str:
        return (
            f"Fact({self.concept.qname} = {self.value!r}, {self._describe_context()})"
        )

    def _describe_context(self) -> str:
        bits = [f"period={self.period.reportPeriod.name}"]
        if self.period.isInstant:
            bits[0] += f" ({self.period.part.value})"
        if (unit := self.unit) is not None:
            bits.append(f"unit={unit}")
        if (decimals := self.decimals) is not None:
            bits.append(f"decimals={decimals}")
        if (scale := self.scale) is not None:
            bits.append(f"scale={scale}")
        bits += [f"{d.dimension.qname}={d.member.qname}" for d in self.explicit_values]
        bits += [f"{d.dimension.qname}={d.value!r}" for d in self.typed_values]
        if (enumeration := self.enumeration) is not None:
            bits.append(f"members={[str(m.qname) for m in enumeration]}")
        return ", ".join(bits)

    @property
    def context_key(self) -> tuple:
        """Everything about the fact except its concept and value: facts with the same concept
        and the same context_key are duplicates of one another."""
        return (
            self.period,
            self.unit,
            self.decimals,
            self.scale,
            frozenset(self.explicit_values),
            frozenset(self.typed_values),
            None if self.enumeration is None else frozenset(self.enumeration),
        )

    @property
    def duplicateKey(self) -> tuple:
        """What makes two facts duplicates (OIM): concept, period as XBRL has it (not the name it goes
        by, nor which part of it was meant: the start of 2025 and the end of 2024 are one instant),
        unit and dimensions. Not the value, decimals or scale."""
        return (
            self.concept.qname,
            self.period.oim,
            self.unit,
            frozenset(self.explicit_values),
            frozenset(self.typed_values),
        )

    def __key(self) -> tuple:
        return (self.concept.qname, self.value, *self.context_key)

    def __lt__(self, other: Fact) -> bool:
        return (str(self.concept.qname), str(self.value)) < (
            str(other.concept.qname),
            str(other.value),
        )

    def __eq__(self, other: object) -> bool:
        if self is other:
            return True
        if isinstance(other, Fact):
            return self.__key() == other.__key()
        return NotImplemented

    def __hash__(self) -> int:
        return hash(self.__key())

    def html_format_value(self, negated: bool = False) -> Markup:
        """Return the value formatted for HTML with locale-aware numeric formatting. ``negated``
        shows a number with the opposite sign to its value."""
        if self.concept.isBoolean:
            match self.value:
                case True:
                    output = escape("YES")
                case False:
                    output = escape("NO")
                case _:
                    output = escape(self.value)
            return output

        if self.concept.isNumeric:
            output = self._format_numeric_value(negated)
            return output

        if hasattr(self.value, "__html__"):
            output = Markup(self.value)
        elif isinstance(self.value, str):
            output = str_to_markupsafe(self.value)
        else:
            output = escape(self.value)
        return output

    def _format_numeric_value(self, negated: bool = False) -> Markup:
        # What is written is what is tagged: the document's number *is* the fact's value. So the
        # digits shown follow the decimals (less any scale), except that 0 decimals, like none, shows
        # the value whole -- rounding 12.5 to "12" would change the fact, not just its look.
        decimal_places: DecimalPlaces
        if self.decimals and self.decimals != "INF" and self.scale:
            decimal_places = self.decimals + self.scale
        else:
            decimal_places = self.decimals or "INF"

        try:
            number: int | float | str | Decimal
            match self.value:
                case bool():
                    raise TypeError(
                        f"Boolean cannot be formatted numerically: {self.value}"
                    )
                case int() | float() | str():
                    number = self.value
                case _:
                    raise TypeError(
                        f"Unsupported type for numeric formatting: {type(self.value).__name__}"
                    )
            if negated:
                number = -Decimal(str(number))
            output = localise_and_format_number(
                number, decimal_places, self._report._outputLocale
            )
        except (ValueError, TypeError, ArithmeticError) as e:
            raise InlineReportException(
                f"Unexpected fact value {self.value=} for numeric concept {self.concept=}."
            ) from e

        # inline xbrl transforms don't support space characters (e.g.
        # non-break space) other than space.
        output = unicodeSpaceNormalize(output)
        return escape(output)

    def as_aoix(self, negated: bool = False) -> Markup:
        """Returns the AOIX representation of the fact. A ``negated`` number is written with the
        opposite sign to its value, and says so (aoix's ``sign=flip``), so the fact keeps its value:
        what a negated preferred label asks of the row it is on. Zero has no sign to flip."""
        negated = negated and self.concept.isNumeric and not self._is_zero()
        return Markup(fact_to_aoix(self, self.html_format_value(negated), negated))

    def _is_zero(self) -> bool:
        try:
            return Decimal(str(self.value)) == 0
        except ArithmeticError:
            return False

    def __html__(self) -> Markup:
        return self.as_aoix()

    @property
    def explicit_dimensions(self) -> dict[Concept, Concept]:
        """Explicit dimension to its member."""
        return {d.dimension: d.member for d in self.explicit_values}

    @property
    def typed_dimensions(self) -> dict[Concept, str]:
        """Typed dimension to its member's text."""
        return {d.dimension: d.value for d in self.typed_values}

    @property
    def hasNonDefaultPeriod(self) -> bool:
        """Whether the page has to say this fact's period: it is not the report's own, or it is the
        start of it (an opening balance, which would read as a closing one if it said nothing)."""
        return (
            self.period.reportPeriod != self._report.defaultReportPeriod
            or self.period.isStart
        )

    @property
    def unitSymbol(self) -> str:
        unit = self.unit
        utr = self._report.taxonomy.UTR
        dataType = self.concept.dataType

        def symbols(measures: Iterable) -> str:
            return "·".join(utr.getSymbolForUnit(m, dataType) for m in measures)

        if unit is not None and unit.is_divide:
            return f"{symbols(unit.numerator)} per {symbols(unit.denominator)}"

        symbol = ""
        if unit is not None and (self.concept.isMonetary or self.concept.isNumeric):
            symbol = symbols(unit.numerator)

        if not symbol and "percentItemType" == dataType.localName:
            # No UTR unit for % so hack it in here.
            symbol = "%"
        return symbol

    def hasTaxonomyDimensions(self) -> bool:
        """Whether the fact is qualified by any taxonomy dimension, explicit or typed."""
        return bool(self.explicit_values or self.typed_values)
