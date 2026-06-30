"""Resolvers that turn workbook named ranges into validated binding holders.

A "named range table" is a container range (a hypercube table, or the footnote
table) that holds a number of sub-ranges which must sit within it. The shared
geometry validation (containment + non-overlap) lives on the base class; the two
concrete resolvers differ in how they discover sub-ranges:

* SimpleNamedRangeTableResolver  -- sub-ranges named by fixed defined names (footnotes).
* XBRLNamedRangeTableResolver    -- sub-ranges discovered geometrically (hypercubes).
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from itertools import combinations
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from mireport.taxonomy import Taxonomy
    from mireport.xlsx_template_reader._reader import WorkbookReader

from mireport.conversionresults import ConversionResultsBuilder, MessageType, Severity
from mireport.xlsx_template_reader._bindings import (
    CellRangeMetadata,
    FootnoteBinding,
    XbrlConceptCellRangeMetadata,
    XbrlTableCellRangeMetadataHolder,
)
from mireport.xlsx_template_reader.util import conceptsToText

L = logging.getLogger(__name__)

# Footnote named ranges.
_FOOTNOTE_TABLE = "footnote_table"
_FOOTNOTE_TEXT = "footnote_text"
_FOOTNOTE_REF = "footnote_ref_concept"
_FOOTNOTE_REF_DIMENSION = "footnote_ref_dimension"


class NamedRangeTableResolver(ABC):
    """Base: a container named range plus sub-ranges that must sit within it."""

    def __init__(
        self, reader: WorkbookReader, results: ConversionResultsBuilder
    ) -> None:
        self._reader = reader
        self._results = results

    @abstractmethod
    def resolve(self) -> object:
        """Resolve and validate this resolver's named ranges into a binding holder."""
        ...

    def _validate_sub_ranges(
        self,
        table: CellRangeMetadata,
        sub_ranges: list[CellRangeMetadata],
        context: str,
    ) -> bool:
        """Return False (and emit a warning) if any sub-range is outside the table or if any two overlap."""
        for crm in sub_ranges:
            if not table.contains(crm):
                self._results.addMessage(
                    f"'{crm.definedName.name}' is not fully contained within "
                    f"'{table.definedName.name}'. {context}",
                    Severity.WARNING,
                    MessageType.ExcelParsing,
                )
                return False
        for c1, c2 in combinations(sub_ranges, 2):
            if c1.overlaps(c2):
                self._results.addMessage(
                    f"'{c1.definedName.name}' and '{c2.definedName.name}' overlap. {context}",
                    Severity.WARNING,
                    MessageType.ExcelParsing,
                )
                return False
        return True


class SimpleNamedRangeTableResolver(NamedRangeTableResolver):
    """Resolves a container named range plus fixed-named sub-ranges (e.g. footnotes)."""

    def __init__(
        self,
        reader: WorkbookReader,
        results: ConversionResultsBuilder,
        *,
        label: str,
        container_name: str,
        required_sub_names: tuple[str, ...],
        optional_sub_names: tuple[str, ...] = (),
        context: str,
    ) -> None:
        super().__init__(reader, results)
        self._label = label
        self._container_name = container_name
        self._required_sub_names = required_sub_names
        self._optional_sub_names = optional_sub_names
        self._context = context

    def resolve(
        self,
    ) -> Optional[tuple[CellRangeMetadata, dict[str, CellRangeMetadata]]]:
        """Return (container_crm, {sub_name: crm}) for the present sub-ranges, or None.

        Returns None silently when nothing is configured, and None with a warning
        when the configuration is present but incomplete or geometrically invalid.
        """
        reader = self._reader
        container_dn = reader.getDefinedName(self._container_name)
        required_dns = {
            name: reader.getDefinedName(name) for name in self._required_sub_names
        }

        # Nothing configured at all -> silently do nothing.
        if container_dn is None and all(d is None for d in required_dns.values()):
            return None

        missing = [
            name
            for name, dn in (
                (self._container_name, container_dn),
                *required_dns.items(),
            )
            if dn is None
        ]
        if missing:
            self._results.addMessage(
                f"{self._label} named ranges are incomplete; missing: "
                f"{', '.join(missing)}. {self._context}",
                Severity.WARNING,
                MessageType.ExcelParsing,
            )
            return None

        container_crm = reader._getCellRangeMetadata(container_dn)
        if container_crm is None:
            return None

        sub_crms: dict[str, CellRangeMetadata] = {}
        for name, dn in required_dns.items():
            crm = reader._getCellRangeMetadata(dn)
            if crm is None:
                return None
            sub_crms[name] = crm

        for name in self._optional_sub_names:
            if (dn := reader.getDefinedName(name)) is not None and (
                crm := reader._getCellRangeMetadata(dn)
            ) is not None:
                sub_crms[name] = crm

        if not self._validate_sub_ranges(
            container_crm, list(sub_crms.values()), self._context
        ):
            return None

        return container_crm, sub_crms


class XBRLNamedRangeTableResolver(NamedRangeTableResolver):
    """Resolves hypercube tables: a table range plus the concept ranges within it.

    Sub-ranges are discovered geometrically (concept ranges that fall within the
    table range) rather than by fixed names, then classified into primary items,
    dimensions and units.
    """

    def __init__(
        self,
        reader: WorkbookReader,
        results: ConversionResultsBuilder,
        taxonomy: Taxonomy,
        concept_map: dict,
        unit_map: dict,
    ) -> None:
        super().__init__(reader, results)
        self._taxonomy = taxonomy
        self._concept_map = concept_map
        self._unit_map = unit_map

    def resolve(
        self,
    ) -> dict[XbrlConceptCellRangeMetadata, XbrlTableCellRangeMetadataHolder]:
        """Build the table_map, popping consumed entries out of concept_map/unit_map."""
        results = self._results
        taxonomy = self._taxonomy
        concept_map = self._concept_map
        unit_map = self._unit_map
        table_map: dict = {}

        tables = [
            (dn, stuff)
            for dn, stuff in concept_map.items()
            if stuff.concept in taxonomy.hypercubes
        ]
        concepts_in_excel = frozenset(stuff.concept for stuff in concept_map.values())
        hc_concepts_in_excel = frozenset(c for c in concepts_in_excel if c.isHypercube)
        used_empty_hypercubes = taxonomy.emptyHypercubes.intersection(
            hc_concepts_in_excel
        )
        if used_empty_hypercubes:
            results.addMessage(
                f"The following hypercubes exist and have corresponding named ranges but they cannot be used due to missing taxonomy definitions: {conceptsToText(used_empty_hypercubes)}.",
                Severity.ERROR,
                MessageType.DevInfo,
            )

        for table, table_stuff in tables:
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
            missing_from_excel = allPermittedConceptsForTable.difference(
                concepts_in_excel
            )
            if missing_from_excel:
                results.addMessage(
                    f"Expected Dimensions or Primary Items for hypercube {table.name} have not been found: {conceptsToText(missing_from_excel)}.",
                    Severity.WARNING,
                    MessageType.DevInfo,
                )

            candidates: list[XbrlConceptCellRangeMetadata] = []
            extras_in_excel: set[XbrlConceptCellRangeMetadata] = set()
            for dn, stuff in concept_map.items():
                if table_stuff.worksheet is not stuff.worksheet:
                    continue
                concept = stuff.concept
                if not (concept.isReportable or concept.isDimension):
                    continue
                if table_stuff.contains(stuff):
                    if concept in allPermittedConceptsForTable:
                        candidates.append(stuff)
                    else:
                        extras_in_excel.add(stuff)
                elif table_stuff.overlaps(stuff):
                    extras_in_excel.add(stuff)

            if extras_in_excel:
                results.addMessage(
                    f"Extra named ranges found within/overlapping bounds of {table.name} named range but not supported by Hypercube {table_stuff.concept.qname}: {extras_in_excel}.",
                    Severity.WARNING,
                    MessageType.DevInfo,
                )

            fishy = False
            for c1, c2 in combinations(candidates, 2):
                disjoint = not c1.overlaps(c2)
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
                units = [
                    u for p in pItems if (u := unit_map.get(p.concept)) is not None
                ]
                table_map[table_stuff] = XbrlTableCellRangeMetadataHolder(
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

        return table_map


class FootnoteTableResolver(NamedRangeTableResolver):
    """Resolves the footnote named ranges into a FootnoteBinding."""

    def resolve(self) -> Optional[FootnoteBinding]:
        resolved = SimpleNamedRangeTableResolver(
            self._reader,
            self._results,
            label="Footnote",
            container_name=_FOOTNOTE_TABLE,
            required_sub_names=(_FOOTNOTE_TEXT, _FOOTNOTE_REF),
            optional_sub_names=(_FOOTNOTE_REF_DIMENSION,),
            context="Footnotes cannot be processed.",
        ).resolve()
        if resolved is None:
            return None
        container, subs = resolved
        return FootnoteBinding(
            table=container,
            text=subs[_FOOTNOTE_TEXT],
            ref=subs[_FOOTNOTE_REF],
            ref_dimension=subs.get(_FOOTNOTE_REF_DIMENSION),
        )
