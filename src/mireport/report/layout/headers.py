"""From a grid of facts to a finished table: which units and periods are said once, which per column.

All pure functions over a grid's data (a list of rows of ``Fact | None``). A unit or a period that
every fact in the table shares is written once, spanning the table; one that is the same for every
fact in a *column* is written under that column; one that varies within a column is written with
each fact.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Sequence
from itertools import compress, groupby

from mireport.report.fact import Fact
from mireport.report.layout.model import (
    FactGrid,
    HeadingValue,
    Table,
    TableCell,
    TableHeadingCell,
    TableRow,
)
from mireport.report.model import FactPeriod, PeriodPart
from mireport.taxonomy import Concept, Relationship

Data = list[list[Fact | None]]


def table_unit(data: Data) -> str | None:
    """The unit symbol every fact shares, if there is exactly one. A table with a fact that has no
    unit (a text or yes/no column) has none for the whole table: the unit belongs to the columns
    that have it, not over the ones that do not."""
    facts = [fact for row in data for fact in row if fact]
    if not all(fact.concept.isNumeric for fact in facts):
        return None
    units = {fact.unitSymbol for fact in facts}
    if len(units) == 1 and (unit := next(iter(units))):
        return unit
    return None


def _heading_period(periods: Iterable[FactPeriod]) -> FactPeriod | None:
    """The one period that can stand for all of ``periods``, if there is one: the period they all
    are, or the whole of the report period when they are that and its end (a balance at the
    end of a year sits comfortably under that year). The start of a period is a different date
    from any of its other parts, so it is never folded into another."""
    found = set(periods)
    if len(found) == 1:
        return next(iter(found))
    reports = {fp.reportPeriod for fp in found}
    if len(reports) == 1 and not any(fp.isStart for fp in found):
        return FactPeriod(next(iter(reports)), PeriodPart.Whole)
    return None


def table_period(data: Data) -> FactPeriod | None:
    """The period every fact shares, if there is exactly one."""
    return _heading_period(fact.period for row in data for fact in row if fact)


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


def column_periods(data: Data) -> list[FactPeriod | None]:
    """Each column's period (None where it has none or several), or [] if no column has one."""
    by_column: dict[int, set[FactPeriod]] = defaultdict(set)
    for row in data:
        for col, fact in enumerate(row):
            if fact:
                by_column[col].add(fact.period)
    result = [
        _heading_period(by_column[col]) for col in range(len(data[0]) if data else 0)
    ]
    return result if any(period is not None for period in result) else []


def period_groups(
    periods: list[FactPeriod | None],
) -> list[tuple[FactPeriod | None, int]]:
    """Runs of neighbouring columns in the same period, as (period, how many columns)."""
    return [(period, len(list(run))) for period, run in groupby(periods)]


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
            period_axis=grid.period_axis,
            col_periods=list(compress(grid.col_periods, keep))
            if grid.col_periods
            else (),
        ),
        list(compress(numeric, keep)),
    )


def build_header_rows(
    row_heading_label: HeadingValue,
    col_labels: Sequence[Concept | str | None],
    col_numeric: list[bool],
    all_numeric: bool,
    unit: str | None,
    period: FactPeriod | None,
    units: list[str | None],
    periods: list[FactPeriod | None],
    groups: Sequence[tuple[FactPeriod | None, int]] = (),
) -> list[list[TableHeadingCell]]:
    """The header rows, top to bottom: the periods (one spanning the table, or one spanning each
    group of columns), a table-wide unit, the column labels, then a period and a unit per column
    where those were not said once for the table."""
    width = max(1, len(col_labels))
    rows: list[list[TableHeadingCell]] = []
    if groups:
        rows.append(
            [
                TableHeadingCell(p, colspan=span, rowspan=1, numeric=True)
                for p, span in groups
            ]
        )
    elif period:
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

    if any(label is not None for label in col_labels):
        rows.append(per_column(col_labels))
    if not period and not groups and periods:
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
                    negated=isinstance(label, Relationship) and label.isNegated,
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
    period: FactPeriod | None
    if grid.col_periods:
        # The grid says which period each column is for: one for the table if it is one throughout.
        periods = list(grid.col_periods)
        period = periods[0] if len(set(periods)) == 1 else None
        groups: Sequence[tuple[FactPeriod | None, int]] = ()
    else:
        periods = column_periods(grid.data)
        period = None if grid.period_axis else table_period(grid.data)
        # Columns repeated per period: say each period once over its columns.
        groups = period_groups(periods) if grid.period_axis else ()
    header_rows = build_header_rows(
        grid.row_heading_label,
        grid.col_labels,
        numeric,
        all_numeric,
        unit,
        period,
        units,
        periods,
        groups,
    )
    return Table(
        style=grid.style,
        numeric=all_numeric,
        header_rows=header_rows,
        rows=build_table_rows(grid, unit, units),
    )
