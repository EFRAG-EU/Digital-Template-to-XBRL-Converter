from __future__ import annotations

import logging
import math
from collections.abc import Iterable
from decimal import Decimal
from typing import TYPE_CHECKING

from mireport.exceptions import InlineReportException
from mireport.report.aoix import coerce_boolean
from mireport.report.fact import Fact, Provenance
from mireport.report.model import (
    ExplicitDimensionValue,
    FactPeriod,
    PeriodPart,
    ReportPeriod,
    TypedDimensionValue,
    Unit,
)
from mireport.report.periods import _Period
from mireport.taxonomy import Concept, PeriodType, QName, Taxonomy
from mireport.typealiases import DecimalPlaces, FactValue

if TYPE_CHECKING:
    from collections.abc import Collection
    from typing import Self

    from mireport.report.report import Report

L = logging.getLogger(__name__)


class FactBuilder:
    """
    Represents a builder for Fact objects: an easy way to build and add facts to a Report.
    """

    def __init__(self, report: Report):
        self._report: Report = report
        self._concept: Concept | None = None
        self._value: FactValue | None = None
        self._provenance: Provenance | None = None
        self._period: ReportPeriod | None = None
        self._part: PeriodPart | None = None
        self._unit: Unit | None = None
        self._decimals: DecimalPlaces | None = None
        self._scale: int | None = None
        self._explicit: dict[Concept, Concept] = {}
        self._typed: dict[Concept, TypedDimensionValue] = {}
        self._enumeration: tuple[Concept, ...] | None = None

    @classmethod
    def fromFact(cls, report: Report, fact: Fact) -> Self:
        """A builder holding a copy of ``fact``, to change (a new period, a new value) and build.

        Building re-runs every check, so the copy is validated like any other fact.
        """
        fb = cls(report)
        fb._concept = fact.concept
        fb._value = fact.value
        fb._period = fact.period.reportPeriod
        fb._part = fact.period.part
        fb._unit = fact.unit
        fb._decimals = fact.decimals
        fb._scale = fact.scale
        fb._explicit = {d.dimension: d.member for d in fact.explicit_values}
        fb._typed = {t.dimension: t for t in fact.typed_values}
        fb._enumeration = fact.enumeration
        return fb

    def __repr__(self) -> str:
        bits = (self._concept, self._value, self._period, self._part, self._unit)
        return f"FactBuilder{bits}"

    @property
    def concept(self) -> Concept | None:
        return self._concept

    def setExplicitDimension(
        self, explicitDimension: Concept, explicitDimensionValue: Concept
    ) -> Self:
        assert explicitDimension.isExplicitDimension, (
            f"Concept {explicitDimension=} is not an explicit dimension."
        )
        self._explicit[explicitDimension] = explicitDimensionValue
        return self

    def setTypedDimension(
        self, typedDimension: Concept, typedDimensionValue: FactValue
    ) -> Self:
        assert typedDimension.isTypedDimension, (
            f"Concept {typedDimension=} is not a typed dimension."
        )
        assert typedDimension.typedElement is not None, (
            f"Typed dimension {typedDimension=} has no wrapper element defined."
        )
        self._typed[typedDimension] = TypedDimensionValue.of(
            typedDimension, typedDimensionValue
        )
        return self

    def setValue(self, value: object) -> Self:
        if value is None:
            raise InlineReportException("Fact value cannot be None.")

        if not isinstance(value, FactValue):
            value = str(value)

        self._value = value
        return self

    def setPercentageValue(
        self,
        value: float,
        decimals: DecimalPlaces,
        *,
        inputIsDecimalForm: bool = True,
    ) -> Self:
        """Use instead of setValue() when you don't want to think about what to
        do with percentage values.

        `decimals` here is the number of decimals the percentage is *displayed*
        with (a spreadsheet's number format): 0.125 at 2 shows 12.50%. If you have
        XBRL's own decimals instead (4 for 0.1250), use setPercentageFact().

        If @inputIsDecimalForm is set to false then
        input is assumed to be whole-number form."""
        if inputIsDecimalForm:
            # The fact carries two more decimals than the page shows, the scale amount.
            return self.setPercentageFact(
                value, decimals if decimals == "INF" else decimals + 2
            )
        self.setValue(value)
        self.setDecimals(decimals)
        return self

    def setPercentageFact(
        self, fraction: float | Decimal, decimals: DecimalPlaces
    ) -> Self:
        """Set a percentage from what the XBRL fact holds: a fraction, and its decimals.

        0.1250 at decimals 4 is 12.50%. The page shows 12.50 (the fraction x100, which is
        what a human reads) and tags it ix:scale="-2", so the fact stays 0.1250 at decimals 4
        exactly as given -- the decimals are XBRL's, not the display's, so none are added.

        Use this when the value and decimals come from XBRL (an xBRL-JSON fact, say).
        setPercentageValue() is the one for a spreadsheet's display decimals. Pass a Decimal
        when exactness matters: a float times 100 can pick up noise (0.07 -> 7.000000000000001).
        """
        match fraction:
            case Decimal() if (shown := fraction * 100) == shown.to_integral_value():
                value: int | float = int(shown)
            case Decimal():
                value = float(fraction * 100)
            case _:
                value = fraction * 10**2
        self.setValue(value).setScale(-2).setDecimals(decimals)
        return self

    def setDecimals(self, decimals: DecimalPlaces) -> Self:
        self._decimals = decimals
        return self

    def setScale(self, scale: int) -> Self:
        self._scale = scale
        return self

    def setNamedPeriod(self, periodName: str, *, at: PeriodPart | None = None) -> Self:
        """
        Sets the period for the fact to a named period in the Report.

        An instant concept is reported at the end of the period unless ``at`` says
        ``PeriodPart.Start`` (without ``at`` a part already there, from a copied fact, is kept): an opening balance is the instant the period starts at, which is also
        where the one before ended, but it is not that period's closing balance and is rendered as
        the start of this one.
        """
        if not self._report.hasNamedPeriod(periodName):
            raise InlineReportException(
                f"Period '{periodName}' does not exist in the report."
            )
        if at is PeriodPart.Whole:
            raise InlineReportException(
                "A fact is reported for the whole of a period by being a duration concept; "
                "'at' is for choosing between the start and the end."
            )
        self._period = self._report.getReportPeriod(periodName)
        if at is not None:
            self._part = at
        return self

    def setPeriod(self, period: _Period) -> Self:
        """
        Sets the period for the fact from an OIM period (a duration, or an instant), which has to be
        a declared period or the end or start of one (see ``Report.locatePeriod``).
        """
        located = self._report.locatePeriod(period)
        self._period = located.reportPeriod
        self._part = located.part
        return self

    def setEnumerationValue(self, member: Concept) -> Self:
        """The member an enumeration (single) fact holds."""
        self._enumeration = (member,)
        return self

    def setEnumerationSet(self, members: Iterable[Concept]) -> Self:
        """The members an enumeration set fact holds, which may be none."""
        self._enumeration = tuple(dict.fromkeys(members))
        return self

    def setProvenance(self, provenance: Provenance) -> Self:
        """Record where this fact originated (e.g. a source cell reference)."""
        self._provenance = provenance
        return self

    def setConcept(self, concept: Concept) -> Self:
        self._concept = concept
        if not concept.isReportable:
            raise InlineReportException(
                f"Fact cannot be reported against concept {concept=}."
            )
        return self

    def setUnit(self, unit: Unit) -> Self:
        self._unit = unit
        return self

    def setSimpleUnit(self, measure: QName) -> Self:
        return self.setUnit(Unit.simple(measure))

    def setCurrency(self, code: QName | str) -> Self:
        if not self._report.taxonomy.UTR.validCurrency(code):
            raise InlineReportException(
                f"Currency '{code}' does not look like a valid currency code."
            )
        match code:
            case QName():
                code = code.localName
        return self.setSimpleUnit(
            self._report.taxonomy.QNameMaker.fromString(f"iso4217:{code}")
        )

    def setComplexUnit(
        self,
        numerator: QName | Collection[QName],
        denominator: QName | Collection[QName],
    ) -> Self:
        return self.setUnit(Unit.divide(numerator, denominator))

    @property
    def hasTaxonomyDimensions(self) -> bool:
        return bool(self._explicit or self._typed)

    def validateBoolean(self) -> None:
        if (value := self._value) is None:
            raise InlineReportException(f"Facts must have values {value=}")
        if coerce_boolean(value) is None:
            raise InlineReportException(
                f"Unable to determine boolean value for string value {str(value).strip().lower()=}"
            )

    def validateNumeric(self) -> None:
        if self._concept is None:
            raise InlineReportException(
                "Concept must be set before validating a FactBuilder.", self
            )
        value = self._value
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            # N.B. bool extends int
            raise InlineReportException(
                f"Unable to create numeric fact from non-numeric value {value=}"
            )
        if isinstance(value, float) and not math.isfinite(value):
            raise InlineReportException(
                f"Unable to create numeric fact from non-finite value {value=}"
            )
        match (self._concept.isMonetary, self._unit, self._report.defaultCurrency):
            case (True, None, None):
                raise InlineReportException("Monetary concepts require a currency unit")
            case (False, None, _):
                raise InlineReportException("Numeric concepts require a unit")

    def validateEESingleFact(self) -> None:
        if (text_value := self._value) is None or not text_value:
            raise InlineReportException(
                f"Unable to create EE item fact with no human readable value {text_value=}"
            )
        if not self._enumeration:
            raise InlineReportException(
                f"Domain members not specified for EE fact {self._enumeration=}"
            )

    def validateEESetFact(self) -> None:
        if (text_value := self._value) is None or not text_value:
            raise InlineReportException(
                f"Unable to create EE fact with no human readable value {text_value=}"
            )
        if self._enumeration is None:
            # Technically an empty EE set is a valid EE set
            raise InlineReportException(
                f"Unable to create EE fact with no machine-readable (expanded name) value {self._enumeration=}"
            )

    def validateTaxonomyDimensions(self) -> None:
        if self._concept is None:
            raise InlineReportException("Concept must be set before validating a Fact.")
        taxonomy = self._report.taxonomy

        # An explicit dimension explicitly set to its own taxonomy-declared default
        # is equivalent to omitting it, and must be omitted -- OIM (xBRL-JSON)
        # forbids writing a dimension explicitly at its default member.
        for dimension, member in list(self._explicit.items()):
            if taxonomy.getDimensionDefault(dimension) == member:
                del self._explicit[dimension]

        self.validateDimensions(
            taxonomy,
            dict(self._explicit),
            {dimension: typed.value for dimension, typed in self._typed.items()},
        )

    def validateDimensions(
        self,
        taxonomy: Taxonomy,
        explicitDims: dict[Concept, Concept],
        typedDims: dict[Concept, str],
    ) -> None:
        """Easy checks for XBRL validity to avoid mistakes. Still possible to create invalid facts.

        A fact is dimensionally valid if its dimension values match at least one
        EffectiveHypercube for the concept (XBRL Dimensions 1.0 section 3.1.1: OR
        across base sets) -- never the union of every one's dimensions, since a
        concept can participate in multiple hypercubes/base-sets with different
        (even unrelated) dimensional requirements. A declared typed dimension that is
        missing makes a fact invalid for that hypercube.

        A concept in no hypercube is not restricted at all, so its facts may carry any
        dimensions (XBRL Dimensions 1.0). A taxonomy that wants them restricted says so with
        a closed hypercube that has no dimensions (the "Guidance on the use of dimensions"
        WGN, section 3.4), which then matches only a fact with none.

        Known gap: a typed dimension's *value* is only checked to be present, not against
        the typed element's datatype.
        """
        if self._concept is None:
            raise InlineReportException("Concept must be set before validating a Fact.")

        effectiveHypercubes = taxonomy.getEffectiveHypercubesForPrimaryItem(
            self._concept
        )
        if not effectiveHypercubes:
            return

        if any(
            effective.matches(explicitDims, typedDims)
            for effective in effectiveHypercubes
        ):
            return

        chosen_ed = ", ".join(f"{d.qname}={v.qname}" for d, v in explicitDims.items())
        chosen_td = ", ".join(f"{d.qname}={v!r}" for d, v in typedDims.items())
        raise InlineReportException(
            f"No valid dimensional combination for {self._concept} matches the "
            f"dimensions set on FactBuilder (explicit: [{chosen_ed}], typed: [{chosen_td}])",
            self,
        )

    def buildFact(self) -> Fact:
        if self._concept is None:
            raise InlineReportException("Concept must be set before building a Fact.")
        if self._value is None:
            raise InlineReportException("Value must be set before building a Fact.")
        concept = self._concept
        match concept:
            case _ if concept.isBoolean:
                self.validateBoolean()
            case _ if concept.isEnumerationSingle:
                self.validateEESingleFact()
            case _ if concept.isEnumerationSet:
                self.validateEESetFact()
            case _ if concept.isNumeric:
                self.validateNumeric()

        self.validateTaxonomyDimensions()
        reportPeriod = self._period or self._report.defaultReportPeriod
        if concept.periodType is PeriodType.Instant:
            part = self._part or PeriodPart.End
        else:
            part = self._part or PeriodPart.Whole
        unit = self._unit
        if unit is None and concept.isMonetary:
            # No currency given: the report's own, which validateNumeric has checked exists.
            if (currency := self._report.defaultCurrency) is None:
                raise InlineReportException("Monetary concepts require a currency unit")
            unit = Unit.simple(currency)
        return Fact(
            concept,
            self._value,
            self._report,
            period=FactPeriod(reportPeriod, part),
            unit=unit,
            decimals=self._decimals,
            scale=self._scale,
            explicit_dimensions=(
                ExplicitDimensionValue(d, m) for d, m in self._explicit.items()
            ),
            typed_dimensions=self._typed.values(),
            enumeration=self._enumeration,
            provenance=self._provenance,
        )
