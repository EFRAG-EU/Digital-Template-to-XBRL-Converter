"""From a grid of facts to a finished table: which units and periods are said once, which per column.

All pure functions over a grid's data (a list of rows of ``Fact | None``). A unit or a period that
every fact in the table shares is written once, spanning the table; one that is the same for every
fact in a *column* is written under that column; one that varies within a column is written with
each fact.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from itertools import compress

from mireport.report.fact import Fact
from mireport.report.layout.model import (
    FactGrid,
    HeadingValue,
    Table,
    TableCell,
    TableHeadingCell,
    TableRow,
)
from mireport.report.periods import _Period
from mireport.taxonomy import Concept

Data = list[list[Fact | None]]


def table_unit(data: Data) -> str | None:
    """The unit symbol every numeric fact shares, if there is exactly one."""
    units = {
        fact.unitSymbol
        for row in data
        for fact in row
        if fact and fact.concept.isNumeric
    }
    if len(units) == 1 and (unit := next(iter(units))):
        return unit
    return None


def table_period(data: Data) -> _Period | None:
    """The period every fact shares, if there is exactly one."""
    periods = {fact.period.duration for row in data for fact in row if fact}
    return next(iter(periods)) if len(periods) == 1 else None


def column_units(data: Data) -> list[str | None]:
    """Each column's unit symbol (None where it has none or several), or [] if no column has one."""
    by_column: dict[int, set[str]] = defaultdict(set)
    for row in data:
        for col, fact in enumerate(row):
            if fact and fact.concept.isNumeric:
                by_column[col].add(fact.unitSymbol)
    result: list[str | None] = []
    for col in range(len(data[0]) if data else 0):
        match by_column[col]:
            case units if len(units) == 1 and (unit := next(iter(units))):
                result.append(unit)
            case _:
                result.append(None)
    return result if any(unit is not None for unit in result) else []


def column_periods(data: Data) -> list[_Period | None]:
    """Each column's period (None where it has none or several), or [] if no column has one."""
    by_column: dict[int, set[_Period]] = defaultdict(set)
    for row in data:
        for col, fact in enumerate(row):
            if fact:
                by_column[col].add(fact.period.duration)
    result: list[_Period | None] = []
    for col in range(len(data[0]) if data else 0):
        match by_column[col]:
            case periods if len(periods) == 1:
                result.append(next(iter(periods)))
            case _:
                result.append(None)
    return result if any(period is not None for period in result) else []


def column_flags(data: Data) -> tuple[list[bool], list[bool], bool]:
    """Per column: is it empty, is it all numeric; and is the whole table numeric."""
    columns = range(len(data[0]) if data else 0)
    empty = [all(row[c] is None for row in data) for c in columns]
    numeric = [
        all(f.concept.isNumeric for row in data if (f := row[c]) is not None)
        for c in columns
    ]
    return empty, numeric, all(numeric)


def drop_empty_columns(
    grid: FactGrid, empty: list[bool], numeric: list[bool]
) -> tuple[FactGrid, list[bool]]:
    keep = [not e for e in empty]
    return (
        FactGrid(
            style=grid.style,
            data=[list(compress(row, keep)) for row in grid.data],
            row_labels=grid.row_labels,
            row_heading_label=grid.row_heading_label,
            col_labels=list(compress(grid.col_labels, keep)),
        ),
        list(compress(numeric, keep)),
    )


def build_header_rows(
    row_heading_label: HeadingValue,
    col_labels: Sequence[Concept | str],
    col_numeric: list[bool],
    all_numeric: bool,
    unit: str | None,
    period: _Period | None,
    units: list[str | None],
    periods: list[_Period | None],
) -> list[list[TableHeadingCell]]:
    """The header rows, top to bottom: a table-wide period, a table-wide unit, the column labels,
    then a period and a unit per column where those were not said once for the table."""
    width = max(1, len(col_labels))
    rows: list[list[TableHeadingCell]] = []
    if period:
        rows.append([TableHeadingCell(period, colspan=width, rowspan=1)])
    if unit:
        rows.append([TableHeadingCell(unit, colspan=width, rowspan=1, numeric=True)])

    def per_column(values: Sequence[HeadingValue]) -> list[TableHeadingCell]:
        return [
            TableHeadingCell(
                v, colspan=1, rowspan=1, numeric=all_numeric or col_numeric[n]
            )
            for n, v in enumerate(values)
        ]

    rows.append(per_column(col_labels))
    if not period and periods:
        rows.append(per_column(periods))
    if not unit and units:
        rows.append(per_column(units))
    if rows:
        rows[0].insert(
            0, TableHeadingCell(row_heading_label, colspan=1, rowspan=len(rows))
        )
    return [row for row in rows if not all(c.value is None for c in row)]


def build_table_rows(
    grid: FactGrid, unit: str | None, units: list[str | None]
) -> list[TableRow]:
    """The body rows. A cell does not repeat its unit when the table or its column already says it."""
    return [
        TableRow(
            heading=TableHeadingCell(label),
            cells=[
                TableCell(
                    fact=fact,
                    suppress_unit=(
                        unit is not None or (j < len(units) and units[j] is not None)
                    ),
                )
                for j, fact in enumerate(row)
            ],
        )
        for label, row in zip(grid.row_labels, grid.data)
    ]


def assemble_table(grid: FactGrid) -> Table:
    """Header rows, units and periods for a grid of facts, and the table that results."""
    empty, numeric, all_numeric = column_flags(grid.data)
    if True in empty:
        grid, numeric = drop_empty_columns(grid, empty, numeric)

    unit, units = table_unit(grid.data), column_units(grid.data)
    header_rows = build_header_rows(
        grid.row_heading_label,
        grid.col_labels,
        numeric,
        all_numeric,
        unit,
        table_period(grid.data),
        units,
        column_periods(grid.data),
    )
    return Table(
        style=grid.style,
        numeric=all_numeric,
        header_rows=header_rows,
        rows=build_table_rows(grid, unit, units),
    )
