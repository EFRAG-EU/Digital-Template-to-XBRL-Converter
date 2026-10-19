"""Composite UTR types: a unit that is one item type's unit over another's.

The UTR lists a few item types (emissions per monetary unit, say) with no named unit, only a
numerator and a denominator item type. ``getUnitsForDataType`` knows named units only, so it is
empty for them; that must not read as "the UTR does not constrain this type".
"""

from __future__ import annotations

import json

import pytest

from mireport.data import registries
from mireport.json import getResource
from mireport.utr import UTR
from mireport.xml import getBootstrapQNameMaker

UTR_NS = "http://www.xbrl.org/2009/utr"
XBRLI = "http://www.xbrl.org/2003/instance"
ISO = "http://www.xbrl.org/2003/iso4217"
DTR = "http://www.xbrl.org/dtr/type/2024-01-31"

_ENTRIES = {
    "utr": [
        {
            "unitId": "tCO2e",
            "itemType": "ghgEmissionsItemType",
            "nsItemType": UTR_NS,
            "nsUnit": UTR_NS,
        },
        {
            "unitId": "kg",
            "itemType": "massItemType",
            "nsItemType": UTR_NS,
            "nsUnit": UTR_NS,
        },
        {
            "unitId": "EUR",
            "itemType": "monetaryItemType",
            "nsItemType": XBRLI,
            "nsUnit": ISO,
        },
        {
            "unitId": "Emissions_per_Monetary",
            "itemType": "ghgEmissionsPerMonetaryItemType",
            "numeratorItemType": "ghgEmissionsItemType",
            "nsNumeratorItemType": UTR_NS,
            "denominatorItemType": "monetaryItemType",
            "nsDenominatorItemType": XBRLI,
        },
    ]
}


@pytest.fixture
def utr() -> UTR:
    return UTR.fromDict(_ENTRIES, qnameMaker=getBootstrapQNameMaker())


@pytest.fixture
def q():
    maker = getBootstrapQNameMaker()
    return maker.fromNamespaceAndLocalName


def test_a_composite_type_is_found_by_its_name_in_any_namespace(utr: UTR, q) -> None:
    composite = utr.compositeFor(q(DTR, "ghgEmissionsPerMonetaryItemType"))
    assert composite is not None
    assert composite.unitId == "Emissions_per_Monetary"
    assert composite.numerator.localName == "ghgEmissionsItemType"
    assert composite.denominator.localName == "monetaryItemType"


def test_a_simple_type_is_not_composite(utr: UTR, q) -> None:
    assert utr.compositeFor(q(UTR_NS, "massItemType")) is None


def test_a_composite_type_has_no_named_units_but_is_not_unconstrained(
    utr: UTR, q
) -> None:
    dataType = q(DTR, "ghgEmissionsPerMonetaryItemType")
    assert utr.getUnitsForDataType(dataType) == frozenset()
    assert utr.compositeFor(dataType) is not None


def test_each_side_must_be_valid_for_its_own_item_type(utr: UTR, q) -> None:
    dataType = q(DTR, "ghgEmissionsPerMonetaryItemType")
    tco2e, kg, eur = q(UTR_NS, "tCO2e"), q(UTR_NS, "kg"), q(ISO, "EUR")
    assert utr.validComposite(dataType, tco2e, eur)
    assert not utr.validComposite(dataType, kg, eur)  # a mass is not an emission
    assert not utr.validComposite(dataType, eur, tco2e)  # the wrong way up


def test_a_simple_type_is_never_a_valid_composite(utr: UTR, q) -> None:
    assert not utr.validComposite(
        q(UTR_NS, "massItemType"), q(UTR_NS, "kg"), q(ISO, "EUR")
    )


def test_the_real_utr_models_every_composite_it_lists() -> None:
    entries = json.loads(getResource(registries, "utr.json").read_text("utf-8"))["utr"]
    composites = [e for e in entries if "numeratorItemType" in e]
    real = UTR.fromDict({"utr": entries}, qnameMaker=getBootstrapQNameMaker())
    maker = getBootstrapQNameMaker()
    # an item type the UTR gives a namespace is looked up in it; the others come from the DTR
    found = {
        e["itemType"]
        for e in composites
        if real.compositeFor(
            maker.fromNamespaceAndLocalName(e.get("nsItemType", DTR), e["itemType"])
        )
        is not None
    }
    assert found == {e["itemType"] for e in composites}
    assert "ghgEmissionsPerMonetaryItemType" in found
