"""How a grid of facts becomes a table: header rows, units, periods, and who says what once."""

from __future__ import annotations

from datetime import date
from unittest.mock import MagicMock

from mireport.report.fact import Fact
from mireport.report.layout.headers import (
    assemble_table,
    build_header_rows,
    build_table_rows,
    column_flags,
    column_periods,
    drop_empty_columns,
    table_period,
)
from mireport.report.layout.model import FactGrid, TableHeadingCell, TableStyle
from mireport.report.model import FactPeriod, PeriodPart, ReportPeriod
from mireport.report.periods import DurationPeriodHolder
from mireport.taxonomy import Concept

CUR = ReportPeriod(
    "cur", DurationPeriodHolder.fromDays(date(2025, 1, 1), date(2025, 12, 31))
)
PRIOR_RP = ReportPeriod(
    "prior", DurationPeriodHolder.fromDays(date(2024, 1, 1), date(2024, 12, 31))
)
CURRENT = FactPeriod(CUR)
PRIOR = FactPeriod(PRIOR_RP)
CUR_END = FactPeriod(CUR, PeriodPart.End)
CUR_START = FactPeriod(CUR, PeriodPart.Start)
PRIOR_END = FactPeriod(PRIOR_RP, PeriodPart.End)


def _fact(*, numeric: bool = True, unit: str | None = "t", period=CURRENT) -> Fact:
    fact = MagicMock(spec=Fact)
    fact.concept = MagicMock(spec=Concept)
    fact.concept.isNumeric = numeric
    fact.unitSymbol = unit
    fact.period = period
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


class TestHeadingPeriods:
    """What a table or column says its period is, when its facts are not all in one."""

    @staticmethod
    def _grid(*periods: FactPeriod) -> list[list[Fact | None]]:
        return [[_fact(period=p) for p in periods]]

    def test_facts_in_the_same_period_share_it(self) -> None:
        assert table_period(self._grid(CUR_END, CUR_END)) == CUR_END

    def test_an_end_instant_sits_under_the_whole_period(self) -> None:
        assert table_period(self._grid(CURRENT, CUR_END)) == CURRENT

    def test_a_start_instant_is_a_different_date_and_is_not_folded_in(self) -> None:
        assert table_period(self._grid(CURRENT, CUR_START)) is None
        assert table_period(self._grid(CUR_END, CUR_START)) is None

    def test_a_table_of_opening_balances_is_headed_by_the_start(self) -> None:
        assert table_period(self._grid(CUR_START, CUR_START)) == CUR_START

    def test_different_report_periods_have_no_one_period(self) -> None:
        assert table_period(self._grid(CUR_END, PRIOR_END)) is None

    def test_the_start_of_one_year_and_the_end_of_the_year_before_are_both_kept(
        self,
    ) -> None:
        # the same OIM instant, but one is an opening balance of 2025 and the other a closing
        # balance of 2024, and each column says which
        assert CUR_START.oim == PRIOR_END.oim
        assert column_periods(self._grid(CUR_START, PRIOR_END)) == [
            CUR_START,
            PRIOR_END,
        ]

    def test_each_column_folds_its_own_facts(self) -> None:
        data = [
            [_fact(period=CURRENT), _fact(period=CUR_START)],
            [_fact(period=CUR_END), _fact(period=CUR_START)],
        ]
        assert column_periods(data) == [CURRENT, CUR_START]


class TestPeriodAxis:
    @staticmethod
    def _grid() -> FactGrid:
        cur_x, cur_y = _fact(period=CURRENT), _fact(period=CURRENT)
        pri_x, pri_y = _fact(period=PRIOR), _fact(period=PRIOR)
        return FactGrid(
            TableStyle.Other,
            [[cur_x, cur_y, pri_x, pri_y]],
            ["r"],
            None,
            ["x", "y", "x", "y"],
            period_axis=True,
        )

    def test_each_period_is_said_once_over_its_columns(self) -> None:
        table = assemble_table(self._grid())
        top = table.header_rows[0]
        assert _values(top) == [None, CURRENT, PRIOR]
        assert [c.colspan for c in top[1:]] == [2, 2]

    def test_the_labels_repeat_under_each_period(self) -> None:
        table = assemble_table(self._grid())
        labels = [r for r in table.header_rows if _values(r)[:1] == ["x"]]
        assert _values(labels[0]) == ["x", "y", "x", "y"]

    def test_the_row_heading_spans_every_header_row(self) -> None:
        table = assemble_table(self._grid())
        assert table.header_rows[0][0].rowspan == len(table.header_rows)

    def test_a_period_group_of_a_dropped_empty_column_shrinks(self) -> None:
        cur = _fact(period=CURRENT)
        pri_x = _fact(period=PRIOR)
        grid = FactGrid(
            TableStyle.Other,
            [[cur, None, pri_x, None]],  # y is empty in both periods
            ["r"],
            None,
            ["x", "y", "x", "y"],
            period_axis=True,
        )
        table = assemble_table(grid)
        assert [c.colspan for c in table.header_rows[0][1:]] == [1, 1]
        assert table.column_count == 2

    def test_a_table_wide_unit_still_comes_once_under_the_periods(self) -> None:
        table = assemble_table(self._grid())  # every fact is in "t"
        assert _values(table.header_rows[1]) == ["t"]

    def test_without_a_period_axis_one_period_is_still_said_once_for_the_table(
        self,
    ) -> None:
        grid = FactGrid(TableStyle.Other, [[_fact(), _fact()]], ["r"], None, ["x", "y"])
        top = assemble_table(grid).header_rows[0]
        assert _values(top) == [None, CURRENT] and top[1].colspan == 2


def test_a_unit_is_said_over_the_columns_that_have_it_not_the_whole_table() -> None:
    # one column in m², beside two yes/no columns that have no unit
    area, yes, no = (
        _fact(unit="m²"),
        _fact(numeric=False, unit=None),
        _fact(numeric=False, unit=None),
    )
    grid = FactGrid(
        TableStyle.Other, [[area, yes, no]], ["1"], "Site", ["Area", "Inside", "Near"]
    )
    table = assemble_table(grid)
    unit_row = table.header_rows[-1]
    assert _values(unit_row) == ["m²", None, None]
    assert all(cell.colspan == 1 for cell in unit_row)
