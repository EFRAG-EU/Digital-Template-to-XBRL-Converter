"""WorkbookBinder: discover and validate the XBRL named-range bindings in a workbook.

Scrapes the workbook's defined names against the taxonomy, resolves hypercube
tables, external values and footnotes via the resolvers, and returns a
WorkbookBindings ready for fact creation.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from openpyxl.workbook.defined_name import DefinedName
    from openpyxl.worksheet.worksheet import Worksheet

    from mireport.taxonomy import Concept, Taxonomy
    from mireport.xlsx_template_reader._reader import WorkbookReader

from mireport.conversionresults import ConversionResultsBuilder, MessageType
from mireport.xlsx_template_reader._bindings import TableBinding, WorkbookBindings
from mireport.xlsx_template_reader._constants import TAXONOMY_NAME_ALIASES
from mireport.xlsx_template_reader._messages import Messenger
from mireport.xlsx_template_reader._ranges import XbrlConceptCellRangeMetadata
from mireport.xlsx_template_reader._resolvers import (
    ExcelCellBindingContext,
    XBRLTableResolver,
    resolveExternalValues,
    resolveFootnoteBinding,
)
from mireport.xlsx_template_reader.util import conceptsToText

L = logging.getLogger(__name__)


class WorkbookBinder:
    """Turns a workbook's named ranges into a validated WorkbookBindings."""

    def __init__(
        self,
        reader: WorkbookReader,
        taxonomy: Taxonomy,
        results: ConversionResultsBuilder,
    ) -> None:
        self._reader = reader
        self._taxonomy = taxonomy
        self._results = results
        self._msg = Messenger(results)

    def bind(self) -> WorkbookBindings:
        """Scrape named ranges from the workbook and return a WorkbookBindings."""
        reader = self._reader
        taxonomy = self._taxonomy

        concept_map: dict[DefinedName, XbrlConceptCellRangeMetadata] = {}
        unit_map: dict[Concept, XbrlConceptCellRangeMetadata] = {}
        preset_dims: defaultdict[XbrlConceptCellRangeMetadata, dict[Concept, Concept]]
        preset_dims = defaultdict(dict)

        # unused_defined_names is a set of identity-hashed DefinedNames; sort so
        # binding (and hence fact/message) order is stable across runs.
        for dn in sorted(reader.unused_defined_names, key=lambda dn: dn.name or ""):
            concept = taxonomy.getConceptForName(
                TAXONOMY_NAME_ALIASES.get(dn.name, dn.name)
            )

            if concept is not None:
                if (crh := reader.peekRange(dn)) is not None:
                    concept_map[dn] = (
                        XbrlConceptCellRangeMetadata.fromCellRangeMetadata(
                            crh, concept=concept
                        )
                    )
            elif "_" in dn.name:
                conceptName, _, memberName = dn.name.partition("_")
                if "unit" == memberName:
                    if (
                        concept := taxonomy.getConceptForName(conceptName)
                    ) is not None and (crh := reader.peekRange(dn)) is not None:
                        unit_map[concept] = (
                            XbrlConceptCellRangeMetadata.fromCellRangeMetadata(
                                crh, concept
                            )
                        )
                        reader.markUsed(dn)
                else:
                    concept = taxonomy.getConceptForName(conceptName)
                    dimValue = taxonomy.getConceptForName(memberName)
                    crh = reader.peekRange(dn)
                    if crh is not None and concept is not None and dimValue is not None:
                        b = XbrlConceptCellRangeMetadata.fromCellRangeMetadata(
                            crh, concept=concept
                        )
                        if (
                            dim := taxonomy.getExplicitDimensionForDomainMember(
                                concept, dimValue
                            )
                        ) is not None:
                            concept_map[dn] = b
                            preset_dims[b][dim] = dimValue
                        else:
                            self._msg.error(
                                f"Domain member qualification set in named range {dn.name} but no dimension can be found for member.",
                                MessageType.DevInfo,
                            )
            if dn in concept_map:
                reader.markUsed(dn)

        self._msg.info(
            f"Excel file parsed ({self._results.numCellsPopulated} cells had data, with {self._results.numCellQueries} cells accessed).",
            MessageType.ExcelParsing,
        )

        hypercube_ranges, concepts_in_excel, candidates_by_ws = (
            self._indexXbrlCandidates(concept_map)
        )
        if empty := taxonomy.emptyHypercubes.intersection(
            c for c in concepts_in_excel if c.isHypercube
        ):
            self._msg.error(
                f"The following hypercubes exist and have corresponding named ranges but they cannot be used due to missing taxonomy definitions: {conceptsToText(empty)}.",
                MessageType.DevInfo,
            )

        ctx = ExcelCellBindingContext(reader, self._msg, taxonomy)

        table_resolver = XBRLTableResolver(
            ctx, unit_map, candidates_by_ws, concepts_in_excel
        )
        tables: list[TableBinding] = [
            binding
            for table_range in hypercube_ranges
            if (binding := table_resolver.resolve(table_range)) is not None
        ]
        self._consumeTableBindings(concept_map, unit_map, tables)

        return WorkbookBindings(
            concept_map=concept_map,
            tables=tables,
            unit_map=unit_map,
            preset_dims=preset_dims,
            has_external_value=resolveExternalValues(ctx),
            footnote=resolveFootnoteBinding(ctx),
        )

    def _indexXbrlCandidates(
        self,
        concept_map: dict[DefinedName, XbrlConceptCellRangeMetadata],
    ) -> tuple[
        list[XbrlConceptCellRangeMetadata],
        frozenset[Concept],
        dict[Worksheet, list[XbrlConceptCellRangeMetadata]],
    ]:
        """Single pass over concept_map: the hypercube table ranges, every concept
        present, and a worksheet-keyed index of reportable/dimension candidate ranges."""
        hypercubes = self._taxonomy.hypercubes
        hypercube_ranges: list[XbrlConceptCellRangeMetadata] = []
        concepts_in_excel: list[Concept] = []
        candidates_by_ws: defaultdict[Worksheet, list[XbrlConceptCellRangeMetadata]] = (
            defaultdict(list)
        )
        for crm in concept_map.values():
            concept = crm.concept
            concepts_in_excel.append(concept)
            if concept in hypercubes:
                hypercube_ranges.append(crm)
            if concept.isReportable or concept.isDimension:
                candidates_by_ws[crm.worksheet].append(crm)
        return hypercube_ranges, frozenset(concepts_in_excel), candidates_by_ws

    @staticmethod
    def _consumeTableBindings(
        concept_map: dict[DefinedName, XbrlConceptCellRangeMetadata],
        unit_map: dict[Concept, XbrlConceptCellRangeMetadata],
        bindings: list[TableBinding],
    ) -> None:
        """Remove resolved table entries from concept_map/unit_map (now held in bindings)."""
        for binding in bindings:
            for crm in binding.conceptRanges:
                concept_map.pop(crm.definedName, None)
            for u in binding.units:
                unit_map.pop(u.concept, None)
