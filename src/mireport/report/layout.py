from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass
from enum import Enum, auto
from itertools import count
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
    tableStyle: TableStyle
    dataColumns: list[Concept | None]
    rowHeadings: list[TableHeadingCell]
    data: list[list[Fact | None]]
    columnUnits: list[str | None]
    newColumnHeadings: list[list[TableHeadingCell]]
    columnPeriods: list[_Period | None]
    numeric: bool = False
    unitSymbol: Optional[str] = None
    period: Optional[_Period] = None

    @property
    def tabular(self) -> bool:
        return True

    @property
    def rowHeadingsHaveTitle(self) -> bool:
        if not self.dataColumns:
            return False
        firstCol = self.dataColumns[0]
        if firstCol is None:
            return False
        return True

    def columnHasUnit(self, colnum: int) -> bool:
        try:
            unit = self.columnUnits[colnum]
            return unit is not None
        except IndexError:
            return False

    @property
    def hasFacts(self) -> bool:
        for row in self.data:
            for fact in row:
                if fact is not None:
                    return True
        return False


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
                section = cast(TabularReportSection, section)
                for row in section.data:
                    potential_unused_facts.difference_update(row)
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
            if 1 != len(hypercubes):
                raise InlineReportException(
                    f"Presentation structure of [{section.presentation.roleUri}] is not currently supported."
                )

            typedDims = [
                r.concept
                for r in section.presentation.relationships
                if r.concept.isTypedDimension
            ]
            explicitDims = [
                r.concept
                for r in section.presentation.relationships
                if r.concept.isExplicitDimension
            ]
            reportable = [
                r.concept
                for r in section.presentation.relationships
                if r.concept.isReportable
            ]

            tableStyle = TableStyle.Other
            rowHeadings: list[Concept | Relationship | str | None] = []
            columnHeadings: list[Concept | None] = []
            data: list[list[Fact | None]] = []
            explicitDim = None

            if len(typedDims) == 1 and not explicitDims:
                tableStyle = self.assembleTypedDimTable(section, typedDims, reportable, rowHeadings, columnHeadings, data)
            elif len(explicitDims) == 1 and not typedDims:
                explicitDim = explicitDims[0]
                domain_set = self.taxonomy.getDomainMembersForExplicitDimension(
                    explicitDim
                )
                domain: list[Concept] = [
                    rel.concept
                    for rel in section.presentation.relationships
                    if rel.concept in domain_set
                ]
                defaultMember = self.taxonomy.getDimensionDefault(explicitDim)

                if len(domain) <= len(reportable):
                    tableStyle = self.assembleDimsAsColumnTable(section, reportable, rowHeadings, columnHeadings, data, explicitDim, domain, defaultMember)
                else:
                    tableStyle = self.assembleDimsAsRowsTable(section, reportable, rowHeadings, columnHeadings, data, explicitDim, domain, defaultMember)

            if not data:
                continue

            tableUnit = self.getTableUnit(data)
            columnUnits = self.getColumnUnits(data)
            tablePeriod = self.getTablePeriod(data)
            columnPeriods = self.getColumnPeriods(data)

            col_empty = [
                all(row[cnum] is None for row in data)
                for cnum in range(len(columnHeadings) - 1)
            ]
            col_numeric = [
                all(f.concept.isNumeric for row in data if (f := row[cnum]) is not None)
                for cnum in range(len(columnHeadings) - 1)
            ]
            all_numeric = all(col_numeric)

            if True in col_empty:
                new_data = [
                    [row[cnum] for cnum, empty in enumerate(col_empty) if not empty]
                    for row in data
                ]
                new_columnHeadings = [
                    ch
                    for cnum, ch in enumerate(columnHeadings[1:])
                    if not col_empty[cnum]
                ]
                assert len(new_columnHeadings) == len(new_data[0]), (
                    f"Expected number of column headings to match number of columns in data. {len(new_columnHeadings)=} {len(new_data[0])=}"
                )
                columnHeadings = [columnHeadings[0]] + new_columnHeadings
                data = new_data

            newColumnHeadings: list[list[TableHeadingCell]] = []
            max_cols = max(1, len(columnHeadings) - 1)
            headerRows: dict[int, list[TableHeadingCell]] = defaultdict(list)
            rowCounter = count()
            if tablePeriod:
                headerRows[next(rowCounter)].append(
                    TableHeadingCell(tablePeriod, colspan=max_cols, rowspan=1)
                )
            if tableUnit:
                headerRows[next(rowCounter)].append(
                    TableHeadingCell(
                        tableUnit, colspan=max_cols, rowspan=1, numeric=True
                    )
                )
            rowNum = next(rowCounter)

            colZeroLabel: _TableHeadingValue = ""
            if columnHeadings:
                colZeroLabel = columnHeadings.pop(0)

            for cnum, col in enumerate(columnHeadings):
                headerRows[rowNum].append(
                    TableHeadingCell(
                        col,
                        colspan=1,
                        rowspan=1,
                        numeric=all_numeric or col_numeric[cnum],
                    )
                )
            if not tablePeriod and columnPeriods:
                rowNum = next(rowCounter)
                for cnum, cp in enumerate(columnPeriods):
                    headerRows[rowNum].append(
                        TableHeadingCell(
                            cp,
                            colspan=1,
                            rowspan=1,
                            numeric=all_numeric or col_numeric[cnum],
                        )
                    )
            if not tableUnit and columnUnits:
                rowNum = next(rowCounter)
                for cnum, cu in enumerate(columnUnits):
                    headerRows[rowNum].append(
                        TableHeadingCell(
                            cu,
                            colspan=1,
                            rowspan=1,
                            numeric=all_numeric or col_numeric[cnum],
                        )
                    )
            if headerRows:
                headerRows[0].insert(
                    0,
                    TableHeadingCell(colZeroLabel, colspan=1, rowspan=len(headerRows)),
                )

            for hrow in headerRows.values():
                empty_row = all([c.value is None for c in hrow])
                if not empty_row:
                    newColumnHeadings.append(hrow)

            newRowHeadings: list[TableHeadingCell] = [
                TableHeadingCell(rh) for rh in rowHeadings
            ]

            table_sections[section.presentation.roleUri] = TabularReportSection(
                relationshipToFact=section.relationshipToFact,
                presentation=section.presentation,
                rowHeadings=newRowHeadings,
                dataColumns=columnHeadings,
                tableStyle=tableStyle,
                data=data,
                columnUnits=columnUnits,
                columnPeriods=columnPeriods,
                numeric=all_numeric,
                unitSymbol=tableUnit,
                period=tablePeriod,
                newColumnHeadings=newColumnHeadings,
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

    def assembleDimsAsColumnTable(self, section, reportable, rowHeadings, columnHeadings, data, explicitDim, domain, defaultMember) -> TableStyle:
        tableStyle = TableStyle.SingleExplicitDimensionColumn
        initialColumnHeadings = domain
        initialRowHeadings = reportable

        for r in initialRowHeadings:
            row: list[None | Fact] = []
            for c in initialColumnHeadings:
                found = None
                for fact in self.report.getFacts(r):
                    eValue = fact.aspects.get(explicitDim.qname)
                    if (eValue is None and c == defaultMember) or (
                                    eValue is not None and eValue == c.qname
                                ):
                        if found is not None:
                            L.debug(
                                            f"Multiple facts found (handle this better) {section.presentation.roleUri=} {tableStyle=}\n{found=}\n{fact=}"
                                        )
                        found = fact
                row.append(found)
            if len(row) != len(initialColumnHeadings):
                raise InlineReportException(
                                f"Failed to fill row correctly {r}, with {initialColumnHeadings}"
                            )
            row_empty = all(c is None for c in row)
            if not row_empty:
                data.append(row)
                rowHeadings.append(r)
                    # There is no column heading above the row headings
        columnHeadings.insert(0, None)
        columnHeadings.extend(domain)
        return tableStyle

    def assembleDimsAsRowsTable(self, section, reportable, rowHeadings, columnHeadings, data, explicitDim, domain, defaultMember) -> TableStyle:
        tableStyle = TableStyle.SingleExplicitDimensionRow
        initialColumnHeadings = reportable
        initialRowHeadings = domain

        for r in initialRowHeadings:
            row: list[None | Fact] = []
            for c in initialColumnHeadings:
                found = None
                for fact in self.report.getFacts(c):
                    eValue = fact.aspects.get(explicitDim.qname)
                    if (
                                    (eValue is None and r == defaultMember)
                                    or eValue is not None
                                    and eValue == r.qname
                                ):
                        if found is not None:
                            L.debug(
                                            f"Multiple facts found (handle this better) {section.presentation.roleUri=} {tableStyle=}\n{found=}\n{fact=}"
                                        )
                        found = fact
                row.append(found)
            if len(row) != len(initialColumnHeadings):
                raise InlineReportException(
                                f"Failed to fill row correctly {r}, with {initialColumnHeadings}"
                            )
            row_empty = all(c is None for c in row)
            if not row_empty:
                data.append(row)
                rowHeadings.append(r)
                    # Put the Dimension name as the heading above the row headings which are the domain members.
        columnHeadings.insert(0, explicitDim)
        columnHeadings.extend(reportable)
        return tableStyle

    def assembleTypedDimTable(self, section, typedDims, reportable, rowHeadings, columnHeadings, data) -> TableStyle:
        tableStyle = TableStyle.SingleTypedDimensionColumn
        initialColumnHeadings: list[Concept] = reportable
        typedQname = f"typed {typedDims[0].qname}"

        tdValues = {
                    str(fact.aspects[typedQname])
                    for r in reportable
                    for fact in self.report.getFacts(r)
                }
        prettyTdValues = [
                    (tidyTdValue(typedValue), typedValue) for typedValue in tdValues
                ]
        prettyTdValues.sort(key=lambda x: numeric_string_key(x[0]))
        for heading, rKey in prettyTdValues:
            row: list[None | Fact] = []
            for c in initialColumnHeadings:
                found = None
                for fact in self.report.getFacts(c):
                    tdValue = fact.aspects.get(typedQname)
                    if tdValue is not None and tdValue == rKey:
                        if found is not None:
                            L.debug(
                                        f"Multiple facts found (handle this better) {section.presentation.roleUri=} {tableStyle=}\n{found=}\n{fact=}"
                                    )
                        found = fact
                row.append(found)
            if len(row) != len(initialColumnHeadings):
                raise InlineReportException(
                            f"Failed to fill row correctly {heading}, with {initialColumnHeadings}"
                        )
            row_empty = all(c is None for c in row)
            if not row_empty:
                data.append(row)
                rowHeadings.append(heading)

                # Put the Dimension name as the heading above the row headings which are the domain members.
        columnHeadings.insert(0, typedDims[0])
        columnHeadings.extend(reportable)
        return tableStyle

    def getTableUnit(self, data: list[list[Fact | None]]) -> Optional[str]:
        units: set[str] = set()
        for row in data:
            for factOrNone in row:
                if factOrNone is None:
                    continue
                fact: Fact = factOrNone
                if fact.concept.isNumeric:
                    units.add(fact.unitSymbol)
        if 1 == len(units):
            unit = next(iter(units))
            if unit:
                return unit
        return None

    def getTablePeriod(self, data: list[list[Fact | None]]) -> Optional[_Period]:
        periods: set[_Period] = set()
        for row in data:
            for factOrNone in row:
                if factOrNone is None:
                    continue
                periods.add(factOrNone.period)
        if 1 == len(periods):
            return next(iter(periods))
        else:
            return None

    def getColumnPeriods(self, data: list[list[Fact | None]]) -> list[_Period | None]:
        colPeriodsMap: dict[int, set[_Period]] = defaultdict(set)
        totalNumberOfColumns: int = 0
        for row in data:
            totalNumberOfColumns = max(totalNumberOfColumns, len(row))
            for colnum, factOrNone in enumerate(row):
                if factOrNone is None:
                    continue
                fact: Fact = factOrNone
                colPeriodsMap[colnum].add(fact.period)
        # assert len(colUnitsMap) == totalNumberOfColumns, f"{len(colUnitsMap)} is not {totalNumberOfColumns}"
        columnPeriods: list[_Period | None] = []
        for c in range(totalNumberOfColumns):
            periods = colPeriodsMap[c]
            if 1 == len(periods):
                columnPeriods.append(next(iter(periods)))
                continue
            columnPeriods.append(None)
        assert len(columnPeriods) == totalNumberOfColumns
        if all(x is None for x in columnPeriods):
            return []
        return columnPeriods

    def getColumnUnits(self, data: list[list[Fact | None]]) -> list[str | None]:
        colUnitsMap: dict[int, set[str]] = defaultdict(set)
        totalNumberOfColumns: int = 0
        for row in data:
            totalNumberOfColumns = max(totalNumberOfColumns, len(row))
            for colnum, factOrNone in enumerate(row):
                if factOrNone is None:
                    continue
                fact: Fact = factOrNone
                if fact.concept.isNumeric:
                    colUnitsMap[colnum].add(fact.unitSymbol)
        # assert len(colUnitsMap) == totalNumberOfColumns, f"{len(colUnitsMap)} is not {totalNumberOfColumns}"
        columnUnits: list[str | None] = []
        for c in range(totalNumberOfColumns):
            units = colUnitsMap[c]
            if 1 == len(units):
                unit = next(iter(units))
                if unit:
                    columnUnits.append(unit)
                    continue
            columnUnits.append(None)
        assert len(columnUnits) == totalNumberOfColumns
        if all(x is None for x in columnUnits):
            return []
        return columnUnits
