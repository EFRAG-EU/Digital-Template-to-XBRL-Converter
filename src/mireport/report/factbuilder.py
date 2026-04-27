from __future__ import annotations

import logging
from collections.abc import Collection
from typing import TYPE_CHECKING, Optional, Self

from mireport.exceptions import InlineReportException
from mireport.stringutil import xml_clean
from mireport.taxonomy import Concept, QName, Taxonomy
from mireport.typealiases import DecimalPlaces, FactValue
from mireport.report.fact import CoreDimensionNames, Fact, Symbol
from mireport.report.periods import DurationPeriodHolder, InstantPeriodHolder, PeriodHolder

if TYPE_CHECKING:
    from mireport.report.inlinereport import InlineReport

L = logging.getLogger(__name__)


class FactBuilder:
    """
    Represents a builder for Fact objects: an easy way to build and add facts to an InlineReport.
    """

    def __init__(self, report: InlineReport):
        self._report: InlineReport = report
        self._concept: Optional[Concept] = None
        self._aspects: dict[str | QName, str | QName] = {}
        self._value: Optional[FactValue] = None

    def __repr__(self) -> str:
        bits = (self._concept, self._aspects, self._value)
        return f"FactBuilder{bits}"

    def setExplicitDimension(
        self, explicitDimension: Concept, explicitDimensionValue: Concept
    ) -> Self:
        assert explicitDimension.isExplicitDimension, (
            f"Concept {explicitDimension=} is not an explicit dimension."
        )
        self._aspects[explicitDimension.qname] = explicitDimensionValue.qname
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
        if isinstance(typedDimensionValue, bool):
            s_value = str(typedDimensionValue).lower()
        else:
            s_value = str(typedDimensionValue)
        value = f'"<{typedDimension.typedElement}>{xml_clean(s_value)}</{typedDimension.typedElement}>"'
        self._aspects[typedDimension.qname] = value
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
        value: int | float,
        decimals: DecimalPlaces,
        *,
        inputIsDecimalForm: bool = True,
    ) -> Self:
        """Use instead of setValue() when you don't want to think about what to
        do with percentage values.

        If @inputIsDecimalForm is set to false then
        input is assumed to be whole-number form."""
        if inputIsDecimalForm:
            # HTML needs the display value for humans (100% stored as "100")
            human_value = value * 10**2
            # And use ix:scale attribute to reduce it down again as XBRL stores
            # same way as Excel (100% stored as "1.0")
            self.setValue(human_value).setScale(-2)

            if decimals != "INF":
                # Add on the scale amount
                decimals += 2
        else:
            self.setValue(value)
        self.setDecimals(decimals)
        return self

    def setDecimals(self, decimals: DecimalPlaces) -> Self:
        self._aspects["decimals"] = f"{decimals}"
        return self

    def setScale(self, scale: int) -> Self:
        self._aspects["numeric-scale"] = f"{scale}"
        return self

    def setNamedPeriod(self, periodName: str) -> Self:
        """
        Sets the period for the fact to a named period in the InlineReport.
        """
        if not self._report.hasNamedPeriod(periodName):
            raise InlineReportException(
                f"Period '{periodName}' does not exist in the report."
            )
        self._aspects["period"] = periodName
        return self

    def setHiddenValue(self, value: str) -> Self:
        if not value.startswith('"') and not value.endswith('"'):
            value = f'"{value}"'
        self._aspects["hidden-value"] = value
        return self

    def setConcept(self, concept: Concept) -> Self:
        self._concept = concept
        if not concept.isReportable:
            raise InlineReportException(
                f"Fact cannot be reported against concept {concept=}."
            )
        return self

    def setSimpleUnit(self, measure: QName) -> Self:
        self._aspects["units"] = measure
        return self

    def setCurrency(self, code: QName | str) -> Self:
        if not self._report.taxonomy.UTR.validCurrency(code):
            raise InlineReportException(
                f"Currency '{code}' does not look like a valid currency code."
            )
        if isinstance(code, QName):
            code = code.localName
        self._aspects["monetary-units"] = code
        return self

    def setComplexUnit(
        self,
        numerator: QName | Collection[QName],
        denominator: QName | Collection[QName],
    ) -> Self:
        if isinstance(numerator, QName):
            numerator = [numerator]
        if isinstance(denominator, QName):
            denominator = [denominator]

        match (len(numerator), len(denominator)):
            case (0, 0) | (0, _) | (_, 0):
                raise InlineReportException(
                    f"At least one numerator ({numerator=}) and denominator ({denominator=}) required for a complex unit."
                )
            case (1, 1):
                self._aspects["complex-units"] = (
                    f'"{next(iter(numerator))}/{next(iter(denominator))}"'
                )
            case _:
                raise InlineReportException(
                    f"More than one measure in the numerator ({numerator=}) or denominator ({denominator=}) is not currently supported.  "
                )
        return self

    @property
    def hasAspects(self) -> bool:
        return bool(self._aspects)

    @property
    def hasTaxonomyDimensions(self) -> bool:
        for name in self._aspects:
            if isinstance(name, QName):
                return True
        return False

    def validateBoolean(self) -> None:
        if (value := self._value) is None:
            raise InlineReportException(f"Facts must have values {value=}")

        b_value: bool | None = None
        if isinstance(value, bool):
            b_value = value
        else:
            s_value = str(value).strip().lower()
            if s_value in {"true", "1", "yes"}:
                b_value = True
            elif s_value in {"false", "0", "no"}:
                b_value = False

            if b_value is None:
                raise InlineReportException(
                    f"Unable to determine boolean value for string value {s_value=}"
                )

        if b_value is True:
            self._aspects["transform"] = "fixed-true"
        else:
            self._aspects["transform"] = "fixed-false"

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
        if self._concept.isMonetary:
            units = self._aspects.get(
                "monetary-units", self._report.defaultAspects.get("monetary-units")
            )
            if not units:
                raise InlineReportException("Monetary concepts require a currency unit")
        else:
            units = self._aspects.get("units", self._report.defaultAspects.get("units"))
            complex_units = self._aspects.get(
                "complex-units", self._report.defaultAspects.get("complex-units")
            )
            if not (units or complex_units):
                raise InlineReportException("Numeric concepts require a unit")

    def validateEESingleFact(self) -> None:
        if (text_value := self._value) is None or not text_value:
            raise InlineReportException(
                f"Unable to create EE item fact with no human readable value {text_value=}"
            )
        if (ee_value := self._aspects.get("hidden-value")) is None or not ee_value:
            raise InlineReportException(
                f"Domain members not specified for EE fact {ee_value=}"
            )

    def validateEESetFact(self) -> None:
        if (text_value := self._value) is None or not text_value:
            raise InlineReportException(
                f"Unable to create EE fact with no human readable value {text_value=}"
            )
        if (ee_value := self._aspects.get("hidden-value")) is None:
            # Technically an empty EE set is a valid EE set
            raise InlineReportException(
                f"Unable to create EE fact with no machine-readable (expanded name) value {ee_value=}"
            )

    def validateTaxonomyDimensions(self) -> None:
        if self._concept is None:
            raise InlineReportException("Concept must be set before validating a Fact.")
        taxonomy = self._report.taxonomy
        typedDims: dict[Concept, str] = {}
        explicitDims: dict[Concept, Concept] = {}
        for name, value in self._aspects.items():
            if isinstance(name, QName):
                dimension = taxonomy.getConcept(name)
                if isinstance(value, str):
                    typedDims[dimension] = value
                elif isinstance(value, QName):
                    explicitDims[dimension] = taxonomy.getConcept(value)
        self.validateTypedDimensions(taxonomy, typedDims)
        self.validateExplicitDimensions(taxonomy, explicitDims)
        return

    def validateTypedDimensions(
        self, taxonomy: Taxonomy, typedDims: dict[Concept, str]
    ) -> None:
        if self._concept is None:
            raise InlineReportException(
                "Concept must be set before validating a FactBuilder.", self
            )
        neededTds = taxonomy.getTypedDimensionsForPrimaryItem(self._concept)
        setTds = frozenset(typedDims)
        neededButNotSet = neededTds - setTds
        setButNotNeeded = setTds - neededTds
        if setButNotNeeded:
            dim_list = ", ".join(str(a.qname) for a in setButNotNeeded)
            raise InlineReportException(
                f"Unexpected typed dimension(s) [{dim_list}] set on FactBuilder for {self._concept}",
                self,
            )
        if neededButNotSet:
            dim_list = ", ".join(str(a.qname) for a in neededButNotSet)
            raise InlineReportException(
                f"Missing required typed dimension(s) [{dim_list}] not set on FactBuilder for {self._concept}",
                self,
            )

    def validateExplicitDimensions(
        self, taxonomy: Taxonomy, explicitDims: dict[Concept, Concept]
    ) -> None:
        """Easy checks for XBRL validity to avoid mistakes. Still possible to create invalid facts."""
        if self._concept is None:
            raise InlineReportException("Concept must be set before validating a Fact.")
        neededEds = set(taxonomy.getExplicitDimensionsForPrimaryItem(self._concept))

        # Take defaulted dimensions out of both neededEds and self._aspects iff they match
        for dimName in neededEds.copy():
            defaultValue = taxonomy.getDimensionDefault(dimName)
            if defaultValue is None:
                continue
            chosenValue = explicitDims.get(dimName)
            if chosenValue is None or chosenValue == defaultValue:
                neededEds.remove(dimName)
                if chosenValue is not None:
                    explicitDims.pop(dimName)
                    self._aspects.pop(dimName.qname)

        # At this point we have no defaulted dimensions or values to worry about.
        chosenEds = frozenset(explicitDims.keys())
        neededButNotChosen = neededEds - chosenEds
        chosenButNotWanted = chosenEds - neededEds
        if chosenButNotWanted:
            dim_list = ", ".join(str(a.qname) for a in chosenButNotWanted)
            raise InlineReportException(
                f"Unexpected explicit dimension(s) [{dim_list}] set on FactBuilder for {self._concept}",
                self,
            )
        if neededButNotChosen:
            dim_list = ", ".join(str(a.qname) for a in neededButNotChosen)
            raise InlineReportException(
                f"Missing explicit dimension(s) [{dim_list}] not set on FactBuilder for {self._concept}",
                self,
            )
        validMembersForDims = {
            explicitDimension: taxonomy.getDomainMembersForExplicitDimension(
                explicitDimension
            )
            for explicitDimension in neededEds
        }
        for dimension, chosenMember in explicitDims.items():
            validMembers = validMembersForDims[dimension]
            if chosenMember not in validMembers:
                raise InlineReportException(
                    f"Explicit dimension {dimension} cannot be set to {chosenMember} on FactBuilder for {self._concept}",
                    self,
                )

    def buildFact(self) -> Fact:
        if self._concept is None:
            raise InlineReportException("Concept must be set before building a Fact.")
        if self._value is None:
            raise InlineReportException("Value must be set before building a Fact.")
        if self._concept.isBoolean:
            self.validateBoolean()
        elif self._concept.isEnumerationSingle:
            self.validateEESingleFact()
        elif self._concept.isEnumerationSet:
            self.validateEESetFact()
        elif self._concept.isNumeric:
            self.validateNumeric()
        self._aspects["period-type"] = self._concept.periodType.value

        if self._concept.isTextblock:
            # https://www.xbrl.org/WGN/html-for-ixbrl-wgn/WGN-2024-11-05/html-for-ixbrl-wgn-2024-11-05.html#sec-text-block-tags
            self._aspects["escape"] = "true"

        self.validateTaxonomyDimensions()
        # TODO: check aspect validity before creating fact and raise Exception if invalid
        return Fact(self._concept, self._value, self._report, self._aspects)
