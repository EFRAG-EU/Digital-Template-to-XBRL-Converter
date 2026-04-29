from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass
from enum import Enum, auto
from itertools import compress, count
from typing import TYPE_CHECKING, NamedTuple, Optional, cast

from mireport.exceptions import InlineReportException
from mireport.report.fact import Fact, numeric_string_key, tidyTdValue
from mireport.report.periods import DurationPeriodHolder, InstantPeriodHolder, _Period
from mireport.taxonomy import (
    Concept,
    PresentationGroup,
    PresentationStyle,
    Relationship,
    Taxonomy,
)

if TYPE_CHECKING:
    from mireport.report.inlinereport import InlineReport

L = logging.getLogger(__name__)

_TableHeadingValue = Concept | Relationship | _Period | str | None


class TableHeadingCell(NamedTuple):
    value: _TableHeadingValue
    colspan: int = 0
    rowspan: int = 0
    numeric: bool = False

    @property
    def isDuration(self) -> bool:
        return isinstance(self.value, DurationPeriodHolder)

    @property
    def isInstant(self) -> bool:
        return isinstance(self.value, InstantPeriodHolder)

    @property
    def isPeriod(self) -> bool:
        return self.isDuration or self.isInstant

    @property
    def isConcept(self) -> bool:
        return isinstance(self.value, Concept)

    @property
    def isRelationship(self) -> bool:
        return isinstance(self.value, Relationship)


class TableStyle(Enum):
    SingleTypedDimensionColumn = auto()
    SingleExplicitDimensionColumn = auto()
    SingleExplicitDimensionRow = auto()
    NoTaxonomyDefinedDimensions = auto()
    Other = auto()


@dataclass(slots=True, frozen=True)
class TableCell:
    fact: Fact | None
    suppress_unit: bool


@dataclass(slots=True, frozen=True)
class TableRow:
    heading: TableHeadingCell
    cells: list[TableCell]


@dataclass(slots=True, frozen=True)
class Table:
    style: TableStyle
    numeric: bool
    header_rows: list[list[TableHeadingCell]]
    rows: list[TableRow]

    @property
    def column_count(self) -> int:
        return len(self.rows[0].cells) if self.rows else 0


@dataclass(frozen=True, slots=True)
class _DataMatrix:
    style: TableStyle
    data: list[list[Fact | None]]
    row_labels: list[Concept | str]
    row_heading_label: Concept | None
    col_labels: list[Concept]


@dataclass(slots=True, frozen=True, eq=True)
class ReportSection:
    relationshipToFact: dict[Relationship, list[Fact]]
    presentation: PresentationGroup

    def getLabel(self, language: str) -> str:
        return self.presentation.getLabel(language)

    @property
    def style(self) -> PresentationStyle:
        return self.presentation.style

    @property
    def hasFacts(self) -> bool:
        if self.presentation.style == PresentationStyle.Empty:
            return False
        return any(factList for factList in self.relationshipToFact.values())

    @property
    def tabular(self) -> bool:
        return False


@dataclass(slots=True, frozen=True, eq=True)
class TabularReportSection(ReportSection):
    table: Table

    @property
    def tabular(self) -> bool:
        return True

    @property
    def hasFacts(self) -> bool:
        return any(
            cell.fact is not None
            for row in self.table.rows
            for cell in row.cells
        )


# ── Module-level pure helpers ─────────────────────────────────────────────────


def _table_unit(data: list[list[Fact | None]]) -> str | None:
    units: set[str] = set()
    for row in data:
        for fact in row:
            if fact is not None and fact.concept.isNumeric:
                units.add(fact.unitSymbol)
    if len(units) == 1:
        unit = next(iter(units))
        if unit:
            return unit
    return None


def _table_period(data: list[list[Fact | None]]) -> _Period | None:
    periods: set[_Period] = set()
    for row in data:
        for fact in row:
            if fact is not None:
                periods.add(fact.period)
    return next(iter(periods)) if len(periods) == 1 else None


def _column_units(data: list[list[Fact | None]]) -> list[str | None]:
    col_units_map: dict[int, set[str]] = defaultdict(set)
    num_cols = max((len(row) for row in data), default=0)
    for row in data:
        for col, fact in enumerate(row):
            if fact is not None and fact.concept.isNumeric:
                col_units_map[col].add(fact.unitSymbol)
    result: list[str | None] = []
    for c in range(num_cols):
        units = col_units_map[c]
        if len(units) == 1:
            unit = next(iter(units))
            if unit:
                result.append(unit)
                continue
        result.append(None)
    if all(u is None for u in result):
        return []
    return result


def _column_periods(data: list[list[Fact | None]]) -> list[_Period | None]:
    col_periods_map: dict[int, set[_Period]] = defaultdict(set)
    num_cols = max((len(row) for row in data), default=0)
    for row in data:
        for col, fact in enumerate(row):
            if fact is not None:
                col_periods_map[col].add(fact.period)
    result: list[_Period | None] = []
    for c in range(num_cols):
        periods = col_periods_map[c]
        result.append(next(iter(periods)) if len(periods) == 1 else None)
    if all(p is None for p in result):
        return []
    return result


def _column_flags(
    data: list[list[Fact | None]],
) -> tuple[list[bool], list[bool], bool]:
    num_cols = len(data[0]) if data else 0
    col_empty = [all(row[c] is None for row in data) for c in range(num_cols)]
    col_numeric = [
        all(f.concept.isNumeric for row in data if (f := row[c]) is not None)
        for c in range(num_cols)
    ]
    return col_empty, col_numeric, all(col_numeric)


def _drop_empty_columns(
    matrix: _DataMatrix,
    col_empty: list[bool],
    col_numeric: list[bool],
) -> tuple[_DataMatrix, list[bool]]:
    keep = [not e for e in col_empty]
    return (
        _DataMatrix(
            style=matrix.style,
            data=[list(compress(row, keep)) for row in matrix.data],
            row_labels=matrix.row_labels,
            row_heading_label=matrix.row_heading_label,
            col_labels=list(compress(matrix.col_labels, keep)),
        ),
        list(compress(col_numeric, keep)),
    )


def _build_header_rows(
    row_heading_label: _TableHeadingValue,
    col_labels: list[Concept],
    col_numeric: list[bool],
    all_numeric: bool,
    table_unit: str | None,
    table_period: _Period | None,
    column_units: list[str | None],
    column_periods: list[_Period | None],
) -> list[list[TableHeadingCell]]:
    max_cols = max(1, len(col_labels))
    hrows: dict[int, list[TableHeadingCell]] = defaultdict(list)
    row_counter = count()
    if table_period:
        hrows[next(row_counter)].append(
            TableHeadingCell(table_period, colspan=max_cols, rowspan=1)
        )
    if table_unit:
        hrows[next(row_counter)].append(
            TableHeadingCell(table_unit, colspan=max_cols, rowspan=1, numeric=True)
        )
    row_num = next(row_counter)
    for cnum, col in enumerate(col_labels):
        hrows[row_num].append(
            TableHeadingCell(col, colspan=1, rowspan=1, numeric=all_numeric or col_numeric[cnum])
        )
    if not table_period and column_periods:
        row_num = next(row_counter)
        for cnum, cp in enumerate(column_periods):
            hrows[row_num].append(
                TableHeadingCell(cp, colspan=1, rowspan=1, numeric=all_numeric or col_numeric[cnum])
            )
    if not table_unit and column_units:
        row_num = next(row_counter)
        for cnum, cu in enumerate(column_units):
            hrows[row_num].append(
                TableHeadingCell(cu, colspan=1, rowspan=1, numeric=all_numeric or col_numeric[cnum])
            )
    if hrows:
        hrows[0].insert(
            0,
            TableHeadingCell(row_heading_label, colspan=1, rowspan=len(hrows)),
        )
    return [hrow for hrow in hrows.values() if not all(c.value is None for c in hrow)]


def _build_table_rows(
    matrix: _DataMatrix,
    table_unit: str | None,
    column_units: list[str | None],
) -> list[TableRow]:
    return [
        TableRow(
            heading=TableHeadingCell(rh),
            cells=[
                TableCell(
                    fact=fact,
                    suppress_unit=(
                        table_unit is not None
                        or (j < len(column_units) and column_units[j] is not None)
                    ),
                )
                for j, fact in enumerate(raw_row)
            ],
        )
        for rh, raw_row in zip(matrix.row_labels, matrix.data)
    ]


# ── Orchestrator ──────────────────────────────────────────────────────────────


class ReportLayoutOrganiser:
    def __init__(self, taxonomy: Taxonomy, report: InlineReport):
        self.taxonomy = taxonomy
        self.report = report
        self.presentation = self.taxonomy.presentation
        self.reportSections: list[ReportSection] = []

    @staticmethod
    def _sectionPrefix(section: ReportSection) -> str:
        """Extract the group prefix (e.g. '[B02') from a section's definition."""
        return section.presentation.definition.split(".")[0]

    def organise(self) -> list[ReportSection]:
        self.createReportSections()
        self.createReportTables()
        self.reportSections.sort(key=lambda x: x.presentation)
        self._vsme_move_hacks_for_efrag_report()
        self.checkAllFactsUsed()
        return self.reportSections

    def _vsme_move_hacks_for_efrag_report(self) -> None:
        """Reorder sections: [C02] after [B02]."""
        self._move_sections_after("[C02", "[B02")

    def _move_sections_after(self, source_prefix: str, target_prefix: str) -> None:
        """Move all sections with *source_prefix* to immediately after the last *target_prefix* section."""
        prefixes = {id(s): self._sectionPrefix(s) for s in self.reportSections}
        to_move = [s for s in self.reportSections if prefixes[id(s)] == source_prefix]
        if not to_move:
            return
        remaining = [s for s in self.reportSections if prefixes[id(s)] != source_prefix]
        insert_pos = None
        for i, s in enumerate(remaining):
            if prefixes[id(s)] == target_prefix:
                insert_pos = i + 1
        if insert_pos is None:
            return
        self.reportSections = remaining[:insert_pos] + to_move + remaining[insert_pos:]

    def checkAllFactsUsed(self) -> None:
        """
        Checks that all facts in the report have been used in the report sections.
        Raises an InlineReportException if any facts are not used.
        """
        potential_unused_facts = set(self.report.facts)
        for section in self.reportSections:
            if not section.tabular:
                for facts in section.relationshipToFact.values():
                    potential_unused_facts.difference_update(facts)
            else:
                tabular = cast(TabularReportSection, section)
                for row in tabular.table.rows:
                    potential_unused_facts.difference_update(
                        cell.fact for cell in row.cells if cell.fact is not None
                    )
        unused_facts = frozenset(potential_unused_facts)
        if unused_facts:
            processed: set[Fact] = set()
            for u in unused_facts:
                if u in processed:
                    continue
                others = list(self.report.getFacts(u.concept))
                others.remove(u)
                u_aspects = frozenset(u.aspects.items())
                inconsistent_duplicates = [
                    f
                    for f in others
                    if frozenset(f.aspects.items()) == u_aspects and f.value != u.value
                ]
                processed.add(u)
                processed.update(inconsistent_duplicates)
                if inconsistent_duplicates:
                    L.warning(
                        f"Fact has inconsistent duplicates.\nUnused: {u}\nOthers: {inconsistent_duplicates}"
                    )

    def createReportSections(self) -> None:
        for group in self.presentation:
            if group.style == PresentationStyle.Empty:
                self.reportSections.append(
                    ReportSection(relationshipToFact={}, presentation=group)
                )
                continue

            factsForRel: dict[Relationship, list[Fact]] = defaultdict(list)
            # TODO: store hasHypercubes:bool on the group and avoid check every time here.
            for rel in group.relationships:
                concept = rel.concept
                factsForConcept = self.report.getFacts(concept)
                if not factsForConcept:
                    continue
                if group.style == PresentationStyle.List:
                    factsForRel[rel].extend(
                        fact
                        for fact in factsForConcept
                        if not fact.hasTaxonomyDimensions()
                    )
                elif group.style in {PresentationStyle.Hybrid, PresentationStyle.Table}:
                    factsForRel[rel].extend(factsForConcept)
                else:
                    pass  # No reportable concepts in this group so nothing to do.
            self.reportSections.append(
                ReportSection(relationshipToFact=factsForRel, presentation=group)
            )

    def createReportTables(self) -> None:
        table_sections: dict[str, TabularReportSection] = {}
        for section in self.reportSections:
            if section.presentation.style in {
                PresentationStyle.List,
                PresentationStyle.Empty,
            }:
                # Nothing to do as these don't have tables
                continue

            if section.presentation.style is PresentationStyle.Hybrid:
                raise InlineReportException(
                    f"Presentation group style ({section.presentation.style.name}) of [{section.presentation.roleUri}] is not currently supported."
                )

            hypercubes = [
                r for r in section.presentation.relationships if r.concept.isHypercube
            ]
            if len(hypercubes) != 1:
                raise InlineReportException(
                    f"Presentation structure of [{section.presentation.roleUri}] is not currently supported."
                )

            typedDims = [
                r.concept for r in section.presentation.relationships if r.concept.isTypedDimension
            ]
            explicitDims = [
                r.concept for r in section.presentation.relationships if r.concept.isExplicitDimension
            ]
            reportable = [
                r.concept for r in section.presentation.relationships if r.concept.isReportable
            ]
            roleUri = section.presentation.roleUri

            matrix: _DataMatrix | None = None
            if len(typedDims) == 1 and not explicitDims:
                matrix = self._assemble_typed_dim(roleUri, typedDims, reportable)
            elif len(explicitDims) == 1 and not typedDims:
                explicitDim = explicitDims[0]
                domain_set = self.taxonomy.getDomainMembersForExplicitDimension(explicitDim)
                domain: list[Concept] = [
                    rel.concept
                    for rel in section.presentation.relationships
                    if rel.concept in domain_set
                ]
                defaultMember = self.taxonomy.getDimensionDefault(explicitDim)
                if len(domain) <= len(reportable):
                    matrix = self._assemble_explicit_dim_as_columns(roleUri, reportable, explicitDim, domain, defaultMember)
                else:
                    matrix = self._assemble_explicit_dim_as_rows(roleUri, reportable, explicitDim, domain, defaultMember)

            if matrix is None or not matrix.data:
                continue

            col_empty, col_numeric, all_numeric = _column_flags(matrix.data)
            if True in col_empty:
                matrix, col_numeric = _drop_empty_columns(matrix, col_empty, col_numeric)

            table_unit = _table_unit(matrix.data)
            table_period = _table_period(matrix.data)
            col_units = _column_units(matrix.data)
            col_periods = _column_periods(matrix.data)

            header_rows = _build_header_rows(
                matrix.row_heading_label, matrix.col_labels,
                col_numeric, all_numeric,
                table_unit, table_period, col_units, col_periods,
            )
            table_rows = _build_table_rows(matrix, table_unit, col_units)

            table_sections[roleUri] = TabularReportSection(
                relationshipToFact=section.relationshipToFact,
                presentation=section.presentation,
                table=Table(
                    style=matrix.style,
                    numeric=all_numeric,
                    header_rows=header_rows,
                    rows=table_rows,
                ),
            )

        merged_sections: list[ReportSection] = []
        for section in self.reportSections:
            roleUri = section.presentation.roleUri
            if section.style is PresentationStyle.Table:
                if new_section := table_sections.get(roleUri):
                    merged_sections.append(new_section)
                else:
                    # table without data, drop the section.
                    continue
            else:
                merged_sections.append(section)
        self.reportSections = merged_sections

    def _assemble_explicit_dim_as_columns(
        self,
        roleUri: str,
        reportable: list[Concept],
        explicitDim: Concept,
        domain: list[Concept],
        defaultMember: Concept | None,
    ) -> _DataMatrix:
        data: list[list[Fact | None]] = []
        row_labels: list[Concept | str] = []
        for r in reportable:
            row: list[Fact | None] = []
            for c in domain:
                found: Fact | None = None
                for fact in self.report.getFacts(r):
                    eValue = fact.aspects.get(explicitDim.qname)
                    if (eValue is None and c == defaultMember) or (
                        eValue is not None and eValue == c.qname
                    ):
                        if found is not None:
                            L.debug(
                                f"Multiple facts found (handle this better) {roleUri=} style=SingleExplicitDimensionColumn\n{found=}\n{fact=}"
                            )
                        found = fact
                row.append(found)
            if len(row) != len(domain):
                raise InlineReportException(
                    f"Failed to fill row correctly {r}, with {domain}"
                )
            if not all(c is None for c in row):
                data.append(row)
                row_labels.append(r)
        return _DataMatrix(
            style=TableStyle.SingleExplicitDimensionColumn,
            data=data,
            row_labels=row_labels,
            row_heading_label=None,
            col_labels=domain,
        )

    def _assemble_explicit_dim_as_rows(
        self,
        roleUri: str,
        reportable: list[Concept],
        explicitDim: Concept,
        domain: list[Concept],
        defaultMember: Concept | None,
    ) -> _DataMatrix:
        data: list[list[Fact | None]] = []
        row_labels: list[Concept | str] = []
        for r in domain:
            row: list[Fact | None] = []
            for c in reportable:
                found: Fact | None = None
                for fact in self.report.getFacts(c):
                    eValue = fact.aspects.get(explicitDim.qname)
                    if (eValue is None and r == defaultMember) or (
                        eValue is not None and eValue == r.qname
                    ):
                        if found is not None:
                            L.debug(
                                f"Multiple facts found (handle this better) {roleUri=} style=SingleExplicitDimensionRow\n{found=}\n{fact=}"
                            )
                        found = fact
                row.append(found)
            if len(row) != len(reportable):
                raise InlineReportException(
                    f"Failed to fill row correctly {r}, with {reportable}"
                )
            if not all(c is None for c in row):
                data.append(row)
                row_labels.append(r)
        return _DataMatrix(
            style=TableStyle.SingleExplicitDimensionRow,
            data=data,
            row_labels=row_labels,
            row_heading_label=explicitDim,
            col_labels=reportable,
        )

    def _assemble_typed_dim(
        self,
        roleUri: str,
        typedDims: list[Concept],
        reportable: list[Concept],
    ) -> _DataMatrix:
        typed_qname = f"typed {typedDims[0].qname}"
        td_values = {
            str(fact.aspects[typed_qname])
            for r in reportable
            for fact in self.report.getFacts(r)
        }
        pretty_td_values = [(tidyTdValue(v), v) for v in td_values]
        pretty_td_values.sort(key=lambda x: numeric_string_key(x[0]))

        data: list[list[Fact | None]] = []
        row_labels: list[Concept | str] = []
        for heading, r_key in pretty_td_values:
            row: list[Fact | None] = []
            for c in reportable:
                found: Fact | None = None
                for fact in self.report.getFacts(c):
                    td_value = fact.aspects.get(typed_qname)
                    if td_value is not None and td_value == r_key:
                        if found is not None:
                            L.debug(
                                f"Multiple facts found (handle this better) {roleUri=} style=SingleTypedDimensionColumn\n{found=}\n{fact=}"
                            )
                        found = fact
                row.append(found)
            if len(row) != len(reportable):
                raise InlineReportException(
                    f"Failed to fill row correctly {heading}, with {reportable}"
                )
            if not all(c is None for c in row):
                data.append(row)
                row_labels.append(heading)
        return _DataMatrix(
            style=TableStyle.SingleTypedDimensionColumn,
            data=data,
            row_labels=row_labels,
            row_heading_label=typedDims[0],
            col_labels=reportable,
        )
