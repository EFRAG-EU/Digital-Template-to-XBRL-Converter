"""A presentation group whose hypercube has more (or fewer) than one dimension.

Layout can follow a hypercube of exactly one dimension. A group with two used to yield no table at
all, silently losing its facts; they are now laid out from their own dimensions.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from typing import Any

import pytest

import mireport.taxonomy as _taxonomy_module
from mireport.conversionresults import ConversionResultsBuilder
from mireport.taxonomy import loadTaxonomyJSON
from mireport.xbrljson_reader import XbrlJsonProcessor

_NS = "https://example.com/tg"
_ENTRY = "test://layout/table-groups"
_ROLE = "https://example.com/tg/role/Table"
_LABEL = "http://www.xbrl.org/2003/role/label"
DURATION = "2025-01-01T00:00:00/2026-01-01T00:00:00"


@pytest.fixture(autouse=True)
def _isolated_taxonomy_registry() -> Iterator[None]:
    before = set(_taxonomy_module._TAXONOMIES)
    yield
    for entryPoint in set(_taxonomy_module._TAXONOMIES) - before:
        del _taxonomy_module._TAXONOMIES[entryPoint]


def _concept(label: str, **kind: bool) -> dict[str, Any]:
    concept: dict[str, Any] = {
        "labels": {"en": {_LABEL: label}},
        "dataType": "xbrli:decimalItemType"
        if kind.get("numeric")
        else "xbrli:stringItemType",
        "baseDataType": "xbrli:decimalItemType"
        if kind.get("numeric")
        else "xbrli:stringItemType",
        "periodType": "duration",
    }
    for flag in ("numeric", "dimension", "hypercube"):
        if kind.get(flag):
            concept[flag] = True
    if kind.get("dimension") or kind.get("hypercube"):
        concept["abstract"] = True
    return concept


def _load(dimensions: list[str]) -> None:
    """A table group (its presentation holds a hypercube) over the given explicit dimensions."""
    members = {f"tg:{d}": [f"tg:{d}1", f"tg:{d}2"] for d in dimensions}
    concepts = {
        "tg:Cube": _concept("Cube", hypercube=True),
        "tg:Value": _concept("Value", numeric=True),
        **{f"tg:{d}": _concept(f"{d} [Axis]", dimension=True) for d in dimensions},
        **{
            m: _concept(m.split(":")[1], dimension=False)
            for ms in members.values()
            for m in ms
        },
    }
    for ms in members.values():
        for m in ms:
            concepts[m]["abstract"] = True
    loadTaxonomyJSON(
        {
            "entryPoint": _ENTRY,
            "namespaces": {"tg": _NS, "xs": "http://www.w3.org/2001/XMLSchema"},
            "concepts": concepts,
            "presentation": {
                _ROLE: {
                    "definition": "Table",
                    "rows": [
                        [0, "tg:Cube"],
                        # each axis with its members beneath it, as a real presentation has
                        *[
                            row
                            for d in dimensions
                            for row in (
                                [1, f"tg:{d}"],
                                [2, f"tg:{d}1"],
                                [2, f"tg:{d}2"],
                            )
                        ],
                        [1, "tg:Value"],
                    ],
                }
            },
            "dimensions": {
                "_defaults": {},
                _ROLE: {
                    "tg:Cube": {
                        "primaryItems": [[0, "tg:Value"]],
                        "xbrldt:contextElement": "scenario",
                        "xbrldt:closed": True,
                        "explicitDimensions": members,
                    }
                },
            },
        }
    )


def _document(dimensions: list[str]) -> dict[str, Any]:
    facts = {}
    for n, combo in enumerate(
        [(1, 1), (1, 2), (2, 1), (2, 2)] if len(dimensions) == 2 else [(1,), (2,)]
    ):
        facts[f"f{n}"] = {
            "value": str(10 * (n + 1)),
            "decimals": 0,
            "dimensions": {
                "concept": "tg:Value",
                "entity": "lei:529900T8BM49AURSDO55",
                "period": DURATION,
                **{f"tg:{d}": f"tg:{d}{i}" for d, i in zip(dimensions, combo)},
            },
        }
    return {
        "documentInfo": {
            "documentType": "https://xbrl.org/2021/xbrl-json",
            "namespaces": {"tg": _NS, "lei": "http://standards.iso.org/iso/17442"},
            "taxonomy": [_ENTRY],
        },
        "facts": facts,
    }


def _render(dimensions: list[str]) -> str:
    _load(dimensions)
    processor = XbrlJsonProcessor(
        _document(dimensions), ConversionResultsBuilder(), strict=True
    )
    return processor.createReport().getInlineReport().fileContent.decode("utf-8")


def _tagged(html: str) -> list[str]:
    return re.findall(r'<ix:non(?:Fraction|Numeric)[^>]*name="([^"]+)"', html)


def test_a_hypercube_with_one_dimension_is_the_table_it_describes() -> None:
    html = _render(["A"])
    assert _tagged(html) == ["tg:Value"] * 2
    titles = [t.strip() for t in re.findall(r"<h2[^>]*>\s*([^<]*)", html)]
    assert "Table" in titles
    assert not any(
        "by" in t for t in titles
    )  # the hypercube said how; no fallback table


def test_a_hypercube_with_two_dimensions_shows_every_fact_in_a_table_of_its_own() -> (
    None
):
    html = _render(["A", "B"])  # strict: it raises if any fact appears nowhere
    assert _tagged(html) == ["tg:Value"] * 4
    titles = [t.strip() for t in re.findall(r"<h2[^>]*>\s*([^<]*)", html)]
    assert "Table - by A and B" in titles
