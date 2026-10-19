"""The reader against a taxonomy whose presentation groups hold no hypercube.

How such a group is laid out is tested on an InlineReport in
``tests/unitTests/test_dimensional_fallback_layout.py``. This is what is particular to reading
xBRL-JSON into one: what is tagged, how a number is shown, which period a card names.
"""

from __future__ import annotations

import re
import warnings
from collections.abc import Callable
from typing import Any

import pytest
from tests.unitTests.synthetic_taxonomy import (
    GROUP_ROLE,
    concept,
    document,
    fact,
    group_document,
    group_taxonomy_json,
    qname,
)

from mireport.conversionresults import ConversionResultsBuilder
from mireport.exceptions import InlineReportException
from mireport.taxonomy import Taxonomy
from mireport.xbrljson_reader import XbrlJsonProcessor

PRIOR_YEAR = "2024-01-01T00:00:00/2025-01-01T00:00:00"


@pytest.fixture
def group(register_taxonomy: Callable[[dict[str, Any]], Taxonomy]) -> None:
    register_taxonomy(group_taxonomy_json())


def _render(doc: dict[str, Any], *, strict: bool = False) -> str:
    processor = XbrlJsonProcessor(doc, ConversionResultsBuilder(), strict=strict)
    return processor.createReport().getInlineReport().fileContent.decode("utf-8")


def _tagged(html: str) -> list[str]:
    return re.findall(r'<ix:non(?:Fraction|Numeric)[^>]*name="([^"]+)"', html)


def test_every_fact_is_tagged_though_presentation_has_no_hypercube(
    group: None,
) -> None:
    html = _render(group_document(), strict=True)
    assert sorted(_tagged(html)) == sorted(
        [qname("Emissions")] * 2
        + [qname("Total")]
        + [qname("Intensity")] * 2
        + [qname("Note")]
    )


def test_a_fact_that_appears_nowhere_is_an_error_when_strict(group: None) -> None:
    doc = group_document()
    doc["facts"]["f9"] = fact(
        qname("Orphan"), "lost", decimals=None
    )  # in no presentation group
    with pytest.raises(InlineReportException, match="appear nowhere"):
        _render(doc, strict=True)


def test_a_fact_that_appears_nowhere_is_left_out_when_not_strict(group: None) -> None:
    doc = group_document()
    doc["facts"]["f9"] = fact(qname("Orphan"), "lost", decimals=None)
    assert qname("Orphan") not in _tagged(_render(doc))


def test_nothing_handed_to_aoix_is_deprecated(group: None) -> None:
    """Rendering typed dimensions must not use the deprecated 'typed' keyword."""
    with warnings.catch_warnings():
        warnings.simplefilter("error", DeprecationWarning)
        _render(group_document(), strict=True)


def test_a_percent_is_shown_scaled_but_stored_as_given(
    register_taxonomy: Callable[[dict[str, Any]], Taxonomy],
) -> None:
    """percentItemType holds a fraction: 0.1250 at decimals 4 is 12.50%. The page shows 12.50 and
    tags it ix:scale -2, so the XBRL value and decimals come out exactly as the source gave them."""
    entryPoint = "test://synthetic/percent"
    taxonomy = group_taxonomy_json(entryPoint)
    taxonomy["namespaces"]["dtr-types"] = "http://www.xbrl.org/dtr/type/2022-03-31"
    share = concept("Share", numeric=True)
    share["dataType"] = "dtr-types:percentItemType"
    share["baseDataType"] = "xbrli:pureItemType"
    taxonomy["concepts"][qname("Share")] = share
    taxonomy["presentation"][GROUP_ROLE]["rows"].append([1, qname("Share")])
    register_taxonomy(taxonomy)

    doc = document(entryPoint, {"p1": fact(qname("Share"), "0.1250", decimals=4)})
    html = _render(doc, strict=True)
    tag = re.search(rf'<ix:nonFraction[^>]*name="{qname("Share")}"[^>]*>([^<]*)<', html)
    assert tag, html[-2000:]
    assert 'scale="-2"' in tag.group(0)
    assert 'decimals="4"' in tag.group(0)  # XBRL's decimals, not display's (2) or 6
    assert tag.group(1) == "12.50"


def test_a_fact_only_in_another_period_says_which_in_a_list(group: None) -> None:
    doc = group_document()
    del doc["facts"]["f3"]  # the Total in the default period
    doc["facts"]["other"] = fact(qname("Total"), "77", period=PRIOR_YEAR)
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
    group: None, value: str, decimals: object, shown: str
) -> None:
    doc = group_document()
    doc["facts"] = {"f": fact(qname("Total"), value, decimals=decimals)}  # type: ignore[arg-type]
    html = _render(doc)
    found = re.search(rf'name="{qname("Total")}"[^>]*>([^<]*)<', html)
    assert found and found.group(1) == shown
