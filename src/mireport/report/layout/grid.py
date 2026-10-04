"""Putting facts into a grid.

Every table is the same operation: say which row and which column each fact belongs to, put it
there, and drop the rows nothing landed in. What differs is only what a row or a column *is*: a
concept, a member of an explicit dimension, the text of a typed dimension, or a combination of
dimension values. ``GridBuilder`` has one method per kind, each a few lines over ``_place`` and
``_matrix``.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from collections.abc import Callable, Hashable, Iterable, Sequence
from typing import TypeVar

from mireport.report.fact import Fact, numeric_string_key
from mireport.report.layout.model import FactGrid, TableStyle
from mireport.report.model import FactPeriod, ReportPeriod
from mireport.taxonomy import Concept, Relationship

L = logging.getLogger(__name__)

R = TypeVar("R", bound=Hashable)  # what identifies a row
C = TypeVar("C", bound=Hashable)  # what identifies a column
DimensionKey = tuple["Concept | str", ...]


Cells = dict[tuple[R, C, FactPeriod], Fact]


def _place(
    facts: Iterable[Fact],
    row_of: Callable[[Fact], R | None],
    column_of: Callable[[Fact], C | None],
) -> Cells[R, C]:
    """Each fact in the cell its row, column and period (which part of it, too). A fact that belongs to no row or
    column is left out; when two belong to one cell the first stays and the other is reported."""
    cells: Cells[R, C] = {}
    for fact in facts:
        row, column = row_of(fact), column_of(fact)
        if row is None or column is None:
            continue
        if (held := cells.get((row, column, fact.period))) is not None:
            L.warning(
                f"Several facts for {fact.concept.qname} belong in the same table cell; "
                f"showing the first.\n{held=}\n{fact=}"
            )
            continue
        cells[row, column, fact.period] = fact
    return cells


def _swap(cells: Cells[R, C]) -> Cells[C, R]:
    """The same cells with rows and columns exchanged."""
    return {
        (column, row, period): fact for (row, column, period), fact in cells.items()
    }


def _matrix(
    rows: Sequence[R], columns: Sequence[C], cells: dict[tuple[R, C], Fact]
) -> tuple[list[R], list[list[Fact | None]]]:
    """The rows that have any fact, and their cells across the columns."""
    kept = [row for row in rows if any((row, column) in cells for column in columns)]
    return kept, [[cells.get((row, column)) for column in columns] for row in kept]


class GridBuilder:
    """Builds FactGrids. ``facts_for`` says what facts a concept has; ``label`` how to show a
    concept to a reader."""

    def __init__(
        self,
        facts_for: Callable[[Concept], Sequence[Fact]],
        label: Callable[[Concept], str],
        period_rank: Callable[[ReportPeriod], int] = lambda period: 0,
    ) -> None:
        self._facts_for = facts_for
        self._label = label
        self._period_rank = period_rank

    def _arrange(
        self, rows: Sequence[R], columns: Sequence[C], cells: Cells[R, C]
    ) -> tuple[list[R], list[list[Fact | None]], list[C], bool]:
        """The grid for these cells: the rows that have a fact, their data, the columns (each
        repeated once per period when a column held facts from more than one), and whether they were.

        A table whose every column is in one period (a table of current facts, or one whose columns
        are baseline and target years) is laid out as it always was."""
        periods_of: dict[C, set[FactPeriod]] = defaultdict(set)
        for _, column, period in cells:
            periods_of[column].add(period)
        if not any(len(periods) > 1 for periods in periods_of.values()):
            kept, data = _matrix(
                rows, columns, {(r, c): f for (r, c, _), f in cells.items()}
            )
            return kept, data, list(columns), False

        periods = sorted(
            {period for _, _, period in cells},
            key=lambda p: (
                self._period_rank(p.reportPeriod),
                p.reportPeriod.duration.start,
                p.reportPeriod.duration.end,
                p.partRank,
            ),
        )
        spread = [(column, period) for period in periods for column in columns]
        kept, data = _matrix(
            rows, spread, {(r, (c, p)): f for (r, c, p), f in cells.items()}
        )
        return kept, data, [column for column, _ in spread], True

    def explicit_dimension(
        self,
        reportable: list[Concept],
        dimension: Concept,
        domain: list[Concept],
        default_member: Concept | None,
    ) -> FactGrid:
        """Concepts against the members of one explicit dimension. A fact with no member is at the
        dimension's default. The narrower of the two is the columns."""
        facts = [f for concept in reportable for f in self._facts_for(concept)]

        def member_of(fact: Fact) -> Concept | None:
            return fact.explicit_dimensions.get(dimension, default_member)

        cells = _place(facts, lambda f: f.concept, member_of)
        if len(domain) <= len(reportable):
            rows, data, columns, spread = self._arrange(reportable, domain, cells)
            return FactGrid(
                TableStyle.SingleExplicitDimensionColumn,
                data,
                rows,
                None,
                columns,
                spread,
            )
        rows, data, columns, spread = self._arrange(domain, reportable, _swap(cells))
        return FactGrid(
            TableStyle.SingleExplicitDimensionRow,
            data,
            rows,
            dimension,
            columns,
            spread,
        )

    def typed_dimension(
        self, reportable: list[Concept], dimension: Concept
    ) -> FactGrid:
        """The text of one typed dimension (down the side, in numeric order) against concepts."""
        facts = [f for concept in reportable for f in self._facts_for(concept)]
        texts = sorted(
            {t for f in facts if (t := f.typed_dimensions.get(dimension)) is not None},
            key=numeric_string_key,
        )
        cells = _place(
            facts, lambda f: f.typed_dimensions.get(dimension), lambda f: f.concept
        )
        rows, data, columns, spread = self._arrange(texts, reportable, cells)
        return FactGrid(
            TableStyle.SingleTypedDimensionColumn,
            data,
            rows,
            dimension,
            columns,
            spread,
        )

    def dimension_set(
        self,
        signature: tuple[Concept, ...],
        items: list[tuple[Relationship, Fact]],
    ) -> FactGrid:
        """Facts that all carry the same set of dimensions: concepts against the combinations of
        dimension values they were reported for. The narrower of the two is the columns."""
        relationships = list(dict.fromkeys(rel for rel, _ in items))
        owner = {id(fact): rel for rel, fact in items}

        def key_of(fact: Fact) -> DimensionKey:
            values: dict[Concept, Concept | str] = dict(fact.explicit_dimensions)
            values.update(fact.typed_dimensions)
            return tuple(values[dimension] for dimension in signature)

        cells = _place(
            (fact for _, fact in items),
            lambda f: key_of(f),
            lambda f: owner[id(f)],
        )
        keys = sorted({row for row, _, _ in cells}, key=self._key_order)
        if len(keys) <= len(relationships):
            _, data, columns, spread = self._arrange(relationships, keys, _swap(cells))
            return FactGrid(
                TableStyle.Other,
                data,
                [rel.concept for rel in relationships],
                None,
                [self._key_label(key) for key in columns],
                spread,
            )
        _, data, by_relationship, spread_by_period = self._arrange(
            keys, relationships, cells
        )
        return FactGrid(
            TableStyle.Other,
            data,
            [self._key_label(key) for key in keys],
            " / ".join(self._label(d) for d in signature),
            [rel.concept for rel in by_relationship],
            spread_by_period,
        )

    def _key_order(self, key: DimensionKey) -> tuple:
        """Typed text in numeric-aware order, explicit members by their label."""
        return tuple(
            numeric_string_key(v) if isinstance(v, str) else (1, self._label(v))
            for v in key
        )

    def _key_label(self, key: DimensionKey) -> str:
        return " / ".join(v if isinstance(v, str) else self._label(v) for v in key)
