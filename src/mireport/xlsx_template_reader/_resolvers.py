"""Resolvers that turn workbook named ranges into validated binding holders.

Every resolver takes an `ExcelCellBindingContext` (reader, results, taxonomy) built
once in `build_bindings`, and extends the `BindingResolver` ABC:

* SimpleNamedRangeTableResolver  -- container range + fixed-named sub-ranges
                                    (containment + non-overlap validation).
* FootnoteTableResolver          -- composes the above into a FootnoteBinding.
* XBRLTableResolver              -- resolves one hypercube table into a TableBinding
                                    (build_bindings handles discovery and cleanup).
* ExternalValuesResolver         -- the template_external_values range.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass
from itertools import combinations
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from mireport.taxonomy import Concept, Taxonomy
    from mireport.xlsx_template_reader._reader import WorkbookReader

from mireport.conversionresults import ConversionResultsBuilder, MessageType, Severity
from mireport.xlsx_template_reader._bindings import (
    CellRangeMetadata,
    FootnoteBinding,
    TableBinding,
    XbrlConceptCellRangeMetadata,
)
from mireport.xlsx_template_reader._constants import (
    EXCEL_VALUES_TO_BE_TREATED_AS_NONE_VALUE,
    EXTERNAL_VALUES_RANGE,
)
from mireport.xlsx_template_reader.util import conceptsToText

L = logging.getLogger(__name__)

# Footnote named ranges.
_FOOTNOTE_TABLE = "footnote_table"
_FOOTNOTE_TEXT = "footnote_text"
_FOOTNOTE_REF = "footnote_ref_concept"
_FOOTNOTE_REF_DIMENSION = "footnote_ref_dimension"


@dataclass(frozen=True, slots=True)
class ExcelCellBindingContext:
    """The ambient dependencies every resolver needs, built once in build_bindings."""

    reader: WorkbookReader
    results: ConversionResultsBuilder
    taxonomy: Taxonomy


class BindingResolver(ABC):
    """Base: resolves something from the workbook into a binding, given the context."""

    def __init__(self, ctx: ExcelCellBindingContext) -> None:
        self._ctx = ctx
        self._reader = ctx.reader
        self._results = ctx.results
        self._taxonomy = ctx.taxonomy

    @abstractmethod
    def resolve(self) -> object:
        """Resolve and validate this resolver's named ranges into a binding holder."""
        ...


class SimpleNamedRangeTableResolver(BindingResolver):
    """Resolves a container named range plus fixed-named sub-ranges (e.g. footnotes)."""

    def __init__(
        self,
        ctx: ExcelCellBindingContext,
        *,
        label: str,
        container_name: str,
        required_sub_names: tuple[str, ...],
        optional_sub_names: tuple[str, ...] = (),
        context: str,
    ) -> None:
        super().__init__(ctx)
        self._label = label
        self._container_name = container_name
        self._required_sub_names = required_sub_names
        self._optional_sub_names = optional_sub_names
        self._context = context

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

        if (
            container_dn is None
            or (container_crm := reader._getCellRangeMetadata(container_dn)) is None
        ):
            return None

        sub_crms: dict[str, CellRangeMetadata] = {}
        for name, dn in required_dns.items():
            if dn is None or (crm := reader._getCellRangeMetadata(dn)) is None:
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


class XBRLTableResolver(BindingResolver):
    """Resolves a single hypercube table: the concept ranges within its bounds,
    classified into primary items, dimensions and units."""

    def __init__(
        self,
        ctx: ExcelCellBindingContext,
        unit_map: dict,
        table: XbrlConceptCellRangeMetadata,
        ws_candidates: list[XbrlConceptCellRangeMetadata] | tuple[()],
        concepts_in_excel: frozenset[Concept],
    ) -> None:
        super().__init__(ctx)
        self._unit_map = unit_map
        self._table = table
        self._ws_candidates = ws_candidates
        self._concepts_in_excel = concepts_in_excel

    def resolve(self) -> Optional[TableBinding]:
        """Return the TableBinding, or None if an overlap conflict makes it unusable."""
        results = self._results
        taxonomy = self._taxonomy
        table = self._table
        table_name = table.definedName.name

        permitted = taxonomy.getDimensionsForHypercube(table.concept).union(
            concept
            for concept in taxonomy.getPrimaryItemsForHypercube(table.concept)
            if concept.isReportable or concept.isDimension
        )
        if missing := permitted - self._concepts_in_excel:
            results.addMessage(
                f"Expected Dimensions or Primary Items for hypercube {table_name} have not been found: {conceptsToText(missing)}.",
                Severity.WARNING,
                MessageType.DevInfo,
            )

        candidates: list[XbrlConceptCellRangeMetadata] = []
        extras: set[XbrlConceptCellRangeMetadata] = set()
        for stuff in self._ws_candidates:
            if table.contains(stuff):
                if stuff.concept in permitted:
                    candidates.append(stuff)
                else:
                    extras.add(stuff)
            elif table.overlaps(stuff):
                extras.add(stuff)

        if extras:
            results.addMessage(
                f"Extra named ranges found within/overlapping bounds of {table_name} named range but not supported by Hypercube {table.concept.qname}: {extras}.",
                Severity.WARNING,
                MessageType.DevInfo,
            )

        conflict = next(
            ((a, b) for a, b in combinations(candidates, 2) if a.conflictsWith(b)),
            None,
        )
        if conflict is not None:
            c1, c2 = conflict
            results.addMessage(
                f"Named range (table) {table_name} has named ranges "
                f"(primary items or dimensions) {c1.definedName.name} and "
                f"{c2.definedName.name} that are neither the same nor disjoint. "
                "Ignoring table.",
                Severity.ERROR,
                MessageType.ExcelParsing,
            )
            return None

        unit_map = self._unit_map
        # Independent filters: a non-abstract dimension can be both reportable and a dimension.
        pItems = [c for c in candidates if c.concept.isReportable]
        return TableBinding(
            table=table,
            primaryItems=pItems,
            explicitDimensions=[c for c in candidates if c.concept.isExplicitDimension],
            typedDimensions=[c for c in candidates if c.concept.isTypedDimension],
            units=[u for p in pItems if (u := unit_map.get(p.concept)) is not None],
        )


class ExternalValuesResolver(BindingResolver):
    """Resolves the template_external_values range into the set of concepts whose
    values are supplied externally rather than from the spreadsheet."""

    def resolve(self) -> frozenset[Concept]:
        reader = self._reader
        taxonomy = self._taxonomy
        ext_dn = reader.getDefinedName(EXTERNAL_VALUES_RANGE)
        if ext_dn is None or (crh := reader._createCellRangeMetadata(ext_dn)) is None:
            return frozenset()

        has_external_value: set[Concept] = set()
        for cell in crh.cells():
            if not isinstance(cell.value, str):
                continue
            name_or_label = cell.value.strip()
            if (
                not name_or_label
                or name_or_label in EXCEL_VALUES_TO_BE_TREATED_AS_NONE_VALUE
            ):
                continue
            concept = taxonomy.getConceptForName(
                name_or_label
            ) or taxonomy.getConceptForLabel(name_or_label)
            if concept is None or not concept.isTextblock:
                self._results.addMessage(
                    f"External value specified in {EXTERNAL_VALUES_RANGE} named range but no matching concept found for name or label '{name_or_label}'.",
                    Severity.WARNING,
                    MessageType.DevInfo,
                    excel_reference=crh.excelRef(cell),
                )
                continue
            has_external_value.add(concept)
        return frozenset(has_external_value)


class FootnoteTableResolver(BindingResolver):
    """Resolves the footnote named ranges into a FootnoteBinding."""

    def resolve(self) -> Optional[FootnoteBinding]:
        resolved = SimpleNamedRangeTableResolver(
            self._ctx,
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
