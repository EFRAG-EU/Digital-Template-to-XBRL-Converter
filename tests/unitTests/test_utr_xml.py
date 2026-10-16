"""The Unit Type Registry as published (utr.xml) and as we bake it (utr.json)."""

from __future__ import annotations

import io
import json
import urllib.error
from pathlib import Path
from typing import Any

import pytest

from mireport import utr_xml
from mireport.exceptions import UtrSourceError
from mireport.utr_xml import (
    UTR_URL,
    ItemTypeRef,
    UtrRegistry,
    UtrUnit,
    loadUtrXml,
)

UTR = "http://www.xbrl.org/2009/utr"
XBRLI = "http://www.xbrl.org/2003/instance"
ISO4217 = "http://www.xbrl.org/2003/iso4217"

ACRE = """
    <unit id="u00001">
      <unitId>acre</unitId>
      <unitName>Acre</unitName>
      <nsUnit>http://www.xbrl.org/2009/utr</nsUnit>
      <itemType>areaItemType</itemType>
      <itemTypeDate>2009-12-16</itemTypeDate>
      <symbol>a</symbol>
      <definition>Acre</definition>
      <baseStandard>Customary</baseStandard>
      <conversionPresentation>
        <math xmlns="http://www.w3.org/1998/Math/MathML"><mn>4046.86</mn></math>
      </conversionPresentation>
      <status>REC</status>
      <versionDate>2012-10-31</versionDate>
    </unit>"""

# a currency: nsItemType present, symbol present
AED = """
    <unit id="u00002">
      <unitId>AED</unitId>
      <unitName>U.A.E. dirham</unitName>
      <nsUnit>http://www.xbrl.org/2003/iso4217</nsUnit>
      <itemType>monetaryItemType</itemType>
      <nsItemType>http://www.xbrl.org/2003/instance</nsItemType>
      <symbol>AED</symbol>
      <definition>United Arab Emirates dirham</definition>
      <baseStandard>ISO 4217</baseStandard>
      <status>REC</status>
      <versionDate>2012-10-31</versionDate>
    </unit>"""

# a currency whose symbol element is present but empty
SSP = (
    AED.replace("u00002", "u00003")
    .replace("AED", "SSP")
    .replace("<symbol>SSP</symbol>", "<symbol/>")
)

# a complex unit: no nsUnit, no symbol, numerator and denominator
EMISSIONS = """
    <unit id="u00004">
      <unitId>Emissions_per_Monetary</unitId>
      <unitName>Emissions/Monetary</unitName>
      <itemType>ghgEmissionsPerMonetaryItemType</itemType>
      <numeratorItemType>ghgEmissionsItemType</numeratorItemType>
      <nsNumeratorItemType>http://www.xbrl.org/2009/utr</nsNumeratorItemType>
      <denominatorItemType>monetaryItemType</denominatorItemType>
      <nsDenominatorItemType>http://www.xbrl.org/2003/instance</nsDenominatorItemType>
      <definition>CO2 equivalent emissions per monetary unit</definition>
      <baseStandard>Customary</baseStandard>
      <status>REC</status>
      <versionDate>2024-10-22</versionDate>
    </unit>"""

CANDIDATE = (
    ACRE.replace("u00001", "u00005")
    .replace("<unitId>acre</unitId>", "<unitId>rood</unitId>")
    .replace("<status>REC</status>", "<status>CR</status>")
)


def utrXml(*units: str, lastUpdated: str = "2025-03-18") -> bytes:
    return (
        f'<?xml version="1.0" encoding="UTF-8"?>\n'
        f'<utr lastUpdated="{lastUpdated}" version="1.0" xmlns="{UTR}">'
        f"<units>{''.join(units)}</units></utr>"
    ).encode()


def parse(*units: str) -> UtrRegistry:
    return UtrRegistry.fromXml(utrXml(*units))


def only(registry: UtrRegistry) -> UtrUnit:
    (unit,) = registry.units
    return unit


class TestParsing:
    def test_registry_records_when_it_was_last_updated(self) -> None:
        registry = UtrRegistry.fromXml(utrXml(ACRE, lastUpdated="2025-03-18"))
        assert registry.lastUpdated == "2025-03-18"

    def test_simple_unit(self) -> None:
        unit = only(parse(ACRE))
        assert unit == UtrUnit(
            id="u00001",
            unitId="acre",
            unitName="Acre",
            definition="Acre",
            status="REC",
            itemType=ItemTypeRef("areaItemType", None),
            nsUnit="http://www.xbrl.org/2009/utr",
            symbol="a",
            numerator=None,
            denominator=None,
        )
        assert unit.isComplex is False

    def test_item_type_namespace_is_kept_when_present(self) -> None:
        unit = only(parse(AED))
        assert unit.itemType == ItemTypeRef("monetaryItemType", XBRLI)
        assert unit.nsUnit == ISO4217

    def test_empty_symbol_element_is_no_symbol(self) -> None:
        assert only(parse(SSP)).symbol is None

    def test_complex_unit(self) -> None:
        unit = only(parse(EMISSIONS))
        assert unit.isComplex is True
        assert unit.nsUnit is None
        assert unit.symbol is None
        assert unit.numerator == ItemTypeRef("ghgEmissionsItemType", UTR)
        assert unit.denominator == ItemTypeRef("monetaryItemType", XBRLI)

    def test_units_keep_document_order(self) -> None:
        registry = parse(EMISSIONS, ACRE, AED)
        assert [u.unitId for u in registry.units] == [
            "Emissions_per_Monetary",
            "acre",
            "AED",
        ]


class TestParsingErrors:
    def test_malformed_xml(self) -> None:
        with pytest.raises(UtrSourceError, match="not well-formed"):
            UtrRegistry.fromXml(b"<utr><units>")

    def test_wrong_root_element(self) -> None:
        with pytest.raises(UtrSourceError, match="utr"):
            UtrRegistry.fromXml(b'<html xmlns="http://www.w3.org/1999/xhtml"/>')

    def test_missing_last_updated(self) -> None:
        xml = f'<utr xmlns="{UTR}"><units>{ACRE}</units></utr>'.encode()
        with pytest.raises(UtrSourceError, match="lastUpdated"):
            UtrRegistry.fromXml(xml)

    @pytest.mark.parametrize(
        "element", ["unitId", "unitName", "itemType", "definition", "status"]
    )
    def test_missing_required_element_names_the_unit(self, element: str) -> None:
        broken = ACRE.replace(f"<{element}>", f"<x{element}>").replace(
            f"</{element}>", f"</x{element}>"
        )
        with pytest.raises(UtrSourceError, match=rf"u00001.*{element}"):
            parse(broken)

    def test_simple_unit_without_nsunit(self) -> None:
        broken = ACRE.replace("<nsUnit>http://www.xbrl.org/2009/utr</nsUnit>", "")
        with pytest.raises(UtrSourceError, match=r"acre.*nsUnit"):
            parse(broken)

    def test_complex_unit_without_denominator(self) -> None:
        broken = EMISSIONS.replace(
            "<denominatorItemType>monetaryItemType</denominatorItemType>", ""
        )
        with pytest.raises(
            UtrSourceError, match=r"Emissions_per_Monetary.*denominator"
        ):
            parse(broken)

    def test_duplicate_unit_id(self) -> None:
        duplicate = ACRE.replace("u00001", "u00099")
        with pytest.raises(UtrSourceError, match=r"duplicate.*acre"):
            parse(ACRE, duplicate)


class TestOnlyRec:
    """Only recommendations (REC) are baked by default: candidate
    recommendations (CR) are not yet stable."""

    def test_unit_knows_whether_it_is_a_recommendation(self) -> None:
        assert only(parse(ACRE)).isRec is True
        assert only(parse(CANDIDATE)).isRec is False

    def test_json_drops_candidates_by_default(self) -> None:
        data = parse(ACRE, CANDIDATE).toJson()
        assert [u["unitId"] for u in data["utr"]] == ["acre"]

    def test_json_can_keep_candidates(self) -> None:
        data = parse(ACRE, CANDIDATE).toJson(onlyREC=False)
        assert [u["unitId"] for u in data["utr"]] == ["acre", "rood"]

    def test_write_passes_the_choice_through(self, tmp_path: Path) -> None:
        registry = parse(ACRE, CANDIDATE)
        registry.writeJson(tmp_path / "default.json")
        registry.writeJson(tmp_path / "all.json", onlyREC=False)
        assert "rood" not in (tmp_path / "default.json").read_text(encoding="utf-8")
        assert "rood" in (tmp_path / "all.json").read_text(encoding="utf-8")

    def test_parsing_itself_keeps_every_unit(self) -> None:
        assert len(parse(ACRE, CANDIDATE).units) == 2


class TestJson:
    def test_simple_unit_keys(self) -> None:
        assert only(parse(ACRE)).toJson() == {
            "unitId": "acre",
            "unitName": "Acre",
            "nsUnit": UTR,
            "itemType": "areaItemType",
            "symbol": "a",
            "definition": "Acre",
            "status": "REC",
        }

    def test_item_type_namespace_becomes_its_own_key(self) -> None:
        assert only(parse(AED)).toJson()["nsItemType"] == XBRLI

    def test_absent_symbol_is_omitted_not_blank(self) -> None:
        assert "symbol" not in only(parse(SSP)).toJson()

    def test_complex_unit_keys(self) -> None:
        assert only(parse(EMISSIONS)).toJson() == {
            "unitId": "Emissions_per_Monetary",
            "unitName": "Emissions/Monetary",
            "itemType": "ghgEmissionsPerMonetaryItemType",
            "numeratorItemType": "ghgEmissionsItemType",
            "nsNumeratorItemType": UTR,
            "denominatorItemType": "monetaryItemType",
            "nsDenominatorItemType": XBRLI,
            "definition": "CO2 equivalent emissions per monetary unit",
            "status": "REC",
        }

    def test_registry_json_has_provenance_and_units_sorted_by_unit_id(self) -> None:
        data = parse(EMISSIONS, ACRE, AED).toJson()
        assert data["lastUpdated"] == "2025-03-18"
        assert [u["unitId"] for u in data["utr"]] == [
            "AED",
            "Emissions_per_Monetary",
            "acre",
        ]

    def test_write_round_trips(self, tmp_path: Path) -> None:
        registry = parse(ACRE, AED, EMISSIONS)
        out = tmp_path / "utr.json"
        registry.writeJson(out)
        assert json.loads(out.read_text(encoding="utf-8")) == registry.toJson()

    def test_write_is_stable_and_escapes_non_ascii(self, tmp_path: Path) -> None:
        euro = AED.replace("<symbol>AED</symbol>", "<symbol>€</symbol>")
        first, second = tmp_path / "first.json", tmp_path / "second.json"
        parse(euro).writeJson(first)
        parse(euro).writeJson(second)
        text = first.read_text(encoding="utf-8")
        assert "\\u20ac" in text
        assert text == second.read_text(encoding="utf-8")
        assert not text.endswith("\n")

    def test_write_replaces_an_existing_file_and_leaves_nothing_behind(
        self, tmp_path: Path
    ) -> None:
        out = tmp_path / "utr.json"
        out.write_text('{"old": true}', encoding="utf-8")
        parse(ACRE).writeJson(out)
        assert "old" not in out.read_text(encoding="utf-8")
        assert list(tmp_path.iterdir()) == [out]

    def test_write_into_a_missing_directory_is_an_os_error(
        self, tmp_path: Path
    ) -> None:
        with pytest.raises(OSError):
            parse(ACRE).writeJson(tmp_path / "missing" / "utr.json")


class TestLoadUtrXml:
    def test_default_url_is_the_official_registry(self) -> None:
        assert UTR_URL == "https://www.xbrl.org/utr/utr.xml"

    def test_reads_a_local_file(self, tmp_path: Path) -> None:
        path = tmp_path / "utr.xml"
        path.write_bytes(b"<utr/>")
        assert loadUtrXml(str(path)) == b"<utr/>"

    def test_missing_local_file(self, tmp_path: Path) -> None:
        with pytest.raises(UtrSourceError, match="missing.xml"):
            loadUtrXml(str(tmp_path / "missing.xml"))

    def test_fetches_a_url(self, monkeypatch: pytest.MonkeyPatch) -> None:
        requested: list[Any] = []

        def fakeUrlopen(request: Any, timeout: float) -> io.BytesIO:
            requested.append((request, timeout))
            return io.BytesIO(b"<utr/>")

        monkeypatch.setattr(utr_xml.urllib.request, "urlopen", fakeUrlopen)
        assert loadUtrXml(UTR_URL) == b"<utr/>"
        ((_, timeout),) = requested
        assert timeout > 0

    @pytest.mark.parametrize(
        "error",
        [
            urllib.error.HTTPError(UTR_URL, 404, "Not Found", {}, None),  # type: ignore[arg-type]
            urllib.error.URLError("no route to host"),
            TimeoutError("timed out"),
        ],
    )
    def test_fetch_failures_are_utr_source_errors(
        self, monkeypatch: pytest.MonkeyPatch, error: Exception
    ) -> None:
        def failing(request: Any, timeout: float) -> io.BytesIO:
            raise error

        monkeypatch.setattr(utr_xml.urllib.request, "urlopen", failing)
        with pytest.raises(UtrSourceError, match=UTR_URL):
            loadUtrXml(UTR_URL)


class TestAgainstWhatWeShipToday:
    """The committed registries/utr.json was baked from the copy of utr.xml that
    ships inside Arelle. Reading that same file with our own reader must give the
    same units, so nothing changes until we choose to take a newer registry."""

    def test_same_units_as_the_committed_json(self) -> None:
        import arelle

        cached = (
            Path(arelle.__file__).parent
            / "resources/cache/http/www.xbrl.org/utr/utr.xml"
        )
        if not cached.exists():
            pytest.skip("Arelle's cached utr.xml is not available")
        committed = json.loads(
            (
                Path(__file__).resolve().parents[2]
                / "src/mireport/data/registries/utr.json"
            ).read_text(encoding="utf-8")
        )

        ours = UtrRegistry.fromXml(cached.read_bytes())

        assert sorted(ours.toJson()["utr"], key=lambda u: u["unitId"]) == sorted(
            committed["utr"], key=lambda u: u["unitId"]
        )
