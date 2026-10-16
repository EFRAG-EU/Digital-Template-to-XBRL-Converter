"""Facts whose taxonomy gives no hypercube in presentation still reach the report.

The TPT taxonomy defines its hypercubes in the definition linkbase only; its presentation groups
are plain lists. mireport used to drop every dimensionally qualified fact from such a group.
"""

from __future__ import annotations

import copy
import re
import warnings
from collections.abc import Iterator
from typing import Any

import pytest

import mireport.taxonomy as _taxonomy_module
from mireport.conversionresults import ConversionResultsBuilder
from mireport.exceptions import InlineReportException
from mireport.taxonomy import loadTaxonomyJSON
from mireport.xbrljson_reader import XbrlJsonProcessor

_NS = "https://example.com/tp"
_ENTRY = "test://layout/dimensional-fallback"
_ROLE = "https://example.com/tp/role/Group"
_LABEL = "http://www.xbrl.org/2003/role/label"
DURATION = "2025-01-01T00:00:00/2026-01-01T00:00:00"


@pytest.fixture(autouse=True)
def _isolated_taxonomy_registry() -> Iterator[None]:
    before = set(_taxonomy_module._TAXONOMIES)
    yield
    for entryPoint in set(_taxonomy_module._TAXONOMIES) - before:
        del _taxonomy_module._TAXONOMIES[entryPoint]


def _concept(
    label: str,
    *,
    numeric: bool = False,
    abstract: bool = False,
    dimension: bool = False,
    hypercube: bool = False,
    other: dict[str, Any] | None = None,
) -> dict[str, Any]:
    kind = "xbrli:decimalItemType" if numeric else "xbrli:stringItemType"
    concept: dict[str, Any] = {
        "labels": {"en": {_LABEL: label}},
        "dataType": kind,
        "baseDataType": kind,
        "periodType": "duration",
    }
    if numeric:
        concept["numeric"] = True
    if abstract or dimension or hypercube:
        concept["abstract"] = True
    if dimension:
        concept["dimension"] = True
    if hypercube:
        concept["hypercube"] = True
    if other:
        concept["other"] = other
    return concept


def _cube(
    items: list[str], explicit: dict[str, list[str]], typed: list[str] | None = None
) -> dict[str, Any]:
    cube: dict[str, Any] = {
        "primaryItems": [[i, q] for i, q in enumerate(items)],
        "xbrldt:contextElement": "scenario",
        "xbrldt:closed": True,
        "explicitDimensions": explicit,
    }
    if typed:
        cube["typedDimensions"] = typed
    return cube


def _load(*, percent: bool = False) -> None:
    taxonomy = _taxonomy_json()
    if percent:
        taxonomy["namespaces"]["dtr-types"] = "http://www.xbrl.org/dtr/type/2022-03-31"
        taxonomy["concepts"]["tp:Share"] = _percent_concept()
        taxonomy["presentation"][_ROLE]["rows"].append([1, "tp:Share"])
    loadTaxonomyJSON(taxonomy)


def _percent_concept() -> dict[str, Any]:
    concept = _concept("Share", numeric=True)
    concept["dataType"] = "dtr-types:percentItemType"
    concept["baseDataType"] = "xbrli:pureItemType"
    return concept


def _taxonomy_json() -> dict[str, Any]:
    return {
        "entryPoint": _ENTRY,
        "namespaces": {"tp": _NS, "xs": "http://www.w3.org/2001/XMLSchema"},
        "concepts": {
            "tp:Group": _concept("Group", abstract=True),
            "tp:Emissions": _concept("Emissions", numeric=True),
            "tp:Total": _concept("Total", numeric=True),
            "tp:Intensity": _concept("Intensity", numeric=True),
            "tp:Note": _concept("Note"),
            "tp:Orphan": _concept("Orphan"),
            "tp:ScopeAxis": _concept("Scope [Axis]", dimension=True),
            "tp:ScopeA": _concept("Scope A", abstract=True),
            "tp:ScopeB": _concept("Scope B", abstract=True),
            "tp:TargetAxis": _concept(
                "Target [Axis]", dimension=True, other={"typedElement": "tp:TYP"}
            ),
            "tp:ScopeCube": _concept("Scope cube", hypercube=True),
            "tp:TargetCube": _concept("Target cube", hypercube=True),
        },
        "xs_elements": {
            "tp:TYP": {"dataType": "xs:string", "baseDataType": "xs:string"}
        },
        # Only primary items are presented: no hypercube, axis or member in the group.
        "presentation": {
            _ROLE: {
                "definition": "Group",
                "rows": [
                    [0, "tp:Group"],
                    [1, "tp:Emissions"],
                    [1, "tp:Total"],
                    [1, "tp:Intensity"],
                    [1, "tp:Note"],
                ],
            }
        },
        "dimensions": {
            "_defaults": {},
            _ROLE: {
                "tp:ScopeCube": _cube(
                    ["tp:Emissions"], {"tp:ScopeAxis": ["tp:ScopeA", "tp:ScopeB"]}
                ),
                "tp:TargetCube": _cube(
                    ["tp:Intensity", "tp:Note"], {}, ["tp:TargetAxis"]
                ),
            },
        },
    }


def _fact(concept: str, value: str, **dims: str) -> dict[str, Any]:
    return {
        "value": value,
        "decimals": 0,
        "dimensions": {
            "concept": concept,
            "entity": "lei:529900T8BM49AURSDO55",
            "period": DURATION,
            **dims,
        },
    }


def _document() -> dict[str, Any]:
    return {
        "documentInfo": {
            "documentType": "https://xbrl.org/2021/xbrl-json",
            "namespaces": {
                "tp": _NS,
                "lei": "http://standards.iso.org/iso/17442",
            },
            "taxonomy": [_ENTRY],
        },
        "facts": {
            "f1": _fact("tp:Emissions", "10", **{"tp:ScopeAxis": "tp:ScopeA"}),
            "f2": _fact("tp:Emissions", "20", **{"tp:ScopeAxis": "tp:ScopeB"}),
            "f3": _fact("tp:Total", "30"),
            "f4": _fact("tp:Intensity", "5", **{"tp:TargetAxis": "T1"}),
            "f5": _fact("tp:Intensity", "7", **{"tp:TargetAxis": "T2"}),
            "f6": _fact("tp:Note", "note one", **{"tp:TargetAxis": "T1"}),
        },
    }


def _render(document: dict[str, Any], *, strict: bool = False) -> str:
    _load()
    processor = XbrlJsonProcessor(document, ConversionResultsBuilder(), strict=strict)
    report = processor.createReport()
    return report.getInlineReport().fileContent.decode("utf-8")


def _tagged(html: str) -> list[str]:
    return re.findall(r'<ix:non(?:Fraction|Numeric)[^>]*name="([^"]+)"', html)


def test_every_fact_is_shown_though_presentation_has_no_hypercube() -> None:
    html = _render(_document(), strict=True)
    assert sorted(_tagged(html)) == sorted(
        ["tp:Emissions"] * 2 + ["tp:Total"] + ["tp:Intensity"] * 2 + ["tp:Note"]
    )


def test_each_dimension_set_gets_its_own_titled_table() -> None:
    html = _render(_document())
    titles = [t.strip() for t in re.findall(r"<h2[^>]*>\s*([^<]*)", html)]
    assert "Group - by Scope" in titles
    assert "Group - by Target" in titles
    assert html.count('<table class="thematic-table">') == 2


def test_the_smaller_axis_becomes_the_columns() -> None:
    html = _render(_document())
    byScope, byTarget = re.findall(r"<table.*?</table>", html, re.DOTALL)
    # One concept across two scopes: the scopes are rows.
    assert re.findall(r'<th class="[^"]*">\s*(Scope [AB])', byScope) == [
        "Scope A",
        "Scope B",
    ]
    # Two concepts across two targets: concepts are rows, targets are columns.
    headings = re.findall(r"<th[^>]*>\s*([^<]*?)\s*</th>", byTarget)
    assert "T1" in headings and "T2" in headings
    assert "Intensity" in headings and "Note" in headings


def test_a_gap_in_the_grid_is_left_blank() -> None:
    html = _render(_document())
    byTarget = re.findall(r"<table.*?</table>", html, re.DOTALL)[1]
    # Note has no fact for T2.
    assert byTarget.count("<td></td>") == 1


def test_a_percent_is_shown_scaled_but_stored_as_given() -> None:
    """percentItemType holds a fraction: 0.1250 at decimals 4 is 12.50%. The page shows 12.50 and
    tags it ix:scale -2, so the XBRL value and decimals come out exactly as the source gave them."""
    _load(percent=True)
    doc = _document()
    doc["facts"] = {"p1": _fact("tp:Share", "0.1250")}
    doc["facts"]["p1"]["decimals"] = 4
    processor = XbrlJsonProcessor(doc, ConversionResultsBuilder(), strict=True)
    html = processor.createReport().getInlineReport().fileContent.decode("utf-8")
    tag = re.search(r"<ix:nonFraction[^>]*name=\"tp:Share\"[^>]*>([^<]*)<", html)
    assert tag, html[-2000:]
    assert 'scale="-2"' in tag.group(0)
    assert 'decimals="4"' in tag.group(0)  # XBRL's decimals, not display's (2) or 6
    assert tag.group(1) == "12.50"


def test_a_fact_that_appears_nowhere_is_an_error_when_strict() -> None:
    doc = _document()
    doc["facts"]["f9"] = _fact("tp:Orphan", "lost")  # in no presentation group
    with pytest.raises(InlineReportException, match="appear nowhere"):
        _render(doc, strict=True)


def test_a_fact_that_appears_nowhere_is_left_out_when_not_strict() -> None:
    doc = copy.deepcopy(_document())
    doc["facts"]["f9"] = _fact("tp:Orphan", "lost")
    assert "tp:Orphan" not in _tagged(_render(doc))


def test_nothing_handed_to_aoix_is_deprecated() -> None:
    """Rendering typed dimensions must not use the deprecated 'typed' keyword."""
    with warnings.catch_warnings():
        warnings.simplefilter("error", DeprecationWarning)
        _render(_document(), strict=True)


def test_a_prior_period_fact_sits_beside_the_current_one_in_a_list() -> None:
    """A concept with a current and a prior fact is one card: the current value, and the prior
    beside it with its period."""
    doc = _document()
    other = _fact("tp:Total", "99")
    other["dimensions"]["period"] = "2024-01-01T00:00:00/2025-01-01T00:00:00"
    doc["facts"]["prior"] = other
    html = _render(doc)
    assert sorted(_tagged(html)).count("tp:Total") == 2  # both are in the document
    cards = [
        c
        for c in re.findall(r'<dl class="fact .*?</dl>', html, re.DOTALL)
        if "tp:Total" in c
    ]
    assert len(cards) == 1  # one card for the concept, not one per period
    (prior,) = re.findall(
        r'class="fact-comparative"[^>]*>(.*?)</div>', cards[0], re.DOTALL
    )
    assert "2024-01-01 &#8211; 2024-12-31" in prior and ">99<" in prior
    assert "Reporting period" not in cards[0]  # the card is the current period's


def test_a_fact_only_in_another_period_says_which_in_a_list() -> None:
    doc = _document()
    only_prior = _fact("tp:Total", "77")
    only_prior["dimensions"]["period"] = "2024-01-01T00:00:00/2025-01-01T00:00:00"
    del doc["facts"]["f3"]  # the current Total
    doc["facts"]["prior"] = only_prior
    html = _render(doc)
    assert re.findall(r"Reporting period: ([^<]*)<", html) == [
        "2024-01-01 &#8211; 2024-12-31"
    ]


@pytest.mark.parametrize(
    ("value", "decimals", "shown"),
    [
        ("12.346", 2, "12.35"),  # decimals set how many digits are shown
        ("12.5", 0, "12.5"),  # but 0 decimals does not round the value away
        ("12.5", "INF", "12.5"),
        ("1234", 0, "1,234"),
    ],
)
def test_the_digits_shown_follow_the_decimals_without_changing_a_whole_value(
    value: str, decimals: object, shown: str
) -> None:
    doc = _document()
    doc["facts"] = {"f": _fact("tp:Total", value)}
    doc["facts"]["f"]["decimals"] = decimals
    html = _render(doc)
    assert re.search(r'name="tp:Total"[^>]*>([^<]*)<', html).group(1) == shown  # type: ignore[union-attr]
