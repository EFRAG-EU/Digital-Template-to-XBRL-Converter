from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import StrEnum
from typing import TYPE_CHECKING, NamedTuple, cast

from markupsafe import Markup, escape

from mireport.exceptions import InlineReportException
from mireport.localise import localise_and_format_number
from mireport.report.periods import DurationPeriodHolder, InstantPeriodHolder
from mireport.stringutil import unicodeSpaceNormalize
from mireport.taxonomy import Concept, QName
from mireport.typealiases import DecimalPlaces, FactValue

if TYPE_CHECKING:
    from typing import Optional

    from mireport.report.inlinereport import InlineReport

TD_VALUE_RE = re.compile(r">(.*?)</")


def tidyTdValue(original: str) -> str:
    new = TD_VALUE_RE.search(original)
    if new is not None:
        return new.group(1)
    else:
        return original


def numeric_string_key(value: str) -> tuple[int, str | int]:
    try:
        return (0, int(value))  # numeric values get priority
    except ValueError:
        return (1, value)  # fallback to lexicographic


class CoreDimension(StrEnum):
    Concept = "concept"
    Entity = "entity"
    Period = "period"
    Unit = "unit"
    Language = "language"


class Symbol(NamedTuple):
    symbol: str
    name: str


@dataclass(slots=True, frozen=True, eq=True)
class Footnote:
    """An immutable footnote that can be shared across many facts."""

    id: int
    content: Markup
    _facts: list[Fact] = field(
        default_factory=list, compare=False, hash=False, repr=False
    )

    def __html__(self) -> Markup:
        if self._facts:
            return self.as_aoix()
        return self.content

    def as_aoix(self) -> Markup:
        return Markup(f"{{{{ footnote {self.id} }}}}{self.content}{{{{ end }}}}")


class Fact:
    """
    Represents a fact in an XBRL instance document.
    """

    def __init__(
        self,
        concept: Concept,
        value: FactValue,
        report: InlineReport,
        aspects: dict[str | QName, str | QName] | None = None,
    ):
        self.concept: Concept = concept
        self.value: FactValue = value
        self._report = report
        self._aspects: dict[str | QName, str | QName] = {}
        if aspects is not None:
            self._aspects.update(aspects)
        for key in list(self._aspects.keys()):
            if isinstance(key, QName):
                keyConcept = self._report.taxonomy.getConcept(key)
                if keyConcept.isTypedDimension:
                    dimvalue = self._aspects.pop(key)
                    self._aspects[f"typed {keyConcept.qname}"] = dimvalue

        self._decimals: Optional[DecimalPlaces]
        if aspect_value := str(self._aspects.get("decimals", "")):
            if aspect_value == "INF":
                self._decimals = "INF"
            else:
                self._decimals = int(aspect_value)
            self._aspects["decimals"] = f'"{aspect_value}"'
        else:
            self._decimals = None

        self._numeric_scale: Optional[int] = None
        if aspect_value := str(self._aspects.get("numeric-scale", "")):
            self._numeric_scale = int(aspect_value)
            self._aspects["numeric-scale"] = f'"{aspect_value}"'

        self.footnotes: list[Footnote] = []

    def __repr__(self) -> str:
        return (
            f"Fact(concept={self.concept}, value={self.value}, aspects={self._aspects})"
        )

    def __lt__(self, other: Fact) -> bool:
        if self.concept is None or other.concept is None:
            return False
        return self.__key() < other.__key()

    def __key(
        self,
    ) -> tuple[QName, FactValue, frozenset[tuple[str | QName, str | QName]]]:
        aspects_flattened = frozenset((k, v) for k, v in self.aspects.items())
        return (self.concept.qname, self.value, aspects_flattened)

    def __eq__(self, other: object) -> bool:
        if self is other:
            return True
        if isinstance(other, Fact):
            return self.__key() == other.__key()
        return NotImplemented

    def __hash__(self) -> int:
        return hash(self.__key())

    def html_format_value(self) -> str:
        """Return the value formatted for HTML with locale-aware numeric formatting."""
        if self.concept.isBoolean:
            match self.value:
                case True:
                    output = "YES"
                case False:
                    output = "NO"
                case _:
                    output = escape(self.value)
        elif not self.concept.isNumeric:
            if hasattr(self.value, "__html__"):
                output = self.value
            elif isinstance(self.value, str):
                output = Markup("<br />").join(
                    escape(p) for p in self.value.splitlines()
                )
            else:
                output = escape(self.value)
        else:
            decimal_places: DecimalPlaces
            if self._decimals and self._decimals != "INF" and self._numeric_scale:
                decimal_places = self._decimals + self._numeric_scale
            else:
                decimal_places = self._decimals or "INF"

            locale = self._report._outputLocale
            try:
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
                output = localise_and_format_number(number, decimal_places, locale)
            except (ValueError, TypeError) as e:
                raise InlineReportException(
                    f"Unexpected fact value {self.value=} for numeric concept {self.concept=}."
                ) from e

            # inline xbrl transforms don't support space characters (e.g.
            # non-break space) other than space.
            output = unicodeSpaceNormalize(output)
            output = escape(output)
        return Markup(output)

    def as_aoix(self) -> Markup:
        """Returns the AOIX representation of the fact."""
        aoix_verb = "string"
        if self.concept.isMonetary:
            aoix_verb = "monetary"
        elif self.concept.isNumeric:
            aoix_verb = "num"
        aspects = self._aspects.copy()
        if self.footnotes:
            aspects["fn-refs"] = f'"{"|".join(str(fn.id) for fn in self.footnotes)}"'
        aspects_str = ", ".join(f"{k}={v}" for k, v in aspects.items())
        value = self.html_format_value()
        return Markup(
            f"{{{{ {aoix_verb} {self.concept.qname}[{aspects_str}] }}}}{value}{{{{ end }}}}"
        )

    def __html__(self) -> Markup:
        return self.as_aoix()

    @property
    def aspects(self) -> dict[str | QName, str | QName]:
        return dict(self._aspects)

    @property
    def hasNonDefaultPeriod(self) -> bool:
        if (
            period := self.aspects.get("period")
        ) is not None and period != self._report._defaultPeriodName:
            return True
        return False

    @property
    def period(self) -> DurationPeriodHolder | InstantPeriodHolder:
        if (period := self.aspects.get("period")) is not None:
            period = cast(str, period)
            return self._report._periods[period]
        else:
            return self._report._periods[self._report._defaultPeriodName]

    @property
    def unitSymbol(self) -> str:
        if "complex-units" in self.aspects:
            complexUnit = cast(str, self.aspects["complex-units"])
            complexUnit = complexUnit[1:-1]  # remove quotes at start and end
            numString, _, denString = complexUnit.rpartition("/")
            numQName = self._report.taxonomy.QNameMaker.fromString(numString)
            numSymbol = self._report.taxonomy.UTR.getSymbolForUnit(
                numQName, self.concept.dataType
            )
            denQName = self._report.taxonomy.QNameMaker.fromString(denString)
            denSymbol = self._report.taxonomy.UTR.getSymbolForUnit(
                denQName, self.concept.dataType
            )
            symbol = f"{numSymbol} per {denSymbol}"
            return symbol

        units: QName | str | None = None
        if self.concept.isMonetary:
            currency = self.aspects.get(
                "monetary-units", self._report.defaultAspects.get("monetary-units")
            )
            if isinstance(currency, str):
                units = self._report.taxonomy.QNameMaker.fromString(
                    f"iso4217:{currency}"
                )
            else:
                raise InlineReportException(
                    f"Monetary concept with non-string currency unit {currency=}"
                )
        elif self.concept.isNumeric:
            units = self.aspects.get("units")

        symbol = ""
        if units and isinstance(units, QName):
            symbol = self._report.taxonomy.UTR.getSymbolForUnit(
                units, self.concept.dataType
            )

        if not symbol and "percentItemType" == self.concept.dataType.localName:
            # No UTR unit for % so hack it in here.
            symbol = "%"
        return symbol

    def hasTaxonomyDimensions(self) -> bool:
        for name in self.aspects:
            if isinstance(name, QName):
                return True
        return False

    def getTaxonomyDimensions(self) -> dict[QName, QName]:
        dims: dict[QName, QName] = {}
        for name, value in self.aspects.items():
            if isinstance(name, QName):
                if not isinstance(value, QName):
                    raise InlineReportException(
                        f"Invalid dimension value {value=} found for dimension {name=}"
                    )
                dims[name] = value
        return dims

    def getCoreDimensions(self) -> dict[CoreDimension, Concept | QName | str]:
        oimD: dict[CoreDimension, Concept | QName | str] = {}
        oimD[CoreDimension.Concept] = self.concept
        oimD[CoreDimension.Entity] = self._report._entityName
        oimD[CoreDimension.Period] = self._report.getPeriodsForAoix()
        if self.concept.isNumeric:
            unit_aspect_names = ("monetary-units", "units", "complex-units")
            defaults = self._report.defaultAspects
            for name in unit_aspect_names:
                if (unit := self.aspects.get(name)) is not None:
                    break
                if (unit := defaults.get(name)) is not None:
                    break
            else:
                raise InlineReportException(
                    f"Numeric concept without a unit is not good! {self}"
                )
            oimD[CoreDimension.Unit] = unit
        return oimD
