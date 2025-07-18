import logging
import re
import shutil
import zipfile
from abc import ABC
from collections import defaultdict
from collections.abc import Collection
from dataclasses import dataclass
from datetime import date, datetime, timezone
from enum import Enum, StrEnum, auto
from io import BytesIO
from itertools import count
from pathlib import Path
from typing import NamedTuple, Optional, Self, cast
from unicodedata import name as unicode_name
from xml.sax.saxutils import escape as xml_escape

import ixbrltemplates
from jinja2 import Environment, PackageLoader
from markupsafe import Markup, escape

from mireport.exceptions import InlineReportException
from mireport.filesupport import FilelikeAndFileName, zipSafeString
from mireport.taxonomy import (
    Concept,
    PresentationGroup,
    PresentationStyle,
    QName,
    Relationship,
    Taxonomy,
)

L = logging.getLogger(__name__)

_FactValue = int | float | bool | str | date | datetime

UNCONSTRAINED_REPORT_PACKAGE_JSON = """{
    "documentInfo": {
        "documentType": "https://xbrl.org/report-package/2023"
    }
}"""

INLINE_REPORT_PACKAGE_JSON = """{
    "documentInfo": {
        "documentType": "https://xbrl.org/report-package/2023/xbri"
    }
}"""

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


class CoreDimensionNames(StrEnum):
    Concept = "concept"
    Entity = "entity"
    Period = "period"
    Unit = "unit"
    Language = "language"


class Symbol(NamedTuple):
    symbol: str
    name: str


class PeriodHolder(ABC):
    @property
    def isInstant(self) -> bool:
        """
        Returns True if this period holder is an InstantPeriodHolder.
        """
        return isinstance(self, InstantPeriodHolder)

    @property
    def isDuration(self) -> bool:
        """
        Returns True if this period holder is a DurationPeriodHolder.
        """
        return isinstance(self, DurationPeriodHolder)


@dataclass(slots=True, frozen=True, eq=True)
class DurationPeriodHolder(PeriodHolder):
    start: datetime | date
    end: datetime | date


@dataclass(slots=True, frozen=True, eq=True)
class InstantPeriodHolder(PeriodHolder):
    instant: datetime | date


_Period = InstantPeriodHolder | DurationPeriodHolder
_TableHeadingValue = Concept | _Period | str | None


class TableHeadingCell(NamedTuple):
    value: _TableHeadingValue
    colspan: int = 0
    rowspan: int = 0
    numeric: bool = False

    @property
    def isDuration(self) -> bool:
        return isinstance(self.value, DurationPeriodHolder)

    @property
    def isInstant(self) -> bool:
        return isinstance(self.value, InstantPeriodHolder)

    @property
    def isPeriod(self) -> bool:
        return self.isDuration or self.isInstant

    @property
    def isConcept(self) -> bool:
        return isinstance(self.value, Concept)


class TableStyle(Enum):
    SingleTypedDimensionColumn = auto()
    SingleExplicitDimensionColumn = auto()
    SingleExplicitDimensionRow = auto()
    Other = auto()


class Fact:
    """
    Represents a fact in an XBRL instance document.
    """

    def __init__(
        self,
        concept: Concept,
        value: _FactValue,
        report: "InlineReport",
        aspects: dict[str | QName, str | QName] | None = None,
    ):
        self.concept: Concept = concept
        self.value: _FactValue = value
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

    def __repr__(self) -> str:
        return (
            f"Fact(concept={self.concept}, value={self.value}, aspects={self._aspects})"
        )

    def __lt__(self, other: "Fact") -> bool:
        if self.concept is None or other.concept is None:
            return False
        return self.__key() < other.__key()

    def __key(
        self,
    ) -> tuple[QName, _FactValue, frozenset[tuple[str | QName, str | QName]]]:
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

    def format_value(self) -> str:
        if not self.concept.isNumeric:
            v = str(self.value)
            if "\n" in v:
                parts = v.split("\n")
                v = "<br />".join([escape(p) for p in parts])
            else:
                v = escape(v)
            m = Markup(v)
            return m

        match self.value:
            case str():
                return escape(self.value)
            case float() | int():
                decimal_places = int(str(self.aspects["decimals"])[1:-1])
                return escape(f"{self.value:,.{decimal_places}f}")
            case _:
                raise InlineReportException(
                    f"Unexpected fact value type {self.value=} for numeric concept {self.concept=}."
                )

    def as_aoix(self) -> str:
        """
        Returns the AOIX representation of the fact."
        """
        aoix_verb = "string"
        if self.concept.isMonetary:
            aoix_verb = "monetary"
        elif self.concept.isNumeric:
            aoix_verb = "num"
        aspects = ", ".join([f"{k}={v}" for k, v in self.aspects.items()])
        value = self.format_value()
        fstr = (
            f"{{{{ {aoix_verb} {self.concept.qname}[{aspects}] }}}}{value}{{{{ end }}}}"
        )
        return fstr

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

        if self.concept.isMonetary:
            units = self.aspects.get(
                "monetary-units", self._report.defaultAspects.get("monetary-units")
            )
            units = self._report.taxonomy.QNameMaker.fromString(f"iso4217:{units}")
        elif self.concept.isNumeric:
            units = self.aspects.get("units")

        if not units:
            return ""

        units = cast(QName, units)
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

    def getCoreDimensions(self) -> dict[CoreDimensionNames, Concept | QName | str]:
        oimD: dict[CoreDimensionNames, Concept | QName | str] = {}
        oimD[CoreDimensionNames.Concept] = self.concept
        oimD[CoreDimensionNames.Entity] = self._report._entityName
        oimD[CoreDimensionNames.Period] = self._report.getPeriodsForAoix()
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
            oimD[CoreDimensionNames.Unit] = unit
        return oimD


class FactBuilder:
    """
    Represents a builder for Fact objects: an easy way to build and add facts to an InlineReport.
    """

    def __init__(self, report: "InlineReport"):
        self._report: InlineReport = report
        self._concept: Optional[Concept] = None
        self._aspects: dict[str | QName, str | QName] = {}
        self._value: Optional[_FactValue] = None
        self._percentage = False

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
        self, typedDimension: Concept, typedDimensionValue: _FactValue
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
        value = f'"<{typedDimension.typedElement}>{xml_escape(s_value)}</{typedDimension.typedElement}>"'
        self._aspects[typedDimension.qname] = value
        return self

    def setValue(self, value: object) -> Self:
        if value is None:
            raise InlineReportException("Fact value cannot be None.")

        if not isinstance(value, _FactValue):
            value = str(value)

        self._value = value
        return self

    def setPercentageValue(
        self, value: int | float, decimals: int, *, inputIsDecimalForm: bool = True
    ) -> Self:
        """Use instead of setValue() when you don't want to think about what to
        do with percentage values.

        If @inputIsDecimalForm is set to false then
        input is assumed to be whole-number form."""
        self._percentage = True
        if inputIsDecimalForm:
            # HTML needs the display value for humans
            human_value = value * 10**2
            self.setValue(f"{human_value:.{decimals}f}")
            # XBRL stores same way as Excel (100% stored as "1.0")
            self.setScale(-2)
            decimals += 2
            self.setDecimals(decimals)
        else:
            self.setValue(f"{value:.{decimals}f}")
            self.setDecimals(decimals)
        return self

    def setDecimals(self, decimals: int) -> Self:
        self._aspects["decimals"] = f'"{decimals}"'
        return self

    def setScale(self, scale: int) -> Self:
        self._aspects["numeric-scale"] = f'"{scale}"'
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
        if self._percentage:
            return
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
        self.validateTaxonomyDimensions()
        # TODO: check aspect validity before creating fact and raise Exception if invalid
        return Fact(self._concept, self._value, self._report, self._aspects)


class InlineReport:
    def __init__(self, taxonomy: Taxonomy):
        self._facts: list[Fact] = []
        self._taxonomy: Taxonomy = taxonomy
        self._defaultAspects: dict[str, str] = {
            "numeric-transform": "num-dot-decimal",
            "decimals": '"0"',
        }
        self._periods: dict[str, DurationPeriodHolder] = {}
        self._entityName: str = "Sample"
        self._generatedReport: Optional[str] = None
        self._defaultPeriodName: str = ""
        self._schemaRefs: set[str] = set()

    @property
    def taxonomy(self) -> Taxonomy:
        return self._taxonomy

    @property
    def defaultAspects(self) -> dict[str, str]:
        return self._defaultAspects.copy()

    def getDefaultAspectsForAoix(self) -> str:
        defaults = self._defaultAspects.copy()
        aoix = []
        for key, value in defaults.items():
            if not (key and value):
                raise InlineReportException(
                    f"Default aspects not configured correctly. Specifically: '{key=}' '{value=}'"
                )
            if key in {"entity-identifier", "entity-scheme"}:
                value = f'"{value}"'
            aoix.append(f"{{{{ default {key} = {value} }}}}")
        return "\n".join(aoix)

    def setDefaultAspect(self, key: str, value: str) -> None:
        self._defaultAspects[key] = value

    def setEntityName(self, name: str) -> None:
        self._entityName = name

    def setDefaultPeriodName(self, name: str) -> None:
        if name not in self._periods:
            raise InlineReportException(
                f"Can't set default period as no such period {name=} exists."
            )
        self._defaultPeriodName = name

    def addDurationPeriod(self, name: str, periodStart: date, periodEnd: date) -> bool:
        if name in self._periods:
            return False
        self._periods[name] = DurationPeriodHolder(periodStart, periodEnd)
        return True

    def hasNamedPeriod(self, name: str) -> bool:
        """
        Returns True if the InlineReport has a period with the given name.
        """
        return name in self._periods

    def addSchemaRef(self, schemaRef: str) -> None:
        self._schemaRefs.add(schemaRef)

    @property
    def defaultPeriod(self) -> DurationPeriodHolder:
        return self._periods[self._defaultPeriodName]

    def getPeriodsForAoix(self) -> str:
        p = []
        for name, period in self._periods.items():
            p.append(f'{{{{ period {name} "{period.start}" "{period.end}" }}}}')
        p.append(f"{{{{ default period = {self._defaultPeriodName} }}}}")
        return "\n".join(p)

    def getFactBuilder(self) -> FactBuilder:
        """
        Returns a FactBuilder for the given concept.
        """
        return FactBuilder(self)

    def addFact(self, fact: Fact) -> None:
        """
        Adds a Fact to the report.
        """
        self._facts.append(fact)

    @property
    def hasFacts(self) -> bool:
        return bool(self._facts)

    @property
    def factCount(self) -> int:
        return len(self._facts)

    def getNamespacesForAoix(self) -> str:
        # {{ namespace utr = "http://www.xbrl.org/2009/utr" }}
        lines = []
        for p, n in self.taxonomy.namespacePrefixesMap.items():
            lines.append(f'{{{{ namespace {p} = "{n}" }}}}')
        return "\n".join(lines)

    def getSchemaRefForAoix(self) -> str:
        # {{ schema-ref "https://xbrl.efrag.org/taxonomy/vsme/2024-12-17/vsme-all.xsd" }}
        if not self._schemaRefs:
            self._schemaRefs.add(self.taxonomy.entryPoint)
        lines = []
        for url in sorted(self._schemaRefs):
            lines.append(f'{{{{ schema-ref "{url}" }}}}')
        return "\n".join(lines)

    def getDocumentInformation(self) -> list[dict[str, str | PeriodHolder | Symbol]]:
        bits: list[dict[str, str | PeriodHolder | Symbol]] = []

        def addDict(
            key: str,
            value: str | PeriodHolder | Symbol,
            format_macro: Optional[str] = None,
        ) -> None:
            d: dict[str, str | PeriodHolder | Symbol] = {"key": key, "value": value}
            if format_macro is not None:
                d["format_macro"] = format_macro
            bits.append(d)

        meta = {
            "Entity Name": self._entityName,
            "Entity Identifier": self._defaultAspects["entity-identifier"],
            "Entity Identifier Scheme": self._defaultAspects["entity-scheme"],
            "Report currency": self._defaultAspects["monetary-units"],
        }
        for k, v in meta.items():
            addDict(k, v)
        addDict("Report period", self.defaultPeriod, "render_duration_period")
        match self._defaultAspects.get("numeric-transform"):
            case "num-dot-decimal":
                separator = "."
            case "num-comma-decimal":
                separator = ","
            case _:
                separator = "unknown"
        bits.append(
            {
                "key": "Decimal separator",
                "value": Symbol(symbol=separator, name=unicode_name(separator)),
                "format_macro": "render_symbol",
            }
        )
        return bits

    def _getInlineReport(self) -> str:
        if not (self.hasFacts and self._defaultPeriodName):
            raise InlineReportException(
                "Cannot generate a report with no facts or period."
            )
        if self._generatedReport is not None:
            return self._generatedReport

        rl = ReportLayoutOrganiser(self._taxonomy, self)
        sections = rl.organise()
        env = Environment(
            loader=PackageLoader(__package__, "inline_report_templates"),
            keep_trailing_newline=True,
        )
        env.globals.update(
            {
                PresentationStyle.__name__: PresentationStyle,
                TableStyle.__name__: TableStyle,
                "now_utc": lambda: datetime.now(timezone.utc),
            }
        )
        env.filters.update(
            {
                "tidyTdValue": tidyTdValue,
            }
        )
        template = env.get_template("inline-report-presentation.html.jinja")
        html_content = template.render(
            aoix={
                "defaults": self.getDefaultAspectsForAoix(),
                "periods": self.getPeriodsForAoix(),
                "schema_ref": self.getSchemaRefForAoix(),
                "namespaces": self.getNamespacesForAoix(),
            },
            report_period=self.defaultPeriod,
            entityName=self._entityName,
            sections=sections,
            facts=list(self._facts),
            documentInfo=self.getDocumentInformation(),
        )

        try:
            parser = ixbrltemplates.Parser(
                "http://www.xbrl.org/inlineXBRL/transformation/2022-02-16",
                self.taxonomy.dimensionContainer.value,
            )
            ixbrl_content = parser.parse(html_content).strip()
            self._generatedReport = ixbrl_content
            return ixbrl_content
        except ixbrltemplates.ParseError as e:
            errors = []
            errors.append("aoix parse error:")
            errors.append(e.message)
            (line, offset) = ixbrltemplates.lineAndOffset(html_content, e._location)
            errors.append(line)
            errors.append(" " * offset + "^")
            message = "\n".join(errors)
            raise InlineReportException(message) from e

    def saveInlineReport(self, target: Path) -> None:
        ixbrl_content = self.getInlineReport()
        with open(target, "wb") as out:
            shutil.copyfileobj(ixbrl_content.fileLike(), out)

    def _getSafeEntityName(self) -> str:
        safeName = zipSafeString(self._entityName, fallback="Sample")
        return safeName

    def getInlineReportPackage(self) -> FilelikeAndFileName:
        # TODO: switch to INLINE_REPORT_PACKAGE_JSON and .xbri once Arelle is
        # updated to pass through the correct filename to its report package
        # validator.
        topLevel = f"{self._getSafeEntityName()}_{self.defaultPeriod.end.year}"
        report = self.getInlineReport()
        with BytesIO() as write_bio:
            with zipfile.ZipFile(
                write_bio, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6
            ) as z:
                # writestr defaults to encoding strings as UTF-8 so we don't need to bother
                z.writestr(
                    zinfo_or_arcname=f"{topLevel}/META-INF/reportPackage.json",
                    data=UNCONSTRAINED_REPORT_PACKAGE_JSON,
                )
                z.writestr(
                    zinfo_or_arcname=f"{topLevel}/reports/{report.filename}",
                    data=report.fileContent,
                )
            rpBytes = write_bio.getvalue()
        packageFilename = f"{topLevel}_XBRL_Report.zip"
        return FilelikeAndFileName(fileContent=rpBytes, filename=packageFilename)

    def getInlineReport(self) -> FilelikeAndFileName:
        yearEnd = self.defaultPeriod.end.year
        filename = f"{self._getSafeEntityName()}_{yearEnd}_XBRL_Report.html"
        return FilelikeAndFileName(
            fileContent=self._getInlineReport().encode("UTF-8"), filename=filename
        )


class ReportLayoutOrganiser:
    def __init__(self, taxonomy: Taxonomy, report: InlineReport):
        self.taxonomy = taxonomy
        self.report = report
        self.presentation = self.taxonomy.presentation
        self.reportSections: list[ReportSection] = []
        self.factsByConceptMap: dict[Concept, list[Fact]] = defaultdict(list)

        for fact in self.report._facts:
            self.factsByConceptMap[fact.concept].append(fact)

    def organise(self) -> list["ReportSection"]:
        self.createReportSections()
        self.createReportTables()
        self.reportSections.sort(key=lambda x: x.presentation.label)
        self.checkAllFactsUsed()
        return self.reportSections

    def checkAllFactsUsed(self) -> None:
        """
        Checks that all facts in the report have been used in the report sections.
        Raises an InlineReportException if any facts are not used.
        """
        potential_unused_facts = set(self.report._facts)
        for section in self.reportSections:
            if not section.tabular:
                for facts in section.relationshipToFact.values():
                    potential_unused_facts.difference_update(facts)
            else:
                section = cast(TabularReportSection, section)
                for row in section.data:
                    potential_unused_facts.difference_update(row)
        unused_facts = frozenset(potential_unused_facts)
        if unused_facts:
            processed: set[Fact] = set()
            for u in unused_facts:
                if u in processed:
                    continue
                others = list(self.factsByConceptMap[u.concept])
                others.remove(u)
                u_aspects = frozenset(u.aspects.items())
                inconsistent_duplicates = [
                    f
                    for f in others
                    if frozenset(f.aspects.items()) == u_aspects and f.value != u.value
                ]
                processed.add(u)
                processed.update(inconsistent_duplicates)
                if inconsistent_duplicates:
                    L.warning(
                        f"Fact has inconsistent duplicates.\nUnused: {u}\nOthers: {inconsistent_duplicates}"
                    )

    def createReportSections(self) -> None:
        for group in self.presentation:
            if group.style == PresentationStyle.Empty:
                self.reportSections.append(
                    ReportSection(relationshipToFact={}, presentation=group)
                )
                continue

            factsForRel: dict[Relationship, list[Fact]] = defaultdict(list)
            # TODO: store hasHypercubes:bool on the group and avoid check every time here.
            for rel in group.relationships:
                concept = rel.concept
                if concept not in self.factsByConceptMap:
                    continue
                factsForConcept = self.factsByConceptMap[concept]
                if group.style == PresentationStyle.List:
                    factsForRel[rel].extend(
                        fact
                        for fact in factsForConcept
                        if not fact.hasTaxonomyDimensions()
                    )
                elif group.style in {PresentationStyle.Hybrid, PresentationStyle.Table}:
                    factsForRel[rel].extend(factsForConcept)
                else:
                    pass  # No reportable concepts in this group so nothing to do.
            self.reportSections.append(
                ReportSection(relationshipToFact=factsForRel, presentation=group)
            )

    def createReportTables(self) -> None:
        table_sections: dict[str, TabularReportSection] = {}
        for section in self.reportSections:
            if section.presentation.style in {
                PresentationStyle.List,
                PresentationStyle.Empty,
            }:
                # Nothing to do as these don't have tables
                continue

            if section.presentation.style is PresentationStyle.Hybrid:
                raise InlineReportException(
                    f"Presentation group style ({section.presentation.style.name}) of [{section.presentation.roleUri}] is not currently supported."
                )

            hypercubes = [
                r for r in section.presentation.relationships if r.concept.isHypercube
            ]
            if 1 != len(hypercubes):
                raise InlineReportException(
                    f"Presentation structure of [{section.presentation.roleUri}] is not currently supported."
                )

            typedDims = [
                r.concept
                for r in section.presentation.relationships
                if r.concept.isTypedDimension
            ]
            explicitDims = [
                r.concept
                for r in section.presentation.relationships
                if r.concept.isExplicitDimension
            ]
            reportable = [
                r.concept
                for r in section.presentation.relationships
                if r.concept.isReportable
            ]

            tableStyle = TableStyle.Other
            rowHeadings: list[Concept | str | None] = []
            columnHeadings: list[Concept | None] = []
            data: list[list[Fact | None]] = []
            explicitDim = None
            typedQname = None

            if len(typedDims) == 1 and not explicitDims:
                tableStyle = TableStyle.SingleTypedDimensionColumn
                initialColumnHeadings: list[Concept] = reportable
                typedQname = f"typed {typedDims[0].qname}"

                tdValues = {
                    str(fact.aspects[typedQname])
                    for r in reportable
                    for fact in self.factsByConceptMap[r]
                }
                prettyTdValues = [
                    (tidyTdValue(typedValue), typedValue) for typedValue in tdValues
                ]
                prettyTdValues.sort(key=lambda x: numeric_string_key(x[0]))
                for heading, rKey in prettyTdValues:
                    row: list[None | Fact] = []
                    for c in initialColumnHeadings:
                        facts = self.factsByConceptMap[c]
                        found = None
                        for fact in facts:
                            tdValue = fact.aspects.get(typedQname)
                            if tdValue is not None and tdValue == rKey:
                                if found is not None:
                                    L.debug(
                                        f"Multiple facts found (handle this better) {section.presentation.roleUri=} {tableStyle=}\n{found=}\n{fact=}"
                                    )
                                found = fact
                        row.append(found)
                    if len(row) != len(initialColumnHeadings):
                        raise InlineReportException(
                            f"Failed to fill row correctly {heading}, with {initialColumnHeadings}"
                        )
                    row_empty = all(c is None for c in row)
                    if not row_empty:
                        data.append(row)
                        rowHeadings.append(heading)

                # Put the Dimension name as the heading above the row headings which are the domain members.
                columnHeadings.insert(0, typedDims[0])
                columnHeadings.extend(reportable)

            elif len(explicitDims) == 1 and not typedDims:
                explicitDim = explicitDims[0]
                domain_set = self.taxonomy.getDomainMembersForExplicitDimension(
                    explicitDim
                )
                domain: list[Concept] = [
                    rel.concept
                    for rel in section.presentation.relationships
                    if rel.concept in domain_set
                ]
                defaultMember = self.taxonomy.getDimensionDefault(explicitDim)

                if len(domain) <= len(reportable):
                    tableStyle = TableStyle.SingleExplicitDimensionColumn
                    initialColumnHeadings = domain
                    initialRowHeadings = reportable

                    for r in initialRowHeadings:
                        row: list[None | Fact] = []
                        for c in initialColumnHeadings:
                            facts = self.factsByConceptMap[r]
                            found = None
                            for fact in facts:
                                eValue = fact.aspects.get(explicitDim.qname)
                                if (eValue is None and c == defaultMember) or (
                                    eValue is not None and eValue == c.qname
                                ):
                                    if found is not None:
                                        L.debug(
                                            f"Multiple facts found (handle this better) {section.presentation.roleUri=} {tableStyle=}\n{found=}\n{fact=}"
                                        )
                                    found = fact
                            row.append(found)
                        if len(row) != len(initialColumnHeadings):
                            raise InlineReportException(
                                f"Failed to fill row correctly {r}, with {initialColumnHeadings}"
                            )
                        row_empty = all(c is None for c in row)
                        if not row_empty:
                            data.append(row)
                            rowHeadings.append(r)
                    # There is no column heading above the row headings
                    columnHeadings.insert(0, None)
                    columnHeadings.extend(domain)
                else:
                    tableStyle = TableStyle.SingleExplicitDimensionRow
                    initialColumnHeadings = reportable
                    initialRowHeadings = domain

                    for r in initialRowHeadings:
                        row: list[None | Fact] = []
                        for c in initialColumnHeadings:
                            facts = self.factsByConceptMap[c]
                            found = None
                            for fact in facts:
                                eValue = fact.aspects.get(explicitDim.qname)
                                if (
                                    (eValue is None and r == defaultMember)
                                    or eValue is not None
                                    and eValue == r.qname
                                ):
                                    if found is not None:
                                        L.debug(
                                            f"Multiple facts found (handle this better) {section.presentation.roleUri=} {tableStyle=}\n{found=}\n{fact=}"
                                        )
                                    found = fact
                            row.append(found)
                        if len(row) != len(initialColumnHeadings):
                            raise InlineReportException(
                                f"Failed to fill row correctly {r}, with {initialColumnHeadings}"
                            )
                        row_empty = all(c is None for c in row)
                        if not row_empty:
                            data.append(row)
                            rowHeadings.append(r)
                    # Put the Dimension name as the heading above the row headings which are the domain members.
                    columnHeadings.insert(0, explicitDim)
                    columnHeadings.extend(reportable)

            tableUnit = self.getTableUnit(data)
            columnUnits = self.getColumnUnits(data)
            tablePeriod = self.getTablePeriod(data)
            columnPeriods = self.getColumnPeriods(data)

            all_numeric = True
            col_numeric: list[bool] = [True for _ in range(len(columnHeadings[1:]))]
            for row in data:
                for col_num, factOrNone in enumerate(row):
                    if factOrNone is None:
                        continue
                    if not factOrNone.concept.isNumeric:
                        all_numeric = False
                        col_numeric[col_num] = False

            newColumnHeadings: list[list[TableHeadingCell]] = []
            max_cols = max(1, len(columnHeadings) - 1)
            rows: dict[int, list[TableHeadingCell]] = defaultdict(list)
            rowCounter = count()
            if tablePeriod:
                rows[next(rowCounter)].append(
                    TableHeadingCell(tablePeriod, colspan=max_cols, rowspan=1)
                )
            if tableUnit:
                rows[next(rowCounter)].append(
                    TableHeadingCell(
                        tableUnit, colspan=max_cols, rowspan=1, numeric=True
                    )
                )
            rowNum = next(rowCounter)
            colZero = columnHeadings.pop(0)
            for cnum, col in enumerate(columnHeadings):
                thisNumeric = all_numeric
                if col_numeric[cnum] is True:
                    thisNumeric = True
                rows[rowNum].append(
                    TableHeadingCell(col, colspan=1, rowspan=1, numeric=thisNumeric)
                )
            if not tablePeriod and columnPeriods:
                rowNum = next(rowCounter)
                for cnum, cp in enumerate(columnPeriods):
                    thisNumeric = all_numeric
                    if col_numeric[cnum] is True:
                        thisNumeric = True
                    rows[rowNum].append(
                        TableHeadingCell(cp, colspan=1, rowspan=1, numeric=thisNumeric)
                    )
            if not tableUnit and columnUnits:
                rowNum = next(rowCounter)
                for cnum, cu in enumerate(columnUnits):
                    thisNumeric = all_numeric
                    if col_numeric[cnum] is True:
                        thisNumeric = True
                    rows[rowNum].append(
                        TableHeadingCell(cu, colspan=1, rowspan=1, numeric=thisNumeric)
                    )
            if rows:
                rows[0].insert(
                    0, TableHeadingCell(colZero, colspan=1, rowspan=len(rows))
                )

            for hrow in rows.values():
                newColumnHeadings.append(hrow)

            table_sections[section.presentation.roleUri] = TabularReportSection(
                relationshipToFact=section.relationshipToFact,
                presentation=section.presentation,
                rowHeadings=rowHeadings,
                dataColumns=columnHeadings,
                tableStyle=tableStyle,
                data=data,
                columnUnits=columnUnits,
                columnPeriods=columnPeriods,
                numeric=all_numeric,
                unitSymbol=tableUnit,
                period=tablePeriod,
                newColumnHeadings=newColumnHeadings,
            )

        merged_sections: list[ReportSection] = []
        for section in self.reportSections:
            roleUri = section.presentation.roleUri
            if roleUri in table_sections:
                merged_sections.append(table_sections[roleUri])
            else:
                merged_sections.append(section)
        self.reportSections = merged_sections

    def getTableUnit(self, data: list[list[Fact | None]]) -> Optional[str]:
        units: set[str] = set()
        for row in data:
            for factOrNone in row:
                if factOrNone is None:
                    continue
                fact: Fact = factOrNone
                if fact.concept.isNumeric:
                    units.add(fact.unitSymbol)
        if 1 == len(units):
            unit = next(iter(units))
            if unit:
                return unit
        return None

    def getTablePeriod(self, data: list[list[Fact | None]]) -> Optional[_Period]:
        periods: set[_Period] = set()
        for row in data:
            for factOrNone in row:
                if factOrNone is None:
                    continue
                periods.add(factOrNone.period)
        if 1 == len(periods):
            return next(iter(periods))
        else:
            return None

    def getColumnPeriods(self, data: list[list[Fact | None]]) -> list[_Period | None]:
        colPeriodsMap: dict[int, set[_Period]] = defaultdict(set)
        totalNumberOfColumns: int = 0
        for row in data:
            totalNumberOfColumns = max(totalNumberOfColumns, len(row))
            for colnum, factOrNone in enumerate(row):
                if factOrNone is None:
                    continue
                fact: Fact = factOrNone
                colPeriodsMap[colnum].add(fact.period)
        # assert len(colUnitsMap) == totalNumberOfColumns, f"{len(colUnitsMap)} is not {totalNumberOfColumns}"
        columnPeriods: list[_Period | None] = []
        for c in range(totalNumberOfColumns):
            periods = colPeriodsMap[c]
            if 1 == len(periods):
                columnPeriods.append(next(iter(periods)))
                continue
            columnPeriods.append(None)
        assert len(columnPeriods) == totalNumberOfColumns
        if all(x is None for x in columnPeriods):
            return []
        return columnPeriods

    def getColumnUnits(self, data: list[list[Fact | None]]) -> list[str | None]:
        colUnitsMap: dict[int, set[str]] = defaultdict(set)
        totalNumberOfColumns: int = 0
        for row in data:
            totalNumberOfColumns = max(totalNumberOfColumns, len(row))
            for colnum, factOrNone in enumerate(row):
                if factOrNone is None:
                    continue
                fact: Fact = factOrNone
                if fact.concept.isNumeric:
                    colUnitsMap[colnum].add(fact.unitSymbol)
        # assert len(colUnitsMap) == totalNumberOfColumns, f"{len(colUnitsMap)} is not {totalNumberOfColumns}"
        columnUnits: list[str | None] = []
        for c in range(totalNumberOfColumns):
            units = colUnitsMap[c]
            if 1 == len(units):
                unit = next(iter(units))
                if unit:
                    columnUnits.append(unit)
                    continue
            columnUnits.append(None)
        assert len(columnUnits) == totalNumberOfColumns
        if all(x is None for x in columnUnits):
            return []
        return columnUnits


@dataclass(slots=True, frozen=True, eq=True)
class ReportSection:
    relationshipToFact: dict[Relationship, list[Fact]]
    presentation: PresentationGroup

    @property
    def title(self) -> str:
        return self.presentation.label

    @property
    def style(self) -> PresentationStyle:
        return self.presentation.style

    @property
    def hasFacts(self) -> bool:
        if self.presentation.style == PresentationStyle.Empty:
            return False
        return any(factList for factList in self.relationshipToFact.values())

    @property
    def tabular(self) -> bool:
        return False


@dataclass(slots=True, frozen=True, eq=True)
class TabularReportSection(ReportSection):
    tableStyle: TableStyle
    dataColumns: list[Concept | None]
    rowHeadings: list[Concept | str | None]
    data: list[list[Fact | None]]
    columnUnits: list[str | None]
    newColumnHeadings: list[list[TableHeadingCell]]
    columnPeriods: list[_Period | None]
    numeric: bool = False
    unitSymbol: Optional[str] = None
    period: Optional[_Period] = None

    @property
    def tabular(self) -> bool:
        return True

    @property
    def rowHeadingsHaveTitle(self) -> bool:
        if not self.dataColumns:
            return False
        firstCol = self.dataColumns[0]
        if firstCol is None:
            return False
        return True

    def columnHasUnit(self, colnum: int) -> bool:
        try:
            unit = self.columnUnits[colnum]
            return unit is not None
        except IndexError:
            return False

    @property
    def hasFacts(self) -> bool:
        for row in self.data:
            for fact in row:
                if fact is not None:
                    return True
        return False
