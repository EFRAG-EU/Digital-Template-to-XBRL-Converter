from collections import defaultdict
from functools import cache
from typing import Optional, Self

from mireport.exceptions import UnitException
from mireport.xml import ISO4217_NS, XBRLI_NS, QName, QNameMaker


class UTR:
    def __init__(
        self,
        unitToNamespaces: dict[str, list[str]],
        dataTypeToUnit: dict[str | QName, list[str]],
        unitQNamesToEntries: dict[QName, dict[str, str]],
        qnameMaker: QNameMaker,
    ) -> None:
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

            if "numeratorItemType" not in entry:
                # simple unit
                unitNamespace = entry["nsUnit"]
                unitToNamespaces.setdefault(unitId, []).append(unitNamespace)
                unitQName = qnameMaker.fromNamespaceAndLocalName(unitNamespace, unitId)

                unitEntry: dict[str, str] = entry.copy()
                for k in ("unitId", "nsUnit", "itemType", "nsItemType"):
                    unitEntry.pop(k, None)
                unitQNamesToEntries[unitQName] = unitEntry

        return cls(unitToNamespaces, dataTypeToUnit, unitQNamesToEntries, qnameMaker)

    @cache
    def getQNameForUnitId(self, unitId: str) -> Optional[QName]:
        if self._qnameMaker.isValidQName(unitId):
            return self._qnameMaker.fromString(unitId)
        namespaces = self._lookupNamespacesByUnitId.get(unitId)
        if namespaces is None:
            return None
        elif len(namespaces) > 1:
            raise UnitException(
                "Found non unique unit identifier {unitId}. Specify a QName not a name to avoid this exception."
            )
        return self._qnameMaker.fromNamespaceAndLocalName(
            namespace=namespaces[0], localName=unitId
        )

    @cache
    def getUnitsForDataType(self, dataType: QName) -> frozenset[QName]:
        """Get the unit IDs for a given data type."""
        possible = self._lookupUnitIdByDataType.get(dataType)
        if not possible:
            possible = self._lookupUnitIdByDataType.get(dataType.localName)
        if possible:
            return frozenset(
                unitQName
                for unitId in possible
                if (unitQName := self.getQNameForUnitId(unitId)) is not None
            )
        return frozenset()

    @cache
    def getUnitIdsForDataType(self, dataType: QName) -> frozenset[str]:
        """Get the unit ID for a given data type."""
        possible: list[str] = []
        possible.extend(self._lookupUnitIdByDataType.get(dataType, []))
        possible.extend(self._lookupUnitIdByDataType.get(dataType.localName, []))
        return frozenset(possible)

    def getSymbolForUnit(self, unit: QName, dataType: QName) -> str:
        unitEntry = self._lookupUnitEntriesByQName[unit]
        return unitEntry.get("symbol", "")

    @cache
    def validCurrency(self, unit: QName | str) -> bool:
        if isinstance(unit, QName):
            unitId = unit.localName
            if unit.namespace is not ISO4217_NS:
                return False
        currencies = frozenset(
            self._lookupUnitIdByDataType[
                self._qnameMaker.fromNamespaceAndLocalName(XBRLI_NS, "monetaryItemType")
            ]
        )
        return unitId in currencies

    def valid(self, dataType: QName, unitType: QName) -> bool:
        units_for_dataType = self.getUnitsForDataType(dataType)
        if not units_for_dataType:
            # If the data type is not in the UTR, there is no UTR validation,
            # i.e. it is a valid combination.
            return True
        else:
            return unitType in units_for_dataType
