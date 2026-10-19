from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from functools import cache, cached_property
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from typing import Self

from mireport.exceptions import UnitException
from mireport.xml import ISO4217_NS, XBRLI_NS, QName, QNameMaker


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


@cache
def _getUnitsForDataType(utr: UTR, dataType: QName) -> frozenset[QName]:
    """Get the unit IDs for a given data type."""
    possible = utr._lookupUnitIdByDataType.get(dataType)
    if not possible:
        possible = utr._lookupUnitIdByDataType.get(dataType.localName)
    if possible:
        return frozenset(
            unitQName
            for unitId in possible
            if (unitQName := utr.getQNameForUnitId(unitId)) is not None
        )
    return frozenset()


@cache
def _getUnitIdsForDataType(utr: UTR, dataType: QName) -> frozenset[str]:
    """Get the unit ID for a given data type."""
    possible: list[str] = []
    possible.extend(utr._lookupUnitIdByDataType.get(dataType, []))
    possible.extend(utr._lookupUnitIdByDataType.get(dataType.localName, []))
    return frozenset(possible)


@dataclass(frozen=True, slots=True)
class CompositeUnit:
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
        composites: dict[str | QName, CompositeUnit] | None = None,
    ) -> None:
        self._composites: dict[str | QName, CompositeUnit] = composites or {}
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
        composites: dict[str | QName, CompositeUnit] = {}
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
                composites[dataType] = CompositeUnit(
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
            composites,
        )

    @staticmethod
    def _itemType(
        qnameMaker: QNameMaker, localName: str, namespace: str | None
    ) -> QName | str:
        if namespace is None:
            return localName
        return qnameMaker.fromNamespaceAndLocalName(namespace, localName)

    def compositeFor(self, dataType: QName) -> CompositeUnit | None:
        """The composite (per-unit) definition for ``dataType``, if the UTR gives it one rather
        than a list of named units. ``getUnitsForDataType`` knows only named units, so it is
        empty for these types; that does *not* make them unconstrained."""
        return self._composites.get(dataType) or self._composites.get(
            dataType.localName
        )

    def validComposite(
        self, dataType: QName, numeratorUnit: QName, denominatorUnit: QName
    ) -> bool:
        """Whether ``numeratorUnit`` over ``denominatorUnit`` is a unit the UTR permits for the
        composite ``dataType``: each side valid for its own item type."""
        composite = self.compositeFor(dataType)
        if composite is None:
            return False
        return self._validForItemType(
            composite.numerator, numeratorUnit
        ) and self._validForItemType(composite.denominator, denominatorUnit)

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

    def getUnitIdsForDataType(self, dataType: QName) -> frozenset[str]:
        """Get the unit ID for a given data type."""
        return _getUnitIdsForDataType(self, dataType)

    def getSymbolForUnit(self, unit: QName, dataType: QName) -> str:
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
        units_for_dataType = self.getUnitsForDataType(dataType)
        if not units_for_dataType:
            # If the data type is not in the UTR, there is no UTR validation,
            # i.e. it is a valid combination.
            return True
        else:
            return unitType in units_for_dataType
