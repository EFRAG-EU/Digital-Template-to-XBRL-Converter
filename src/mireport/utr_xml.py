"""Read the Unit Type Registry as published (utr.xml) and write it as we bake it
(registries/utr.json).

The object model follows utr.xml: one UtrUnit per <unit>, with the *ItemType /
ns*ItemType element pairs as ItemTypeRefs. The JSON follows what
mireport.utr.UTR.fromDict reads. Deliberately stdlib-only, like
mireport.taxonomy_package: no Arelle start-up to read a small XML file.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory
from xml.etree import ElementTree

from mireport.exceptions import UtrSourceError
from mireport.xml import UTR_NS

UTR_URL = "https://www.xbrl.org/utr/utr.xml"

FETCH_TIMEOUT_SECONDS = 30


def _name(local: str) -> str:
    return f"{{{UTR_NS}}}{local}"


@dataclass(frozen=True, slots=True)
class ItemTypeRef:
    """An item type: utr.xml gives its local name and, optionally, its namespace
    in two sibling elements (e.g. itemType and nsItemType)."""

    localName: str
    namespace: str | None


@dataclass(frozen=True, slots=True)
class UtrUnit:
    """One <unit> of the registry.

    A simple unit is for one item type and has a namespace of its own (nsUnit); a
    complex unit is a ratio of two item types and has neither nsUnit nor a symbol.
    Elements nothing here uses (baseStandard, versionDate, itemTypeDate, the
    MathML conversions) are not modelled.
    """

    id: str
    unitId: str
    unitName: str
    definition: str
    status: str
    itemType: ItemTypeRef
    nsUnit: str | None
    symbol: str | None
    numerator: ItemTypeRef | None
    denominator: ItemTypeRef | None

    @property
    def isRec(self) -> bool:
        """Whether this is a recommendation, as opposed to a candidate (CR)."""
        return self.status == "REC"

    @property
    def isComplex(self) -> bool:
        return self.numerator is not None and self.denominator is not None

    def toJson(self) -> dict[str, str]:
        """The keys UTR.fromDict reads; those the unit lacks are omitted."""
        entry: dict[str, str] = {
            "unitId": self.unitId,
            "unitName": self.unitName,
            "itemType": self.itemType.localName,
            "definition": self.definition,
            "status": self.status,
        }
        if self.nsUnit is not None:
            entry["nsUnit"] = self.nsUnit
        if self.itemType.namespace is not None:
            entry["nsItemType"] = self.itemType.namespace
        if self.symbol is not None:
            entry["symbol"] = self.symbol
        for prefix, ref in (
            ("numerator", self.numerator),
            ("denominator", self.denominator),
        ):
            if ref is not None:
                entry[f"{prefix}ItemType"] = ref.localName
                if ref.namespace is not None:
                    entry[f"ns{prefix.title()}ItemType"] = ref.namespace
        return entry


def _text(unit: ElementTree.Element, local: str) -> str | None:
    """The stripped text of a child element; None if absent or empty."""
    text = unit.findtext(_name(local))
    if text is None or not (text := text.strip()):
        return None
    return text


def _itemTypeRef(
    unit: ElementTree.Element, localElement: str, namespaceElement: str, label: str
) -> ItemTypeRef | None:
    if (local := _text(unit, localElement)) is None:
        if _text(unit, namespaceElement) is not None:
            raise UtrSourceError(
                f"UTR unit {label}: {namespaceElement} but no {localElement}"
            )
        return None
    return ItemTypeRef(local, _text(unit, namespaceElement))


def _parseUnit(unit: ElementTree.Element) -> UtrUnit:
    unitId = _text(unit, "unitId")
    id_ = unit.get("id")
    label = f"{id_} ({unitId})" if unitId else str(id_)

    def required(local: str) -> str:
        if (value := _text(unit, local)) is None:
            raise UtrSourceError(f"UTR unit {label}: missing required element {local}")
        return value

    if id_ is None:
        raise UtrSourceError(f"UTR unit {label}: missing id attribute")
    unitId = required("unitId")
    itemType = _itemTypeRef(unit, "itemType", "nsItemType", label)
    if itemType is None:
        raise UtrSourceError(f"UTR unit {label}: missing required element itemType")

    numerator = _itemTypeRef(unit, "numeratorItemType", "nsNumeratorItemType", label)
    denominator = _itemTypeRef(
        unit, "denominatorItemType", "nsDenominatorItemType", label
    )
    if (numerator is None) != (denominator is None):
        missing = "denominatorItemType" if denominator is None else "numeratorItemType"
        raise UtrSourceError(
            f"UTR unit {label}: a complex unit needs both numerator and denominator "
            f"but has no {missing}"
        )
    nsUnit = _text(unit, "nsUnit")
    if numerator is None and nsUnit is None:
        raise UtrSourceError(f"UTR unit {label}: a simple unit needs an nsUnit")

    return UtrUnit(
        id=id_,
        unitId=unitId,
        unitName=required("unitName"),
        definition=required("definition"),
        status=required("status"),
        itemType=itemType,
        nsUnit=nsUnit,
        symbol=_text(unit, "symbol"),
        numerator=numerator,
        denominator=denominator,
    )


@dataclass(frozen=True, slots=True)
class UtrRegistry:
    lastUpdated: str
    units: tuple[UtrUnit, ...]

    @classmethod
    def fromXml(cls, xml: bytes) -> UtrRegistry:
        try:
            root = ElementTree.fromstring(xml)
        except ElementTree.ParseError as e:
            raise UtrSourceError(f"UTR is not well-formed XML: {e}") from e
        if root.tag != _name("utr"):
            raise UtrSourceError(
                f"UTR root element is {root.tag}, expected utr in {UTR_NS}"
            )
        if (lastUpdated := root.get("lastUpdated")) is None:
            raise UtrSourceError("UTR root element has no lastUpdated attribute")

        units = tuple(_parseUnit(u) for u in root.iter(_name("unit")))
        seen: dict[str, str] = {}
        for unit in units:
            if (first := seen.setdefault(unit.unitId, unit.id)) != unit.id:
                raise UtrSourceError(
                    f"UTR has a duplicate unitId {unit.unitId} ({first} and {unit.id})"
                )
        return cls(lastUpdated, units)

    def toJson(self, *, onlyREC: bool = True) -> dict[str, object]:
        """What mireport.utr.UTR.fromDict reads (the "utr" list), plus when the
        registry was last updated, which it ignores. By default only
        recommendations are included, since candidate recommendations (CR) are
        not yet stable."""
        units = sorted(
            (u for u in self.units if u.isRec or not onlyREC), key=lambda u: u.unitId
        )
        return {
            "lastUpdated": self.lastUpdated,
            "utr": [u.toJson() for u in units],
        }

    def writeJson(self, path: Path, *, onlyREC: bool = True) -> None:
        """Write toJson() to path. The file is only replaced once the new content
        has been written in full."""
        payload = json.dumps(self.toJson(onlyREC=onlyREC), indent=2, sort_keys=True)
        # Same directory as the target so Path.replace() is an atomic rename.
        with TemporaryDirectory(dir=path.parent, prefix=".utr-") as tmp:
            staged = Path(tmp) / path.name
            staged.write_text(payload, encoding="utf-8")
            staged.replace(path)


def loadUtrXml(source: str) -> bytes:
    """Fetch utr.xml from an http(s) URL, or read it from a local path."""
    if source.startswith(("http://", "https://")):
        try:
            request = urllib.request.Request(source)
            with urllib.request.urlopen(request, timeout=FETCH_TIMEOUT_SECONDS) as r:
                return r.read()  # type: ignore[no-any-return]
        except (urllib.error.URLError, OSError) as e:
            raise UtrSourceError(f"Could not fetch {source}: {e}") from e
    try:
        return Path(source).read_bytes()
    except OSError as e:
        raise UtrSourceError(f"Could not read {source}: {e}") from e
