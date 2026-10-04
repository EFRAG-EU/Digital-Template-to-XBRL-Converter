"""From a report's facts to the sections of its Inline XBRL document.

One section per presentation group. A group that is a *list* shows its facts as a list; one that is
a *table* (its presentation has a hypercube) is laid out as the hypercube says. A dimensionally
qualified fact in a list, and any fact no group's table shows (a hypercube with no dimension or
several, say), gets a table built from the facts' own dimensions, so nothing is dropped for want
of a layout. A concept can sit in several groups, so "no group shows it" is judged across all of
them, not within one.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from typing import TYPE_CHECKING

from mireport.exceptions import InlineReportException
from mireport.report.fact import Fact
from mireport.report.layout.entries import list_entries
from mireport.report.layout.grid import GridBuilder
from mireport.report.layout.headers import assemble_table
from mireport.report.layout.model import (
    FactGrid,
    ReportSection,
    TabularReportSection,
)
from mireport.report.model import ReportPeriod
from mireport.taxonomy import Concept, PresentationStyle, Relationship, Taxonomy

if TYPE_CHECKING:
    from mireport.report.disclosure_layout import DisclosureLayoutStrategy
    from mireport.report.inlinereport import InlineReport

L = logging.getLogger(__name__)

# A fact, and the presentation relationship that put it in its group.
Placed = tuple[Relationship, Fact]


class ReportLayoutOrganiser:
    def __init__(self, taxonomy: Taxonomy, report: InlineReport):
        self.taxonomy = taxonomy
        self.report = report
        self.presentation = self.taxonomy.presentation
        self.reportSections: list[ReportSection] = []
        self._grids = GridBuilder(report.getFacts, self._label, self._period_rank)
        # Facts a List group cannot show in its list because they carry taxonomy dimensions, in
        # presentation order, keyed by the group's role.
        self._dimensional: dict[str, list[Placed]] = {}

    def organise(self, layout: DisclosureLayoutStrategy) -> list[ReportSection]:
        self.createReportSections()
        self.createReportTables()
        self.reportSections.sort(key=lambda x: x.presentation)
        self.reportSections = layout.organise_sections(self.reportSections)
        self.checkAllFactsUsed()
        return [s for s in self.reportSections if s.hasFacts]

    # -- checking ------------------------------------------------------------------------------

    def checkAllFactsUsed(self) -> None:
        """
        Checks that all facts in the report have been used in the report sections.
        Raises an InlineReportException if any facts are not used (and the report says they must be).
        """
        unused = set(self.report.facts)
        for section in self.reportSections:
            if isinstance(section, TabularReportSection):
                unused.difference_update(
                    cell.fact
                    for row in section.table.rows
                    for cell in row.cells
                    if cell.fact is not None
                )
            else:
                for facts in section.relationshipToFact.values():
                    unused.difference_update(facts)
        if not unused:
            return

        processed: set[Fact] = set()
        for fact in unused:
            if fact in processed:
                continue
            others = list(self.report.getFacts(fact.concept))
            others.remove(fact)
            inconsistent = [
                f
                for f in others
                if f.context_key == fact.context_key and f.value != fact.value  # type: ignore[operator]
            ]
            processed.add(fact)
            processed.update(inconsistent)
            if inconsistent:
                # Surfaced to the report preparer as an error via
                # InlineReport.reportInconsistentDuplicateFacts; kept here at
                # debug level only as a developer safety net.
                L.debug(
                    f"Fact has inconsistent duplicates.\nUnused: {fact}\nOthers: {inconsistent}"
                )
        if self.report.requireAllFactsRendered:
            raise InlineReportException(
                f"{len(unused)} fact(s) appear nowhere in the report: "
                + "; ".join(sorted(repr(f) for f in unused))
            )

    # -- sections ------------------------------------------------------------------------------

    def createReportSections(self) -> None:
        """One section per presentation group, holding the facts of the group's concepts."""
        for group in self.presentation:
            if group.style == PresentationStyle.Empty:
                self.reportSections.append(
                    ReportSection(relationshipToFact={}, presentation=group)
                )
                continue

            factsForRel: dict[Relationship, list[Fact]] = defaultdict(list)
            for rel in group.relationships:
                if not (facts := self.report.getFacts(rel.concept)):
                    continue
                match group.style:
                    case PresentationStyle.List:
                        for fact in facts:
                            if fact.hasTaxonomyDimensions():
                                self._dimensional.setdefault(group.roleUri, []).append(
                                    (rel, fact)
                                )
                            else:
                                factsForRel[rel].append(fact)
                    case PresentationStyle.Hybrid | PresentationStyle.Table:
                        factsForRel[rel].extend(facts)
            self.reportSections.append(
                ReportSection(
                    relationshipToFact=factsForRel,
                    presentation=group,
                    entries=(
                        list_entries(factsForRel, self._period_rank)
                        if group.style == PresentationStyle.List
                        else ()
                    ),
                )
            )

    def createReportTables(self) -> None:
        """Tables for the table groups, and for whatever any group could not place."""
        groups = list(self.reportSections)
        sections: list[ReportSection] = []
        for section in groups:
            if section.style in {PresentationStyle.Table, PresentationStyle.Hybrid}:
                if (table := self._hypercube_table(section)) is not None:
                    sections.append(table)
            else:
                sections.append(section)
                sections += self._dimension_set_tables(
                    section, self._dimensional.get(section.presentation.roleUri, [])
                )
        self.reportSections = sections
        self.reportSections += self._tables_for_facts_shown_nowhere(groups)

    def _hypercube_table(self, section: ReportSection) -> TabularReportSection | None:
        """The table a table group's hypercube describes, if it describes one."""
        group = section.presentation
        if group.style is PresentationStyle.Hybrid:
            raise InlineReportException(
                f"Presentation group style ({group.style.name}) of [{group.roleUri}] is not currently supported."
            )
        if sum(r.concept.isHypercube for r in group.relationships) != 1:
            raise InlineReportException(
                f"Presentation structure of [{group.roleUri}] is not currently supported."
            )

        concepts = [r.concept for r in group.relationships]
        typedDims = [c for c in concepts if c.isTypedDimension]
        explicitDims = [c for c in concepts if c.isExplicitDimension]
        reportable = [c for c in concepts if c.isReportable]

        grid: FactGrid | None
        match (typedDims, explicitDims):
            case ([typedDim], []):
                grid = self._grids.typed_dimension(reportable, typedDim)
            case ([], [explicitDim]):
                domain_set = self.taxonomy.getDomainMembersForExplicitDimension(
                    explicitDim
                )
                grid = self._grids.explicit_dimension(
                    reportable,
                    explicitDim,
                    [c for c in concepts if c in domain_set],
                    self.taxonomy.getDimensionDefault(explicitDim),
                )
            case _:
                # No dimension, or several: nothing for a one-dimension grid to follow.
                grid = None

        if grid is None or not grid.data:
            return None
        return self._section(section, grid)

    def _tables_for_facts_shown_nowhere(
        self, groups: list[ReportSection]
    ) -> list[ReportSection]:
        """Tables for facts that no section shows, each in the first group holding its concept.
        `groups` are the sections as the groups made them, before any became a table or none."""
        shown: set[int] = set()
        for section in self.reportSections:
            match section:
                case TabularReportSection(table=table):
                    shown.update(
                        id(cell.fact)
                        for row in table.rows
                        for cell in row.cells
                        if cell.fact is not None
                    )
                case _:
                    shown.update(
                        id(f)
                        for facts in section.relationshipToFact.values()
                        for f in facts
                    )

        home: dict[Concept, tuple[ReportSection, Relationship]] = {}
        for section in groups:
            for rel in section.presentation.relationships:
                home.setdefault(rel.concept, (section, rel))

        leftovers: dict[int, tuple[ReportSection, list[Placed]]] = {}
        for fact in self.report.facts:
            if id(fact) not in shown and (found := home.get(fact.concept)) is not None:
                section, rel = found
                leftovers.setdefault(id(section), (section, []))[1].append((rel, fact))
        return [
            table
            for section, placed in leftovers.values()
            for table in self._dimension_set_tables(section, placed)
        ]

    def _dimension_set_tables(
        self, section: ReportSection, placed: list[Placed]
    ) -> list[ReportSection]:
        """A table for each set of dimensions among facts the group could not place, titled by
        the dimensions ("... - by Target Category and Target Identifier")."""
        bySignature: dict[tuple[Concept, ...], list[Placed]] = {}
        for rel, fact in placed:
            dimensions = {*fact.explicit_dimensions, *fact.typed_dimensions}
            signature = tuple(sorted(dimensions, key=lambda d: str(d.qname)))
            bySignature.setdefault(signature, []).append((rel, fact))
        return [
            self._section(
                section,
                self._grids.dimension_set(signature, items),
                "by " + " and ".join(self._label(d) for d in signature)
                if signature
                else "",
            )
            for signature, items in bySignature.items()
        ]

    @staticmethod
    def _section(
        section: ReportSection, grid: FactGrid, heading_suffix: str = ""
    ) -> TabularReportSection:
        return TabularReportSection(
            relationshipToFact=section.relationshipToFact,
            presentation=section.presentation,
            heading_suffix=heading_suffix,
            table=assemble_table(grid),
        )

    def _period_rank(self, period: ReportPeriod) -> int:
        """Where a period comes among a report's periods: current, prior, then the rest."""
        return next(
            (n for n, p in enumerate(self.report.reportingPeriods) if p == period),
            len(self.report.reportingPeriods),
        )

    def _label(self, concept: Concept) -> str:
        language = self.taxonomy.getBestSupportedLanguage(self.report.language)
        return concept.getStandardLabel(
            language,
            fallbackToAnyLang=True,
            fallbackToQName=language is None,
            removeSuffix=True,
        ) or str(concept.qname)
