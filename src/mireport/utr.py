from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from functools import cache, cached_property
from typing import TYPE_CHECKING, TypeVar

if TYPE_CHECKING:
    from typing import Self

from mireport.exceptions import UnitException
from mireport.xml import ISO4217_NS, XBRLI_NS, QName, QNameMaker

_T = TypeVar("_T")


@cache
def _getQNameForUnitId(utr: UTR, unitId: str) -> QName | None:
    if utr._qnameMaker.isValidQName(unitId):
        return utr._qnameMaker.fromString(unitId)
    namespaces = utr._lookupNamespacesByUnitId.get(unitId)
    if namespaces is None:
        return None
    elif len(namespaces) > 1:
        raise UnitException(
            "Found non unique unit identifier {unitId}. Specify a QName not a name to avoid this exception."
        )
    return utr._qnameMaker.fromNamespaceAndLocalName(
        namespace=namespaces[0], localName=unitId
    )


def _byDataType(table: dict[str | QName, _T], dataType: QName) -> _T | None:
    """The entry for ``dataType``: under its QName if the UTR gave the item type a namespace,
    else under its local name, which is how the UTR lists some of them."""
    return table.get(dataType) or table.get(dataType.localName)


@cache
def _getUnitsForDataType(utr: UTR, dataType: QName) -> frozenset[QName]:
    """The named units the UTR permits for a given data type. Empty for a type the UTR does
    not constrain, and for one that takes a complex unit (see ``UTR.complexUnitFor``)."""
    return frozenset(
        unitQName
        for unitId in _byDataType(utr._lookupUnitIdByDataType, dataType) or ()
        if (unitQName := utr.getQNameForUnitId(unitId)) is not None
    )


@dataclass(frozen=True, slots=True)
class ComplexUnit:
    """A UTR unit that is one item type's unit over another's (emissions per monetary unit),
    not a single named unit: a fact of such a type is reported in a divide unit, a numerator
    unit valid for ``numerator`` over a denominator unit valid for ``denominator``."""

    unitId: str
    numerator: QName | str
    denominator: QName | str


class UTR:
    def __init__(
        self,
        unitToNamespaces: dict[str, list[str]],
        dataTypeToUnit: dict[str | QName, list[str]],
        unitQNamesToEntries: dict[QName, dict[str, str]],
        qnameMaker: QNameMaker,
        complexUnits: dict[str | QName, ComplexUnit] | None = None,
    ) -> None:
        self._complexUnits: dict[str | QName, ComplexUnit] = complexUnits or {}
        self._lookupNamespacesByUnitId: dict[str, list[str]] = unitToNamespaces
        self._lookupUnitIdByDataType: dict[str | QName, list[str]] = dataTypeToUnit
        self._lookupUnitEntriesByQName: dict[QName, dict[str, str]] = (
            unitQNamesToEntries
        )
        self._qnameMaker: QNameMaker = qnameMaker

    @classmethod
    def fromDict(cls, utr: dict, *, qnameMaker: QNameMaker) -> Self:
        """Load the UTR from a file."""
        unitToNamespaces: dict[str, list[str]] = {}
        dataTypeToUnit: dict[str | QName, list[str]] = defaultdict(list)
        unitQNamesToEntries: dict[QName, dict[str, str]] = {}
        complexUnits: dict[str | QName, ComplexUnit] = {}
        for entry in utr["utr"]:
            unitId = entry["unitId"]
            localName = entry["itemType"]
            if (namespace := entry.get("nsItemType")) is not None:
                dataType = qnameMaker.fromNamespaceAndLocalName(
                    namespace=namespace, localName=localName
                )
                dataTypeToUnit[dataType].append(unitId)
            else:
                dataType = localName
                dataTypeToUnit[localName].append(unitId)

            if "numeratorItemType" in entry:
                complexUnits[dataType] = ComplexUnit(
                    unitId,
                    cls._itemType(
                        qnameMaker,
                        entry["numeratorItemType"],
                        entry.get("nsNumeratorItemType"),
                    ),
                    cls._itemType(
                        qnameMaker,
                        entry["denominatorItemType"],
                        entry.get("nsDenominatorItemType"),
                    ),
                )
            else:
                # simple unit
                unitNamespace = entry["nsUnit"]
                unitToNamespaces.setdefault(unitId, []).append(unitNamespace)
                unitQName = qnameMaker.fromNamespaceAndLocalName(unitNamespace, unitId)

                unitEntry: dict[str, str] = entry.copy()
                for k in ("unitId", "nsUnit", "itemType", "nsItemType"):
                    unitEntry.pop(k, None)
                unitQNamesToEntries[unitQName] = unitEntry

        return cls(
            unitToNamespaces,
            dataTypeToUnit,
            unitQNamesToEntries,
            qnameMaker,
            complexUnits,
        )

    @staticmethod
    def _itemType(
        qnameMaker: QNameMaker, localName: str, namespace: str | None
    ) -> QName | str:
        if namespace is None:
            return localName
        return qnameMaker.fromNamespaceAndLocalName(namespace, localName)

    def complexUnitFor(self, dataType: QName) -> ComplexUnit | None:
        """The complex (per-unit) UTR unit for ``dataType``, if the UTR gives it one rather
        than a list of named units. ``getUnitsForDataType`` knows only named units, so it is
        empty for these types; that does *not* make them unconstrained."""
        return _byDataType(self._complexUnits, dataType)

    def validComplexUnit(
        self, dataType: QName, numeratorUnit: QName, denominatorUnit: QName
    ) -> bool:
        """Whether ``numeratorUnit`` over ``denominatorUnit`` is a unit the UTR permits for the
        ``dataType``: each side valid for its own item type."""
        complexUnit = self.complexUnitFor(dataType)
        if complexUnit is None:
            return False
        return self._validForItemType(
            complexUnit.numerator, numeratorUnit
        ) and self._validForItemType(complexUnit.denominator, denominatorUnit)

    def _validForItemType(self, itemType: QName | str, unit: QName) -> bool:
        if isinstance(itemType, str):
            unitIds = self._lookupUnitIdByDataType.get(itemType, [])
            return any(self.getQNameForUnitId(unitId) == unit for unitId in unitIds)
        return unit in self.getUnitsForDataType(itemType)

    def getQNameForUnitId(self, unitId: str) -> QName | None:
        return _getQNameForUnitId(self, unitId)

    def getUnitsForDataType(self, dataType: QName) -> frozenset[QName]:
        """Get the unit IDs for a given data type."""
        return _getUnitsForDataType(self, dataType)

    def getSymbolForUnit(self, unit: QName) -> str:
        unitEntry = self._lookupUnitEntriesByQName[unit]
        return unitEntry.get("symbol", "")

    @cached_property
    def _currencies(self) -> frozenset[str]:
        return frozenset(
            self._lookupUnitIdByDataType[
                self._qnameMaker.fromNamespaceAndLocalName(XBRLI_NS, "monetaryItemType")
            ]
        )

    def validCurrency(self, unit: QName | str) -> bool:
        if isinstance(unit, QName):
            if unit.namespace is not ISO4217_NS:
                return False
            code = unit.localName
        else:
            code = unit
        return code in self._currencies

    def valid(self, dataType: QName, unitType: QName) -> bool:
        """Whether the single unit ``unitType`` is one the UTR permits for ``dataType``.

        A type the UTR does not list is unconstrained, so any unit is valid. A type with a complex unit
        (see ``complexUnitFor``) is listed, but with no named unit, so no single unit is valid
        for it: it takes a divide unit, checked with ``validComplexUnit``."""
        if units_for_dataType := self.getUnitsForDataType(dataType):
            return unitType in units_for_dataType
        return self.complexUnitFor(dataType) is None
