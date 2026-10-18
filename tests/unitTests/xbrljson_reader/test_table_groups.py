"""A presentation group whose hypercube has more (or fewer) than one dimension.

Layout can follow a hypercube of exactly one dimension. A group with two used to yield no table at
all, silently losing its facts; they are now laid out from their own dimensions.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import Any

from tests.unitTests.synthetic_taxonomy import (
    NAMESPACE,
    concept,
    cube,
    document,
    fact,
    qname,
    taxonomy_json,
)

from mireport.conversionresults import ConversionResultsBuilder
from mireport.taxonomy import Taxonomy
from mireport.xbrljson_reader import XbrlJsonProcessor

ENTRY_POINT = "test://synthetic/table-groups"
ROLE = f"{NAMESPACE}/role/Table"


def _taxonomy(dimensions: list[str]) -> dict[str, Any]:
    """A table group (its presentation holds a hypercube) over the given explicit dimensions."""
    members = {qname(d): [qname(f"{d}1"), qname(f"{d}2")] for d in dimensions}
    concepts = {
        qname("Cube"): concept("Cube", hypercube=True),
        qname("Value"): concept("Value", numeric=True),
        **{qname(d): concept(f"{d} [Axis]", dimension=True) for d in dimensions},
        **{
            member: concept(member.partition(":")[2], abstract=True)
            for axisMembers in members.values()
            for member in axisMembers
        },
    }
    rows = [
        [0, qname("Cube")],
        # each axis with its members beneath it, as a real presentation has
        *[
            row
            for d in dimensions
            for row in (
                [1, qname(d)],
                [2, qname(f"{d}1")],
                [2, qname(f"{d}2")],
            )
        ],
        [1, qname("Value")],
    ]
    return taxonomy_json(
        ENTRY_POINT,
        concepts=concepts,
        presentation={ROLE: {"definition": "Table", "rows": rows}},
        dimensions={ROLE: {qname("Cube"): cube([qname("Value")], members)}},
    )


def _document(dimensions: list[str]) -> dict[str, Any]:
    combinations = (
        [(1, 1), (1, 2), (2, 1), (2, 2)] if len(dimensions) == 2 else [(1,), (2,)]
    )
    return document(
        ENTRY_POINT,
        {
            f"f{n}": fact(
                qname("Value"),
                str(10 * (n + 1)),
                dimensions={
                    qname(d): qname(f"{d}{i}") for d, i in zip(dimensions, combo)
                },
            )
            for n, combo in enumerate(combinations)
        },
    )


def _render(
    register_taxonomy: Callable[[dict[str, Any]], Taxonomy], dimensions: list[str]
) -> str:
    register_taxonomy(_taxonomy(dimensions))
    processor = XbrlJsonProcessor(
        _document(dimensions), ConversionResultsBuilder(), strict=True
    )
    return processor.createReport().getInlineReport().fileContent.decode("utf-8")


def _tagged(html: str) -> list[str]:
    return re.findall(r'<ix:non(?:Fraction|Numeric)[^>]*name="([^"]+)"', html)


def test_a_hypercube_with_one_dimension_is_the_table_it_describes(
    register_taxonomy: Callable[[dict[str, Any]], Taxonomy],
) -> None:
    html = _render(register_taxonomy, ["A"])
    assert _tagged(html) == [qname("Value")] * 2
    titles = [t.strip() for t in re.findall(r"<h2[^>]*>\s*([^<]*)", html)]
    assert "Table" in titles
    assert not any(
        "by" in t for t in titles
    )  # the hypercube said how; no fallback table


def test_a_hypercube_with_two_dimensions_shows_every_fact_in_a_table_of_its_own(
    register_taxonomy: Callable[[dict[str, Any]], Taxonomy],
) -> None:
    html = _render(register_taxonomy, ["A", "B"])  # strict: raises if a fact is lost
    assert _tagged(html) == [qname("Value")] * 4
    titles = [t.strip() for t in re.findall(r"<h2[^>]*>\s*([^<]*)", html)]
    assert "Table - by A and B" in titles
