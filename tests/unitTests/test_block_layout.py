"""A presentation group is laid out block by block: a role may hold several hypercubes, and line
items alongside them, in any order.

The roles here mirror the real structure: a depth-0 abstract holding a hypercube and its axes, with
the line items as a sibling abstract beneath it; which concepts are the hypercube's rows is
declared in the definition linkbase.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from datetime import date
from typing import Any, ClassVar

import pytest
from tests.unitTests.synthetic_taxonomy import (
    NAMESPACE,
    concept,
    cube,
    qname,
    taxonomy_json,
)
from tests.unitTests.table_integrity import problems

from mireport.report import InlineReport
from mireport.report.factbuilder import FactBuilder
from mireport.taxonomy import BlockKind, PresentationStyle, Taxonomy
from mireport.xml import getBootstrapQNameMaker

ROLE = f"{NAMESPACE}/role/Mixed"
_Q = getBootstrapQNameMaker()
_Q.addNamespacePrefix("utr", "http://www.xbrl.org/2009/utr")

SCOPE = qname("ScopeAxis")
TARGET = qname("TargetAxis")


def _common_concepts() -> dict[str, dict[str, Any]]:
    return {
        SCOPE: concept("Scope [Axis]", dimension=True),
        qname("ScopeA"): concept("Scope A", abstract=True),
        qname("ScopeB"): concept("Scope B", abstract=True),
        TARGET: concept(
            "Target [Axis]",
            dimension=True,
            other={"typedElement": qname("TargetType")},
        ),
        qname("Emissions"): concept("Emissions", numeric=True),
        qname("Intensity"): concept("Intensity", numeric=True),
        qname("Note"): concept("Note"),
        qname("Plain"): concept("Plain figure", numeric=True),
        qname("Other"): concept("Other figure", numeric=True),
    }


def _abstract(name: str, label: str) -> dict[str, dict[str, Any]]:
    return {qname(name): concept(label, abstract=True)}


def _build(
    concepts: dict[str, dict[str, Any]],
    rows: list[list[Any]],
    cubes: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    return taxonomy_json(
        "test://synthetic/blocks",
        concepts={**_common_concepts(), **concepts},
        presentation={ROLE: {"definition": "Group", "rows": rows}},
        dimensions={ROLE: cubes},
        xsElements={
            qname("TargetType"): {"dataType": "xs:string", "baseDataType": "xs:string"}
        },
    )


def _two_cubes() -> dict[str, Any]:
    """Like VS B4: two branches, each a hypercube and its line items, one explicit, one typed."""
    concepts = {
        qname("CubeOne"): concept("Emissions by scope [Table]", hypercube=True),
        qname("CubeTwo"): concept("Intensity by target [Table]", hypercube=True),
        **_abstract("BranchOne", "Emissions by scope"),
        **_abstract("BranchTwo", "Intensity by target"),
        **_abstract("LinesOne", "Line items"),
        **_abstract("LinesTwo", "Line items two"),
    }
    rows = [
        [0, qname("BranchOne")],
        [1, qname("CubeOne")],
        [2, SCOPE],
        [3, qname("ScopeA")],
        [3, qname("ScopeB")],
        [1, qname("LinesOne")],
        [2, qname("Emissions")],
        [0, qname("BranchTwo")],
        [1, qname("CubeTwo")],
        [2, TARGET],
        [1, qname("LinesTwo")],
        [2, qname("Intensity")],
        [2, qname("Note")],
    ]
    cubes = {
        qname("CubeOne"): cube(
            [qname("Emissions")], {SCOPE: [qname("ScopeA"), qname("ScopeB")]}
        ),
        qname("CubeTwo"): cube([qname("Intensity"), qname("Note")], {}, [TARGET]),
    }
    return _build(concepts, rows, cubes)


def _hybrid() -> dict[str, Any]:
    """Line items, then a table, then line items: three blocks. ``Hidden`` is a line item whose
    hypercube the role declares but never presents, so it can still carry a dimension."""
    concepts = {
        qname("Cube"): concept("Emissions by scope [Table]", hypercube=True),
        qname("HiddenCube"): concept("Hidden cube [Table]", hypercube=True),
        qname("Hidden"): concept("Hidden figure", numeric=True),
        **_abstract("BranchA", "Before the table"),
        **_abstract("BranchB", "The table"),
        **_abstract("BranchC", "After the table"),
        **_abstract("LinesB", "Table line items"),
    }
    rows = [
        [0, qname("BranchA")],
        [1, qname("Plain")],
        [0, qname("BranchB")],
        [1, qname("Cube")],
        [2, SCOPE],
        [3, qname("ScopeA")],
        [3, qname("ScopeB")],
        [1, qname("LinesB")],
        [2, qname("Emissions")],
        [0, qname("BranchC")],
        [1, qname("Other")],
        [1, qname("Hidden")],
    ]
    cubes = {
        qname("Cube"): cube(
            [qname("Emissions")], {SCOPE: [qname("ScopeA"), qname("ScopeB")]}
        ),
        qname("HiddenCube"): cube(
            [qname("Hidden")], {SCOPE: [qname("ScopeA"), qname("ScopeB")]}
        ),
    }
    return _build(concepts, rows, cubes)


def _items_before_table() -> dict[str, Any]:
    """The table's line items come before the table in the presentation (siblings of it): the
    definition linkbase, not their position, says they are its rows."""
    concepts = {
        qname("Cube"): concept("Emissions by scope [Table]", hypercube=True),
        **_abstract("Branch", "Emissions"),
        **_abstract("Lines", "Line items"),
    }
    rows = [
        [0, qname("Branch")],
        [1, qname("Lines")],
        [2, qname("Emissions")],
        [1, qname("Cube")],
        [2, SCOPE],
        [3, qname("ScopeA")],
        [3, qname("ScopeB")],
    ]
    cubes = {
        qname("Cube"): cube(
            [qname("Emissions")], {SCOPE: [qname("ScopeA"), qname("ScopeB")]}
        )
    }
    return _build(concepts, rows, cubes)


def _report(taxonomy: Taxonomy, facts: list[tuple[str, Any, dict[str, str]]]):
    report = InlineReport(taxonomy)
    report.setEntity("http://standards.iso.org/iso/17442", "529900T8BM49AURSDO55")
    report.addDurationPeriod("cur", date(2025, 1, 1), date(2025, 12, 31))
    report.setDefaultPeriodName("cur")
    report.requireAllFactsRendered = True
    for name, value, dims in facts:
        c = taxonomy.getConcept(name)
        fb = FactBuilder(report).setConcept(c).setValue(value)
        if c.isNumeric:
            fb.setSimpleUnit(_Q.fromString("utr:kg")).setDecimals(0)
        for axis, member in dims.items():
            dimension = taxonomy.getConcept(axis)
            if dimension.isTypedDimension:
                fb.setTypedDimension(dimension, member)
            else:
                fb.setExplicitDimension(dimension, taxonomy.getConcept(member))
        report.addFact(fb.buildFact())
    return report


def _html(report: InlineReport) -> str:
    return report.getInlineReport().fileContent.decode("utf-8")


def _titles(html: str) -> list[str]:
    return [
        t.strip()
        for t in re.findall(r"<h2[^>]*>\s*([^<]*)", html)
        if t.strip().startswith("Group")
    ]


def _subheadings(html: str) -> list[str]:
    return [
        t.strip() for t in re.findall(r'<h3 class="block-heading">\s*([^<]*)', html)
    ]


def _tagged(html: str) -> list[str]:
    return re.findall(r'<ix:non(?:Fraction|Numeric)[^>]*name="([^"]+)"', html)


Register = Callable[[dict[str, Any]], Taxonomy]


class TestTheBlocks:
    def test_two_hypercubes_in_one_role_are_two_cube_blocks(
        self, register_taxonomy: Register
    ) -> None:
        group = register_taxonomy(_two_cubes()).presentation[0]
        assert group.style is PresentationStyle.Table
        assert [b.kind for b in group.blocks] == [BlockKind.CUBE, BlockKind.CUBE]
        assert [[r.concept.qname.localName for r in b.rows] for b in group.blocks] == [
            ["Emissions"],
            ["Intensity", "Note"],
        ]
        assert [b.heading.concept.qname.localName for b in group.blocks] == [  # type: ignore[union-attr]
            "BranchOne",
            "BranchTwo",
        ]

    def test_line_items_a_table_and_line_items_are_three_blocks(
        self, register_taxonomy: Register
    ) -> None:
        group = register_taxonomy(_hybrid()).presentation[0]
        assert group.style is PresentationStyle.Hybrid
        assert [b.kind for b in group.blocks] == [
            BlockKind.LINE_ITEMS,
            BlockKind.CUBE,
            BlockKind.LINE_ITEMS,
        ]

    def test_line_items_before_the_table_are_its_rows_when_it_declares_them(
        self, register_taxonomy: Register
    ) -> None:
        group = register_taxonomy(_items_before_table()).presentation[0]
        assert group.style is PresentationStyle.Table
        (block,) = group.blocks
        assert block.kind is BlockKind.CUBE
        assert [r.concept.qname.localName for r in block.rows] == ["Emissions"]


class TestTwoHypercubesInOneRole:
    FACTS: ClassVar[list[tuple[str, Any, dict[str, str]]]] = [
        (qname("Emissions"), 10, {SCOPE: qname("ScopeA")}),
        (qname("Emissions"), 20, {SCOPE: qname("ScopeB")}),
        (qname("Intensity"), 5, {TARGET: "T1"}),
        (qname("Intensity"), 7, {TARGET: "T2"}),
        (qname("Note"), "note one", {TARGET: "T1"}),
    ]

    def test_each_hypercube_is_a_table_under_its_own_heading(
        self, register_taxonomy: Register
    ) -> None:
        taxonomy = register_taxonomy(_two_cubes())
        html = _html(_report(taxonomy, self.FACTS))  # strict: raises if a fact is lost
        assert _titles(html) == ["Group"]
        assert _subheadings(html) == ["Emissions by scope", "Intensity by target"]
        assert html.count('href="#section-class') == 1  # one table-of-contents entry
        assert html.count('<table class="thematic-table">') == 2
        assert sorted(_tagged(html)) == sorted(
            [qname("Emissions")] * 2 + [qname("Intensity")] * 2 + [qname("Note")]
        )
        assert problems(html) == []

    def test_each_table_follows_its_own_axis(self, register_taxonomy: Register) -> None:
        html = _html(_report(register_taxonomy(_two_cubes()), self.FACTS))
        explicit, typed = re.findall(r"<table.*?</table>", html, re.DOTALL)
        assert re.findall(r'<th class="[^"]*">\s*(Scope [AB])', explicit) == [
            "Scope A",
            "Scope B",
        ]
        headings = re.findall(r"<th[^>]*>\s*([^<]*?)\s*</th>", typed)
        assert "T1" in headings and "T2" in headings
        assert "Intensity" in headings and "Note" in headings

    def test_a_period_over_several_columns_is_right_aligned_like_the_units_beside_it(
        self, register_taxonomy: Register
    ) -> None:
        html = _html(_report(register_taxonomy(_two_cubes()), self.FACTS))
        _, typed = re.findall(r"<table.*?</table>", html, re.DOTALL)
        # Intensity is a number and Note is text, so the table is not all numeric
        (period,) = re.findall(
            r'<th colspan="[2-9]"[^>]*class="([^"]*)">\s*<span class="period"', typed
        )
        assert period == "numeric"

    def test_a_role_with_only_one_cube_keeps_its_plain_heading(
        self, register_taxonomy: Register
    ) -> None:
        taxonomy = register_taxonomy(_items_before_table())
        facts = [
            (qname("Emissions"), 10, {SCOPE: qname("ScopeA")}),
            (qname("Emissions"), 20, {SCOPE: qname("ScopeB")}),
        ]
        assert _titles(_html(_report(taxonomy, facts))) == ["Group"]


class TestHybridRole:
    FACTS: ClassVar[list[tuple[str, Any, dict[str, str]]]] = [
        (qname("Plain"), 1, {}),
        (qname("Emissions"), 10, {SCOPE: qname("ScopeA")}),
        (qname("Emissions"), 20, {SCOPE: qname("ScopeB")}),
        (qname("Other"), 2, {}),
    ]

    def test_sections_follow_the_presentation_order(
        self, register_taxonomy: Register
    ) -> None:
        html = _html(_report(register_taxonomy(_hybrid()), self.FACTS))
        assert _titles(html) == ["Group"]
        assert _subheadings(html) == [
            "Before the table",
            "The table",
            "After the table",
        ]
        assert sorted(_tagged(html)) == sorted(
            [qname("Plain"), qname("Other")] + [qname("Emissions")] * 2
        )

    def test_the_line_items_are_lists_and_the_cube_is_a_table(
        self, register_taxonomy: Register
    ) -> None:
        html = _html(_report(register_taxonomy(_hybrid()), self.FACTS))
        assert html.count('<table class="thematic-table">') == 1
        assert html.count('<dl class="fact') == 2  # one card each: Plain, Other
        assert problems(html) == []

    def test_a_dimensional_line_item_gets_one_fallback_table_not_one_per_block(
        self, register_taxonomy: Register
    ) -> None:
        facts = [*self.FACTS, (qname("Hidden"), 3, {SCOPE: qname("ScopeA")})]
        html = _html(_report(register_taxonomy(_hybrid()), facts))
        assert _subheadings(html).count("By Scope") == 1
        assert sorted(_tagged(html)).count(qname("Hidden")) == 1


def test_line_items_before_the_table_are_shown_in_it(
    register_taxonomy: Register,
) -> None:
    taxonomy = register_taxonomy(_items_before_table())
    facts = [
        (qname("Emissions"), 10, {SCOPE: qname("ScopeA")}),
        (qname("Emissions"), 20, {SCOPE: qname("ScopeB")}),
    ]
    html = _html(_report(taxonomy, facts))
    assert html.count('<table class="thematic-table">') == 1
    assert [t for t in re.findall(r"<th[^>]*>\s*([^<]*?)\s*</th>", html)].count(
        "Scope A"
    ) == 1
    assert problems(html) == []


@pytest.mark.parametrize("builder", [_two_cubes, _hybrid, _items_before_table])
def test_nothing_in_these_roles_raises_as_unsupported(
    builder: Callable[[], dict[str, Any]], register_taxonomy: Register
) -> None:
    taxonomy = register_taxonomy(builder())
    report = _report(taxonomy, [(qname("Emissions"), 1, {SCOPE: qname("ScopeA")})])
    assert "Unsupported presentation" not in _html(report)
