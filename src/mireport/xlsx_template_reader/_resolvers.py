"""Resolvers that turn workbook named ranges into validated binding holders.

All resolvers share an `ExcelCellBindingContext` (reader, msg, taxonomy) built
once in `WorkbookBinder.bind`:

* resolveNamedRangeTable  -- container range + fixed-named sub-ranges
                             (containment + non-overlap validation).
* resolveFootnoteBinding  -- composes the above into a FootnoteBinding.
* resolveExternalValues   -- the template_external_values range.
* XBRLTableResolver       -- classifies the concept ranges within hypercube
                             tables; construct once with the workbook-wide
                             indexes, then resolve(table) per hypercube.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from itertools import combinations
from typing import TYPE_CHECKING, NamedTuple, Optional

if TYPE_CHECKING:
    from openpyxl.worksheet.worksheet import Worksheet

    from mireport.taxonomy import Concept, Taxonomy
    from mireport.xlsx_template_reader._reader import WorkbookReader

from mireport.conversionresults import MessageType
from mireport.exceptions import AmbiguousComponentException
from mireport.xlsx_template_reader._bindings import FootnoteBinding, TableBinding
from mireport.xlsx_template_reader._messages import Messenger
from mireport.xlsx_template_reader._ranges import (
    CellRangeMetadata,
    XbrlConceptCellRangeMetadata,
)
from mireport.xlsx_template_reader._reader import CellValue
from mireport.xlsx_template_reader.util import conceptsToText

L = logging.getLogger(__name__)

# Footnote named ranges.
_FOOTNOTE_TABLE = "footnote_table"
_FOOTNOTE_TEXT = "footnote_text"
_FOOTNOTE_REF = "footnote_ref_concept"
_FOOTNOTE_REF_DIMENSION = "footnote_ref_dimension"

# External values named range.
_EXTERNAL_VALUES_RANGE = "template_external_values"


@dataclass(frozen=True, slots=True)
class ExcelCellBindingContext:
    """The ambient dependencies every resolver needs, built once per bind."""

    reader: WorkbookReader
    msg: Messenger
    taxonomy: Taxonomy


def _validateSubRanges(
    msg: Messenger,
    table: CellRangeMetadata,
    sub_ranges: list[CellRangeMetadata],
    context: str,
) -> bool:
    """False (with a warning) if any sub-range is outside the table or two overlap."""
    for crm in sub_ranges:
        if not table.contains(crm):
            msg.warning(
                f"'{crm.definedName.name}' is not fully contained within "
                f"'{table.definedName.name}'. {context}",
                MessageType.ExcelParsing,
            )
            return False
    for c1, c2 in combinations(sub_ranges, 2):
        if c1.overlaps(c2):
            msg.warning(
                f"'{c1.definedName.name}' and '{c2.definedName.name}' overlap. {context}",
                MessageType.ExcelParsing,
            )
            return False
    return True


class ResolvedNamedRangeTable(NamedTuple):
    """A container named range plus its resolved sub-ranges, keyed by name."""

    container: CellRangeMetadata
    subRanges: dict[str, CellRangeMetadata]


def resolveNamedRangeTable(
    ctx: ExcelCellBindingContext,
    *,
    label: str,
    container_name: str,
    required_sub_names: tuple[str, ...],
    optional_sub_names: tuple[str, ...] = (),
    context: str,
) -> Optional[ResolvedNamedRangeTable]:
    """Resolve a container named range plus fixed-named sub-ranges (e.g. footnotes).

    Returns None silently when nothing is configured, and None with a warning
    when the configuration is present but incomplete or geometrically invalid.
    """
    reader = ctx.reader
    container_dn = reader.getDefinedName(container_name)
    required_dns = {name: reader.getDefinedName(name) for name in required_sub_names}

    # Nothing configured at all -> silently do nothing.
    if container_dn is None and all(d is None for d in required_dns.values()):
        return None

    missing = [
        name
        for name, dn in ((container_name, container_dn), *required_dns.items())
        if dn is None
    ]
    if missing:
        ctx.msg.warning(
            f"{label} named ranges are incomplete; missing: "
            f"{', '.join(missing)}. {context}",
            MessageType.ExcelParsing,
        )
        return None

    if (
        container_dn is None
        or (container_crm := reader.resolveRange(container_dn)) is None
    ):
        return None

    sub_crms: dict[str, CellRangeMetadata] = {}
    for name, dn in required_dns.items():
        if dn is None or (crm := reader.resolveRange(dn)) is None:
            return None
        sub_crms[name] = crm

    for name in optional_sub_names:
        if (crm := reader.resolveRange(name)) is not None:
            sub_crms[name] = crm

    if not _validateSubRanges(ctx.msg, container_crm, list(sub_crms.values()), context):
        return None

    return ResolvedNamedRangeTable(container_crm, sub_crms)


def resolveFootnoteBinding(ctx: ExcelCellBindingContext) -> Optional[FootnoteBinding]:
    """Resolve the footnote named ranges into a FootnoteBinding."""
    resolved = resolveNamedRangeTable(
        ctx,
        label="Footnote",
        container_name=_FOOTNOTE_TABLE,
        required_sub_names=(_FOOTNOTE_TEXT, _FOOTNOTE_REF),
        optional_sub_names=(_FOOTNOTE_REF_DIMENSION,),
        context="Footnotes cannot be processed.",
    )
    if resolved is None:
        return None
    binding = FootnoteBinding(
        table=resolved.container,
        text=resolved.subRanges[_FOOTNOTE_TEXT],
        ref=resolved.subRanges[_FOOTNOTE_REF],
        ref_dimension=resolved.subRanges.get(_FOOTNOTE_REF_DIMENSION),
    )
    # The footnote reader only supports single-column sub-ranges; wider ones
    # fall back to their first column.
    for crm in (binding.text, binding.ref, binding.ref_dimension):
        if crm is not None and crm.maximum_width > 1:
            ctx.msg.warning(
                f"Footnote named range '{crm.definedName.name}' is "
                f"{crm.maximum_width} columns wide but only single-column "
                "footnote ranges are supported; only the first column will "
                "be used.",
                MessageType.ExcelParsing,
                ref=crm.excelRef(),
            )
    return binding


def resolveExternalValues(ctx: ExcelCellBindingContext) -> frozenset[Concept]:
    """Resolve the template_external_values range into the set of concepts whose
    values are supplied externally rather than from the spreadsheet."""
    taxonomy = ctx.taxonomy
    if (crh := ctx.reader.resolveRange(_EXTERNAL_VALUES_RANGE)) is None:
        return frozenset()

    has_external_value: set[Concept] = set()
    for cell in crh.cells():
        if (value := CellValue.fromCell(cell)).isBlank:
            continue
        name_or_label = value.as_str_stripped()
        try:
            concept = taxonomy.resolveConcept(
                name_or_label, by_label=True, by_name=True, only_reportable=True
            )
        except AmbiguousComponentException as exc:
            ctx.msg.warning(
                f"External value '{name_or_label}' in {_EXTERNAL_VALUES_RANGE} named range is ambiguous: {exc}",
                MessageType.DevInfo,
                ref=crh.excelRef(cell),
            )
            continue
        if concept is None or not concept.isTextblock:
            ctx.msg.warning(
                f"External value specified in {_EXTERNAL_VALUES_RANGE} named range but no matching concept found for name or label '{name_or_label}'.",
                MessageType.DevInfo,
                ref=crh.excelRef(cell),
            )
            continue
        has_external_value.add(concept)
    return frozenset(has_external_value)


class XBRLTableResolver:
    """Classifies the concept ranges within hypercube tables into primary items,
    dimensions and units.

    Construct once with the workbook-wide indexes; call resolve(table) for each
    hypercube table range.
    """

    def __init__(
        self,
        ctx: ExcelCellBindingContext,
        unit_map: dict[Concept, XbrlConceptCellRangeMetadata],
        candidates_by_ws: dict[Worksheet, list[XbrlConceptCellRangeMetadata]],
        concepts_in_excel: frozenset[Concept],
    ) -> None:
        self._ctx = ctx
        self._unit_map = unit_map
        self._candidates_by_ws = candidates_by_ws
        self._concepts_in_excel = concepts_in_excel

    def resolve(self, table: XbrlConceptCellRangeMetadata) -> Optional[TableBinding]:
        """Return the TableBinding, or None if an overlap conflict makes it unusable."""
        msg = self._ctx.msg
        taxonomy = self._ctx.taxonomy
        table_name = table.definedName.name

        permitted = taxonomy.getDimensionsForHypercube(table.concept).union(
            concept
            for concept in taxonomy.getPrimaryItemsForHypercube(table.concept)
            if concept.isReportable or concept.isDimension
        )
        if missing := permitted - self._concepts_in_excel:
            msg.warning(
                f"Expected Dimensions or Primary Items for hypercube {table_name} have not been found: {conceptsToText(missing)}.",
                MessageType.DevInfo,
            )

        candidates: list[XbrlConceptCellRangeMetadata] = []
        extras: set[XbrlConceptCellRangeMetadata] = set()
        for crm in self._candidates_by_ws.get(table.worksheet, ()):
            if table.contains(crm):
                if crm.concept in permitted:
                    candidates.append(crm)
                else:
                    extras.add(crm)
            elif table.overlaps(crm):
                extras.add(crm)

        if extras:
            msg.warning(
                f"Extra named ranges found within/overlapping bounds of {table_name} named range but not supported by Hypercube {table.concept.qname}: {extras}.",
                MessageType.DevInfo,
            )

        conflict = next(
            ((a, b) for a, b in combinations(candidates, 2) if a.conflictsWith(b)),
            None,
        )
        if conflict is not None:
            c1, c2 = conflict
            msg.error(
                f"Named range (table) {table_name} has named ranges "
                f"(primary items or dimensions) {c1.definedName.name} and "
                f"{c2.definedName.name} that are neither the same nor disjoint. "
                "Ignoring table.",
                MessageType.ExcelParsing,
            )
            return None

        # Independent filters: a non-abstract dimension can be both reportable and a dimension.
        pItems = [c for c in candidates if c.concept.isReportable]
        return TableBinding(
            table=table,
            primaryItems=pItems,
            explicitDimensions=[c for c in candidates if c.concept.isExplicitDimension],
            typedDimensions=[c for c in candidates if c.concept.isTypedDimension],
            units=[
                u for p in pItems if (u := self._unit_map.get(p.concept)) is not None
            ],
        )
