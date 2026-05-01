"""Map openpyxl named ranges to XBRL concepts, producing WorkbookBindings."""

from __future__ import annotations

import logging
from collections import defaultdict
from itertools import combinations
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from mireport.taxonomy import Taxonomy
    from mireport.xlsx_template_reader._util import WorkbookReader

from mireport.conversionresults import MessageType, Severity
from mireport.xlsx_template_reader._bindings import (
    CellAndXBRLMetadataHolder,
    TableXBRLContents,
    WorkbookBindings,
)
from mireport.xlsx_template_reader._util import (
    conceptsToText,
    excelDefinedNameRef,
)

L = logging.getLogger(__name__)


def build_bindings(
    reader: WorkbookReader,
    taxonomy: Taxonomy,
    defaults: dict,
) -> WorkbookBindings:
    """Scrape named ranges from the workbook and return a WorkbookBindings.

    Side effects: populates ``reader._unused`` with DefinedName objects that
    were not matched to any XBRL concept.
    """
    concept_map: dict = {}
    unit_map: dict = {}
    preset_dims: defaultdict = defaultdict(dict)

    wb = reader._workbook
    results = reader._results
    unused = reader._unused

    unused.update(
        dn
        for dn in wb.defined_names.values()
        if dn.name and not dn.name.startswith(("enum_", "template_"))
    )

    for dn in list(unused):
        if dn.name is None:
            results.addMessage(
                "Named range has no name. Skipping.",
                Severity.ERROR,
                MessageType.DevInfo,
                excel_reference=excelDefinedNameRef(dn),
            )
            unused.discard(dn)
            continue

        concept = taxonomy.getConceptForName(dn.name)

        # TODO FIXME Temporary fix for the VSME taxonomy
        if dn.name == "IdentifierOfSitesInBiodiversitySensitiveAreasTypedAxis":
            concept = taxonomy.getConceptForName("IdentifierOfSiteTypedAxis")
        # TODO FIXME Temporary fix for the VSME taxonomy

        if concept is not None:
            if (crh := reader._getCellRange(dn)) is not None:
                concept_map[dn] = CellAndXBRLMetadataHolder.fromCellRangeMetadata(
                    crh, concept=concept
                )
        elif "_" in dn.name:
            conceptName, _, memberName = dn.name.partition("_")
            if "unit" == memberName:
                if (
                    concept := taxonomy.getConceptForName(conceptName)
                ) is not None and (crh := reader._getCellRange(dn)) is not None:
                    unit_map[concept] = CellAndXBRLMetadataHolder.fromCellRangeMetadata(
                        crh, concept
                    )
                    unused.discard(dn)
            else:
                concept = taxonomy.getConceptForName(conceptName)
                dimValue = taxonomy.getConceptForName(memberName)
                crh = reader._getCellRange(dn)
                if crh is not None and concept is not None and dimValue is not None:
                    b = CellAndXBRLMetadataHolder.fromCellRangeMetadata(
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
                        results.addMessage(
                            f"Domain member qualification set in named range {dn.name} but no dimension can be found for member.",
                            Severity.ERROR,
                            MessageType.DevInfo,
                        )
        if dn in concept_map:
            unused.discard(dn)

    results.addMessage(
        f"Excel file parsed ({results.numCellsPopulated} cells had data, with {results.numCellQueries} cells accessed).",
        Severity.INFO,
        MessageType.ExcelParsing,
    )

    table_map: dict = {}

    tables = [
        (dn, stuff)
        for dn, stuff in concept_map.items()
        if stuff.concept in taxonomy.hypercubes
    ]
    concepts_in_excel = frozenset(stuff.concept for stuff in concept_map.values())
    hc_concepts_in_excel = frozenset(c for c in concepts_in_excel if c.isHypercube)
    used_empty_hypercubes = taxonomy.emptyHypercubes.intersection(hc_concepts_in_excel)
    if used_empty_hypercubes:
        results.addMessage(
            f"The following hypercubes exist and have corresponding named ranges but they cannot be used due to missing taxonomy definitions: {conceptsToText(used_empty_hypercubes)}.",
            Severity.ERROR,
            MessageType.DevInfo,
        )

    for table, table_stuff in tables:
        tableCr = table_stuff.cellRange
        tableWorksheet = table_stuff.worksheet
        table_concept = table_stuff.concept

        allPermittedConceptsForTable = taxonomy.getDimensionsForHypercube(
            table_concept
        ).union(
            {
                concept
                for concept in taxonomy.getPrimaryItemsForHypercube(table_concept)
                if concept.isReportable or concept.isDimension
            }
        )
        missing_from_excel = allPermittedConceptsForTable.difference(concepts_in_excel)
        if missing_from_excel:
            results.addMessage(
                f"Expected Dimensions or Primary Items for hypercube {table.name} have not been found: {conceptsToText(missing_from_excel)}.",
                Severity.WARNING,
                MessageType.DevInfo,
            )

        candidates: list[CellAndXBRLMetadataHolder] = []
        extras_in_excel: set[CellAndXBRLMetadataHolder] = set()
        for dn, stuff in concept_map.items():
            if tableWorksheet is not stuff.worksheet:
                continue
            concept = stuff.concept
            if not (concept.isReportable or concept.isDimension):
                continue
            if tableCr.issuperset(stuff.cellRange):
                if concept in allPermittedConceptsForTable:
                    candidates.append(stuff)
                else:
                    extras_in_excel.add(stuff)
            elif not tableCr.isdisjoint(stuff.cellRange):
                extras_in_excel.add(stuff)

        if extras_in_excel:
            results.addMessage(
                f"Extra named ranges found within/overlapping bounds of {table.name} named range but not supported by Hypercube {table_stuff.concept.qname}: {extras_in_excel}.",
                Severity.WARNING,
                MessageType.DevInfo,
            )

        fishy = False
        for c1, c2 in combinations(candidates, 2):
            disjoint = c1.cellRange.isdisjoint(c2.cellRange)
            same = (
                c1.concept.isReportable
                and c2.concept.isReportable
                and (c1.cellRange.bounds == c2.cellRange.bounds)
            )
            if not (disjoint or same):
                fishy = True
                results.addMessage(
                    f"Named range (table) {table.name} has named ranges (primary items or dimensions) {c1.definedName.name} and {c2.definedName.name} that are neither the same nor disjoint. Ignoring table.",
                    Severity.ERROR,
                    MessageType.ExcelParsing,
                )
                break

        if not fishy:
            pItems = [c for c in candidates if c.concept.isReportable]
            eDims = [c for c in candidates if c.concept.isExplicitDimension]
            tDims = [c for c in candidates if c.concept.isTypedDimension]
            units = [u for p in pItems if (u := unit_map.get(p.concept)) is not None]
            table_map[table_stuff] = TableXBRLContents(
                primaryItems=pItems,
                explicitDimensions=eDims,
                typedDimensions=tDims,
                units=units,
            )

    # Remove table entries from concept_map (they're now in table_map)
    for tableStuff, table_contents in table_map.items():
        concept_map.pop(tableStuff.definedName, None)
        table_dict = table_contents._asdict()
        for name, part_list in table_dict.items():
            for holder in part_list:
                if "units" == name:
                    unit_map.pop(holder.concept, None)
                else:
                    concept_map.pop(holder.definedName, None)

    return WorkbookBindings(
        concept_map=concept_map,
        table_map=table_map,
        unit_map=unit_map,
        preset_dims=preset_dims,
        unused=unused,
    )
