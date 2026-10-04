"""How a grid of facts becomes a table: header rows, units, periods, and who says what once."""

from __future__ import annotations

from datetime import date
from types import SimpleNamespace
from unittest.mock import MagicMock

from mireport.report.fact import Fact
from mireport.report.layout.headers import (
    assemble_table,
    build_header_rows,
    build_table_rows,
    column_flags,
    drop_empty_columns,
)
from mireport.report.layout.model import FactGrid, TableHeadingCell, TableStyle
from mireport.report.periods import DurationPeriodHolder
from mireport.taxonomy import Concept

CURRENT = DurationPeriodHolder(date(2025, 1, 1), date(2025, 12, 31))
PRIOR = DurationPeriodHolder(date(2024, 1, 1), date(2024, 12, 31))


def _fact(*, numeric: bool = True, unit: str | None = "t", period=CURRENT) -> Fact:
    fact = MagicMock(spec=Fact)
    fact.concept = MagicMock(spec=Concept)
    fact.concept.isNumeric = numeric
    fact.unitSymbol = unit
    fact.period = SimpleNamespace(duration=period)
    return fact


def _values(row: list[TableHeadingCell]) -> list:
    return [cell.value for cell in row]


class TestColumnFlags:
    def test_empty_and_numeric_columns(self) -> None:
        a, b = _fact(), _fact(numeric=False)
        empty, numeric, all_numeric = column_flags([[a, None, b], [a, None, None]])
        assert empty == [False, True, False]
        assert numeric == [True, True, False]  # an empty column counts as numeric
        assert all_numeric is False

    def test_a_table_of_numbers_is_numeric(self) -> None:
        assert column_flags([[_fact(), _fact()]])[2] is True

    def test_no_data_is_numeric_and_has_no_columns(self) -> None:
        assert column_flags([]) == ([], [], True)


def test_dropping_empty_columns_keeps_everything_in_step() -> None:
    a, b = _fact(), _fact()
    grid = FactGrid(TableStyle.Other, [[a, None, b]], ["row"], None, ["x", "y", "z"])
    dropped, numeric = drop_empty_columns(
        grid, [False, True, False], [True, True, False]
    )
    assert dropped.data == [[a, b]]
    assert list(dropped.col_labels) == ["x", "z"]
    assert numeric == [True, False]
    assert list(dropped.row_labels) == ["row"]


class TestHeaderRows:
    @staticmethod
    def _rows(*, unit=None, period=None, units=(), periods=(), labels=("x", "y"), **kw):
        return build_header_rows(
            kw.get("heading", "rows"),
            list(labels),
            [True] * len(labels),
            kw.get("all_numeric", True),
            unit,
            period,
            list(units),
            list(periods),
        )

    def test_just_the_column_labels(self) -> None:
        rows = self._rows()
        assert len(rows) == 1
        assert _values(rows[0]) == ["rows", "x", "y"]
        assert rows[0][0].rowspan == 1

    def test_a_table_wide_period_and_unit_come_first_and_span_the_columns(self) -> None:
        rows = self._rows(unit="t", period=CURRENT)
        assert [_values(r) for r in rows] == [
            ["rows", CURRENT],
            ["t"],
            ["x", "y"],
        ]
        assert rows[0][1].colspan == 2 and rows[1][0].colspan == 2
        assert rows[0][0].rowspan == 3  # the row heading spans every header row

    def test_per_column_periods_then_units_follow_the_labels(self) -> None:
        rows = self._rows(units=["t", "kg"], periods=[CURRENT, PRIOR])
        assert [_values(r) for r in rows] == [
            ["rows", "x", "y"],
            [CURRENT, PRIOR],
            ["t", "kg"],
        ]

    def test_a_table_wide_period_makes_per_column_periods_redundant(self) -> None:
        rows = self._rows(period=CURRENT, periods=[CURRENT, CURRENT])
        assert [_values(r) for r in rows] == [["rows", CURRENT], ["x", "y"]]

    def test_a_table_wide_unit_makes_per_column_units_redundant(self) -> None:
        rows = self._rows(unit="t", units=["t", "t"])
        assert [_values(r) for r in rows] == [["rows", "t"], ["x", "y"]]

    def test_numeric_headings_follow_the_columns_unless_the_table_is_all_numeric(
        self,
    ) -> None:
        rows = build_header_rows(
            "rows", ["x", "y"], [True, False], False, None, None, [], []
        )
        assert [cell.numeric for cell in rows[0][1:]] == [True, False]
        rows = build_header_rows(
            "rows", ["x", "y"], [True, False], True, None, None, [], []
        )
        assert [cell.numeric for cell in rows[0][1:]] == [True, True]

    def test_a_table_wide_unit_is_numeric(self) -> None:
        rows = self._rows(unit="t")
        assert rows[0][0].value == "rows" and rows[1][0].numeric is True


class TestTableRows:
    @staticmethod
    def _grid(a, b) -> FactGrid:
        return FactGrid(TableStyle.Other, [[a, b]], ["r"], None, ["x", "y"])

    def test_a_cell_keeps_its_unit_when_nothing_else_says_it(self) -> None:
        a, b = _fact(), _fact()
        (row,) = build_table_rows(self._grid(a, b), None, [])
        assert [c.suppress_unit for c in row.cells] == [False, False]
        assert row.heading.value == "r"

    def test_a_table_wide_unit_is_not_repeated(self) -> None:
        a, b = _fact(), _fact()
        (row,) = build_table_rows(self._grid(a, b), "t", [])
        assert [c.suppress_unit for c in row.cells] == [True, True]

    def test_a_column_unit_is_not_repeated_in_that_column_only(self) -> None:
        a, b = _fact(), _fact()
        (row,) = build_table_rows(self._grid(a, b), None, ["t", None])
        assert [c.suppress_unit for c in row.cells] == [True, False]

    def test_a_gap_stays_a_gap(self) -> None:
        (row,) = build_table_rows(self._grid(None, _fact()), None, [])
        assert row.cells[0].fact is None


class TestAssembleTable:
    def test_one_unit_and_one_period_are_said_once(self) -> None:
        grid = FactGrid(
            TableStyle.Other,
            [[_fact(), _fact()]],
            ["r"],
            None,
            ["x", "y"],
        )
        table = assemble_table(grid)
        assert [_values(r) for r in table.header_rows] == [
            [None, CURRENT],
            ["t"],
            ["x", "y"],
        ]
        assert table.numeric is True
        assert table.column_count == 2

    def test_periods_that_differ_by_column_are_said_under_each_column(self) -> None:
        grid = FactGrid(
            TableStyle.Other,
            [[_fact(period=CURRENT), _fact(period=PRIOR)]],
            ["r"],
            None,
            ["x", "y"],
        )
        table = assemble_table(grid)
        # the unit is the same everywhere, so it is said once; the periods differ, so they are
        # said under their columns, after the labels
        assert [_values(r) for r in table.header_rows] == [
            [None, "t"],
            ["x", "y"],
            [CURRENT, PRIOR],
        ]

    def test_empty_columns_are_dropped(self) -> None:
        grid = FactGrid(
            TableStyle.Other, [[_fact(), None]], ["r"], None, ["x", "empty"]
        )
        table = assemble_table(grid)
        assert table.column_count == 1
        assert "empty" not in [v for r in table.header_rows for v in _values(r)]

    def test_text_in_a_column_makes_the_table_not_numeric(self) -> None:
        grid = FactGrid(
            TableStyle.Other,
            [[_fact(), _fact(numeric=False, unit=None)]],
            ["r"],
            None,
            ["x", "y"],
        )
        assert assemble_table(grid).numeric is False
