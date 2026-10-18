"""A presentation group is a sequence of blocks (line items, or one hypercube's table), and its
style follows from them.

Which concepts are a table's rows comes from the definition linkbase (the role's hypercube
declarations); the presentation supplies only their order. These tests run over every built-in
taxonomy, so they also pin that moving from the old rule (a row is a table row if it sits under the
hypercube in the tree) to the declarations changed no group's style.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator, Sequence

import pytest

from mireport.entrypoints import describeEntryPointSet
from mireport.taxonomy import (
    BlockKind,
    PresentationGroup,
    PresentationStyle,
    Relationship,
    getTaxonomy,
    listTaxonomies,
    loadBuiltInTaxonomyJSON,
)


def _positionalStyle(rels: Iterable[Relationship]) -> PresentationStyle:
    """The rule groups were classified by before blocks: positional, from the presentation
    alone. A reportable concept after a hypercube row, at or below its depth and before a
    shallower row, counted as in the table."""
    rels = list(rels)
    hasHypercubes = any(rel.concept.isHypercube for rel in rels)
    hasReportable = any(rel.concept.isReportable for rel in rels)
    if not hasReportable:
        return PresentationStyle.Empty
    if not hasHypercubes:
        return PresentationStyle.List

    listStyle = tableStyle = False
    inHypercube = [False]
    hypercubeDepth = [0]
    for rel in rels:
        if inHypercube[-1] and (0 == rel.depth or rel.depth < hypercubeDepth[-1]):
            hypercubeDepth.pop()
            inHypercube.pop()
        if rel.concept.isHypercube:
            inHypercube.append(True)
            hypercubeDepth.append(rel.depth)
        if rel.concept.isReportable:
            if inHypercube[-1] and rel.depth >= hypercubeDepth[-1]:
                tableStyle = True
            else:
                listStyle = True
    match (tableStyle, listStyle):
        case (True, True):
            return PresentationStyle.Hybrid
        case (True, False):
            return PresentationStyle.Table
        case (False, True):
            return PresentationStyle.List
        case _:
            return PresentationStyle.Empty


def _builtInGroups() -> list[pytest.ParameterSet]:
    if not listTaxonomies():
        loadBuiltInTaxonomyJSON()
    return [
        pytest.param(
            group,
            id=f"{describeEntryPointSet(entryPoints)}::{group.roleUri.rsplit('/', 1)[-1]}",
        )
        for entryPoints in sorted(listTaxonomies(), key=lambda e: sorted(e))
        for group in getTaxonomy(entryPoints).presentation
    ]


GROUPS = _builtInGroups()


def _is_subsequence(part: Sequence[object], whole: Sequence[object]) -> bool:
    remaining: Iterator[object] = iter(whole)
    return all(any(item is other for other in remaining) for item in part)


@pytest.mark.parametrize("group", GROUPS)
def test_the_style_is_the_one_the_positional_rule_gave(
    group: PresentationGroup,
) -> None:
    assert group.style is _positionalStyle(group.relationships)


@pytest.mark.parametrize("group", GROUPS)
def test_the_rows_of_the_blocks_are_the_reportable_relationships_in_order(
    group: PresentationGroup,
) -> None:
    reportable = [r for r in group.relationships if r.concept.isReportable]
    assert [r for block in group.blocks for r in block.rows] == reportable


@pytest.mark.parametrize("group", GROUPS)
def test_every_block_keeps_the_presentation_order_and_only_its_own_rows(
    group: PresentationGroup,
) -> None:
    for block in group.blocks:
        assert _is_subsequence(block.relationships, group.relationships)
        assert all(row in block.relationships for row in block.rows)
    # a relationship is in no more than one block
    placed = [id(r) for block in group.blocks for r in block.relationships]
    assert len(placed) == len(set(placed))


@pytest.mark.parametrize("group", GROUPS)
def test_a_table_group_is_all_cube_blocks_and_a_list_group_is_one_run_of_line_items(
    group: PresentationGroup,
) -> None:
    kinds = [block.kind for block in group.blocks]
    match group.style:
        case PresentationStyle.Table:
            assert kinds and set(kinds) == {BlockKind.CUBE}
            # each cube is one table: a role of one cube is one block, of two cubes two
            cubes = [block.hypercube for block in group.blocks]
            assert len(set(cubes)) == len(cubes)
            assert all(c is not None and c.roleUri == group.roleUri for c in cubes)
        case PresentationStyle.List:
            assert kinds == [BlockKind.LINE_ITEMS]
        case PresentationStyle.Empty:
            assert BlockKind.LINE_ITEMS not in kinds
            assert all(not block.rows for block in group.blocks)
        case PresentationStyle.Hybrid:
            assert {BlockKind.LINE_ITEMS, BlockKind.CUBE} <= set(kinds)


@pytest.mark.parametrize("group", GROUPS)
def test_a_cube_block_names_the_hypercube_it_is_the_table_of(
    group: PresentationGroup,
) -> None:
    for block in group.blocks:
        if block.kind is not BlockKind.CUBE:
            continue
        assert block.hypercube is not None
        assert block.hypercube.hypercube in {r.concept for r in block.relationships}
        assert {r.concept for r in block.rows} <= set(block.hypercube.primaryItems)


def test_declarations_for_a_role_are_the_modelled_positive_cubes_of_that_role() -> None:
    if not listTaxonomies():
        loadBuiltInTaxonomyJSON()
    for entryPoints in listTaxonomies():
        taxonomy = getTaxonomy(entryPoints)
        for group in taxonomy.presentation:
            declarations = taxonomy.getDeclarationsForRole(group.roleUri)
            assert all(d.roleUri == group.roleUri for d in declarations)
            cubeRows = {r.concept for r in group.relationships if r.concept.isHypercube}
            if group.style is PresentationStyle.List:
                assert declarations == () or not (
                    {d.hypercube for d in declarations} & cubeRows
                )
            if group.style is PresentationStyle.Table:
                assert declarations


def test_an_unknown_role_has_no_declarations() -> None:
    if not listTaxonomies():
        loadBuiltInTaxonomyJSON()
    taxonomy = getTaxonomy(next(iter(listTaxonomies())))
    assert taxonomy.getDeclarationsForRole("urn:no-such-role") == ()
