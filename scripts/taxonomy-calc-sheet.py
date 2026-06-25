#!/usr/bin/env python3
"""
Dump taxonomy presentation groups to an Excel workbook.

Each presentation group gets its own worksheet.  Concepts that participate in
the calculation linkbase are highlighted by role and cross-sheet involvement:
  light green  — leaf contributor, in-sheet only
  medium green — total / sub-total, in-sheet only
  yellow       — cross-sheet calculation only
  green+hatch  — in-sheet AND cross-sheet (hatch overlay signals the cross risk)

Orphan calc groups (no matching presentation roleUri) that have 100% concept
coverage within a presentation group are overlaid onto that group's sheet and
highlighted as if they were in-sheet calculations.
"""

import argparse
import re
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, NamedTuple, Self

import xlsxwriter
from xlsxwriter import Workbook
from xlsxwriter.worksheet import Worksheet

import mireport
from mireport.excelprocessor import VSME_DEFAULTS
from mireport.taxonomy import (
    CalculationGroup,
    Concept,
    PresentationGroup,
    PresentationRelationship,
    Taxonomy,
    getTaxonomy,
    listTaxonomies,
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

HEADERS = [
    "Calc weight",
    "Calculation Depth",
    "Presentation Depth",
    "Label",
    "QName",
    "Data Type",
    "Period",
    "Abstract",
    "Documentation",
    "Calculation component kind",
]

COL_WIDTHS = [20, 6, 6, 50, 40, 25, 10, 8, 60, 22]  # matches HEADERS order; cols B+C are hidden

_ROW_TITLE  = 0  # full group label
_ROW_HEADER = 1  # column headers
_ROW_DATA   = 2  # first data row

INVALID_SHEET_CHARS = re.compile(r"[\[\]:*?/\\]")

# ---------------------------------------------------------------------------
# Calc-kind types
# ---------------------------------------------------------------------------


class CalcKind(Enum):
    NONE = "none"
    LEAF = "leaf"  # in-sheet, leaf contributor
    TOTAL = "total"  # in-sheet, total/sub-total
    CROSS = "cross"  # cross-sheet only
    LEAF_CROSS = "leaf_cross"  # in-sheet leaf + cross-sheet
    TOTAL_CROSS_ALL = "total_cross_all"  # in-sheet total + cross-sheet
    TOTAL_CROSS_SOME = (
        "total_cross_some"  # in-sheet total, but ≥1 leaf contributor appears elsewhere
    )


class CalcStyle(NamedTuple):
    bg: str | None  # background colour hex, or None
    pattern: int = 1  # xlsxwriter fill pattern; 1 = solid, 7 = darkDown, 8 = darkUp
    fg: str | None = None  # pattern foreground colour (used when pattern != 1)
    border: bool = False  # thin black top + bottom border


class CalcInfo(NamedTuple):
    kind: CalcKind
    weight: str  # "=" root, "+" additive, "-" subtractive
    depth: int   # depth in the calculation group


class GroupCoverage(NamedTuple):
    kind_counts: Counter[CalcKind]
    primary_calc_not_in_pres: frozenset[Concept]
    overlays_applied: tuple[str, ...]


class ConceptAppearance(NamedTuple):
    sheet_name: str
    row_idx: int  # xlsxwriter 0-based; _ROW_TITLE=title, _ROW_HEADER=header, _ROW_DATA+=data
    label: str    # full presentation group label for hyperlink display text

    @property
    def excel_row(self) -> int:
        """1-based Excel row number (title = 1, header = 2, first data row = 3)."""
        return self.row_idx + 1

    @property
    def url(self) -> str:
        """Internal hyperlink URL to column A of this row."""
        return f"internal:'{self.sheet_name}'!A{self.excel_row}"


_CALC_DESC: dict[CalcKind, str] = {
    CalcKind.NONE: "no calc involvement",
    CalcKind.LEAF: "in-group contributor to a total/sub-total, with NO contribution to calculations in other groups",
    CalcKind.TOTAL: "in-group total/sub-total with NO contribution to calculations in other groups",
    CalcKind.CROSS: "Reported here (with no calculation contribution) but DOES contribute to calculations in other groups",
    CalcKind.LEAF_CROSS: "in-group calculation contributor which ALSO contributes to calculations in other groups",
    CalcKind.TOTAL_CROSS_ALL: "in-group total/sub-total with ALL contributors also contributing to calculations in other groups",
    CalcKind.TOTAL_CROSS_SOME: "in-group total/sub-total with a MIXTURE of some in-group only contributors and some in-group contributors that also contribute to calculations in other groups",
}

_CALC_STYLE: dict[CalcKind, CalcStyle] = {
    CalcKind.NONE: CalcStyle(bg=None),
    CalcKind.LEAF: CalcStyle(bg="#C6EFCE"),  # solid light green
    CalcKind.TOTAL: CalcStyle(bg="#70AD47", border=True),  # solid dark green
    CalcKind.CROSS: CalcStyle(bg="#FFEB9C"),  # solid yellow
    CalcKind.LEAF_CROSS: CalcStyle(
        bg="#C6EFCE", pattern=7, fg="#F4B942"
    ),  # light green \ amber
    CalcKind.TOTAL_CROSS_ALL: CalcStyle(
        bg="#70AD47", pattern=7, fg="#FFEB9C", border=True
    ),  # dark green \ yellow
    CalcKind.TOTAL_CROSS_SOME: CalcStyle(
        bg="#70AD47", pattern=8, fg="#FFFFFF", border=True
    ),  # dark green / white
}

_CROSS_KINDS: frozenset[CalcKind] = frozenset(
    {
        CalcKind.LEAF_CROSS,
        CalcKind.TOTAL_CROSS_ALL,
        CalcKind.TOTAL_CROSS_SOME,
        CalcKind.CROSS,
    }
)

# ---------------------------------------------------------------------------
# Worksheet-name helpers
# ---------------------------------------------------------------------------


def _label(rel_or_concept: PresentationRelationship | Concept) -> str:
    if isinstance(rel_or_concept, PresentationRelationship):
        return rel_or_concept.getLabel(removeSuffix = False, fallbackToQName=True)
    return rel_or_concept.getStandardLabel(removeSuffix = False, fallbackToQName=True)


def _sanitise_sheet_name(name: str, used: set[str]) -> str:
    name = INVALID_SHEET_CHARS.sub("", name).strip()
    candidate = name[:31]
    i = 2
    while candidate.lower() in used:
        suffix = f" {i}"
        candidate = name[: 31 - len(suffix)] + suffix
        i += 1
    used.add(candidate.lower())
    return candidate


# ---------------------------------------------------------------------------
# Format factory
# ---------------------------------------------------------------------------


class Formats:
    """Pre-built XlsxWriter format objects."""

    def __init__(self, wb: Workbook) -> None:
        self._wb = wb
        self._cache: dict[tuple, Any] = {}

        self.header = self._make_fmt(
            bold=True,
            style=CalcStyle(bg="#4472C4"),
            align="center",
            font_color="#FFFFFF",
            text_wrap=True,
        )
        self.link = self._make_fmt(underline=True, font_color="#0563C1")

    def _make_fmt(
        self,
        *,
        bold: bool = False,
        italic: bool = False,
        style: CalcStyle = CalcStyle(bg=None),
        indent: int = 0,
        align: str | None = None,
        underline: bool = False,
        font_color: str | None = None,
        text_wrap: bool = False,
    ) -> Any:
        props: dict[str, Any] = {
            "text_wrap": text_wrap,
            "bold": bold,
            "italic": italic,
            "underline": underline,
        }
        if font_color:
            props["font_color"] = font_color
        if style.bg:
            props["bg_color"] = style.bg
            if style.pattern != 1:
                props["pattern"] = style.pattern
            if style.fg:
                props["fg_color"] = style.fg
        if indent:
            props["indent"] = indent
        if align:
            props["align"] = align
        if style.border:
            props["top"] = 1
            props["bottom"] = 1
        return self._wb.add_format(props)

    def row_fmt(
        self,
        *,
        depth: int = 0,
        italic: bool = False,
        style: CalcStyle = CalcStyle(bg=None),
        for_label: bool = False,
        text_wrap: bool = False,
    ) -> Any:
        """Return (cached) format for a data cell."""
        key = (depth if for_label else 0, italic, style, for_label, text_wrap)
        if key not in self._cache:
            self._cache[key] = self._make_fmt(
                italic=italic,
                style=style,
                indent=depth if for_label else 0,
                text_wrap=text_wrap,
            )
        return self._cache[key]


# ---------------------------------------------------------------------------
# Calc group classification
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class CalcGroupClassification:
    """Classification of calculation groups relative to presentation groups.

    Separates calc groups into primaries (matching a pres role), overlays
    (orphan calc groups whose concepts are 100% contained in a pres group),
    and unmatched orphans.  Also pre-computes derived lookups used by the
    coverage report.
    """

    overlays: dict[str, frozenset[CalculationGroup]]
    """pres roleUri → orphan calc groups overlaid onto that presentation group."""

    primaries: dict[str, CalculationGroup]
    """pres roleUri → primary calc group (only for roles that have one)."""

    unmatched: list[CalculationGroup]
    """Orphan calc groups with no 100%-match presentation group."""

    pres_labels: dict[str, str]
    """pres roleUri → group label."""

    concept_to_pres_roles: dict[Concept, frozenset[str]]
    """Concept → frozenset of pres roleUris where it appears."""

    orphan_calc_concepts: frozenset[Concept]
    """All concepts from non-primary (orphan) calc groups."""

    @classmethod
    def from_taxonomy(cls, taxonomy: Taxonomy) -> Self:
        groups = taxonomy.presentation
        pres_roles = frozenset(g.roleUri for g in groups)
        pres_concepts: dict[str, frozenset[Concept]] = {
            g.roleUri: frozenset(rel.concept for rel in g.relationships)
            for g in groups
        }

        primaries: dict[str, CalculationGroup] = {}
        orphans: list[CalculationGroup] = []
        for g in taxonomy.calculation:
            if g.roleUri in pres_roles:
                primaries[g.roleUri] = g
            else:
                orphans.append(g)

        overlay_lists: defaultdict[str, list[CalculationGroup]] = defaultdict(list)
        unmatched: list[CalculationGroup] = []
        for ocalc in orphans:
            calc_concepts = frozenset(rel.concept for rel in ocalc.relationships)
            matched_roles = [
                r for r, pcs in pres_concepts.items() if calc_concepts <= pcs
            ]
            if matched_roles:
                for r in matched_roles:
                    overlay_lists[r].append(ocalc)
            else:
                unmatched.append(ocalc)
        overlays = {r: frozenset(v) for r, v in overlay_lists.items()}

        # Derived lookups
        pres_labels = {g.roleUri: g.getLabel() for g in groups}

        c2r: dict[Concept, set[str]] = {}
        for role_uri, concepts in pres_concepts.items():
            for concept in concepts:
                c2r.setdefault(concept, set()).add(role_uri)
        concept_to_pres_roles = {c: frozenset(rs) for c, rs in c2r.items()}

        orphan_calc_concepts = frozenset(
            rel.concept for o in orphans for rel in o.relationships
        )

        return cls(
            overlays=overlays,
            primaries=primaries,
            unmatched=unmatched,
            pres_labels=pres_labels,
            concept_to_pres_roles=concept_to_pres_roles,
            orphan_calc_concepts=orphan_calc_concepts,
        )


# ---------------------------------------------------------------------------
# Per-group calc-kind lookup
# ---------------------------------------------------------------------------


def _build_calc_map(
    primary: CalculationGroup | None,
    taxonomy: Taxonomy,
    overlay_groups: frozenset[CalculationGroup],
) -> tuple[dict[Concept, CalcInfo], frozenset[CalculationGroup]]:
    """Return (concept → CalcInfo, own_groups) for a presentation group + any overlays.

    own_groups is the set of CalculationGroups treated as "in-sheet" for cross-detection.
    Only in-sheet calc group concepts are included in the CalcInfo map.
    """
    own_groups = overlay_groups | (
        frozenset((primary,)) if primary is not None else frozenset()
    )

    in_sheet_totals: set[Concept] = set()
    in_sheet_leaves: set[Concept] = set()
    weight_map: dict[Concept, str] = {}
    depth_map: dict[Concept, int] = {}
    for calc_group in own_groups:
        rels = calc_group.relationships
        for i, rel in enumerate(rels):
            has_child = i + 1 < len(rels) and rels[i + 1].depth > rel.depth
            (in_sheet_totals if has_child else in_sheet_leaves).add(rel.concept)
            if rel.concept not in weight_map:
                if rel.weight is None:
                    weight_map[rel.concept] = "="
                elif rel.weight == "+1":
                    weight_map[rel.concept] = "+"
                else:
                    weight_map[rel.concept] = "-"
                depth_map[rel.concept] = rel.depth

    kind_map: dict[Concept, CalcKind] = {}
    for concept in in_sheet_totals | in_sheet_leaves:
        cross = bool(taxonomy.getCalculationGroupsForConcept(concept) - own_groups)
        match (concept in in_sheet_totals, cross):
            case (True, True):
                kind_map[concept] = CalcKind.TOTAL_CROSS_ALL
            case (True, False):
                kind_map[concept] = CalcKind.TOTAL
            case (False, True):
                kind_map[concept] = CalcKind.LEAF_CROSS
            case _:
                kind_map[concept] = CalcKind.LEAF

    for calc_group in own_groups:
        rels = calc_group.relationships
        for i, rel in enumerate(rels):
            has_child = i + 1 < len(rels) and rels[i + 1].depth > rel.depth
            if not has_child or kind_map.get(rel.concept) != CalcKind.TOTAL:
                continue
            parent_depth = rel.depth
            for child_rel in rels[i + 1 :]:
                if child_rel.depth <= parent_depth:
                    break
                if (
                    child_rel.depth == parent_depth + 1
                    and kind_map.get(child_rel.concept) == CalcKind.LEAF_CROSS
                ):
                    kind_map[rel.concept] = CalcKind.TOTAL_CROSS_SOME
                    break

    result: dict[Concept, CalcInfo] = {
        concept: CalcInfo(kind=kind_map[concept], weight=weight_map[concept], depth=depth_map[concept])
        for concept in kind_map
    }
    return result, own_groups


# ---------------------------------------------------------------------------
# Sheet writers
# ---------------------------------------------------------------------------


def _write_index(
    ws: Worksheet,
    fmts: Formats,
    groups: tuple[PresentationGroup, ...],
    sheet_names: list[str],
    coverages: list[GroupCoverage],
) -> None:
    index_headers = [
        "#",
        "Label",
        "Role URI",
        "Concept count",
        "Calculations (internal)",
        "Calculations (cross-sheet)",
        "Calculations (internal with external links)",
    ]
    ws.write_row(0, 0, index_headers, fmts.header)
    ws.autofilter(0, 0, 0, len(index_headers) - 1)
    ws.freeze_panes(1, 0)
    ws.set_row(0, 50)  # taller header row for wrapped text
    ws.set_column(0, 0, 5)
    ws.set_column(1, 1, 100)
    ws.set_column(2, 2, 55)
    ws.set_column(3, 3, 12)
    ws.set_column(4, 6, 17)

    plain = fmts.row_fmt()

    for i, (group, sheet_name, cov) in enumerate(
        zip(groups, sheet_names, coverages), start=1
    ):
        concept_count = sum(1 for r in group.relationships if not r.concept.isAbstract)
        int_ext = cov.kind_counts[CalcKind.TOTAL_CROSS_SOME]
        internal = cov.kind_counts[CalcKind.TOTAL]
        cross = cov.kind_counts[CalcKind.TOTAL_CROSS_ALL]
        ws.write(i, 0, i, plain)
        ws.write_url(i, 1, f"internal:'{sheet_name}'!A1", fmts.link, group.getLabel())
        ws.write(i, 2, group.roleUri, plain)
        ws.write_row(i, 3, [concept_count, internal, cross, int_ext], plain)


def _write_group_sheet(
    ws: Worksheet,
    fmts: Formats,
    pres_group: PresentationGroup,
    taxonomy: Taxonomy,
    sheet_name: str,
    concept_appearances: dict[Concept, list[ConceptAppearance]],
    calc_cls: CalcGroupClassification,
) -> GroupCoverage:
    overlay_groups = calc_cls.overlays.get(pres_group.roleUri, frozenset())
    calc_group = calc_cls.primaries.get(pres_group.roleUri)
    calc_info_map, own_groups = _build_calc_map(calc_group, taxonomy, overlay_groups)
    stats: Counter[CalcKind] = Counter()

    def _kind(concept: Concept) -> CalcKind:
        info = calc_info_map.get(concept)
        if info is not None:
            return info.kind
        if taxonomy.getCalculationGroupsForConcept(concept) - own_groups:
            return CalcKind.CROSS
        return CalcKind.NONE

    # Pre-scan to find max "Also in" columns needed for CROSS-kind concepts on this sheet
    max_also_in = 0
    for rel in pres_group.relationships:
        if _kind(rel.concept) in _CROSS_KINDS:
            other_count = sum(
                1
                for a in concept_appearances.get(rel.concept, ())
                if a.sheet_name != sheet_name
            )
            max_also_in = max(max_also_in, other_count)

    also_in_headers = [f"Also in {i}" for i in range(1, max_also_in + 1)]
    ws.write(_ROW_TITLE, 0, pres_group.getLabel(), fmts._make_fmt(bold=True))
    ws.set_row(_ROW_TITLE, 20)
    ws.write_row(_ROW_HEADER, 0, HEADERS + also_in_headers, fmts.header)
    ws.freeze_panes(_ROW_DATA, 0)
    ws.set_row(_ROW_HEADER, 50)  # taller header row for wrapped text

    for col, width in enumerate(COL_WIDTHS):
        ws.set_column(col, col, width)
    # Hide cols B+C (indices 1+2 = calc depth and presentation depth integers)
    ws.set_column(1, 2, COL_WIDTHS[1], None, {"hidden": True})
    if max_also_in:
        ws.set_column(10, 10 + max_also_in - 1, 35)

    for row_idx, rel in enumerate(pres_group.relationships, start=_ROW_DATA):
        concept = rel.concept
        kind = _kind(concept)
        stats[kind] += 1
        style = _CALC_STYLE[kind]
        italic = concept.isAbstract

        label_fmt = fmts.row_fmt(
            depth=rel.depth, italic=italic, style=style, for_label=True
        )
        rest_fmt = fmts.row_fmt(italic=italic, style=style)

        label = _label(rel)
        dtype = "" if concept.isAbstract else concept.dataType.localName
        period = concept.periodType.value
        abstract = "Yes" if concept.isAbstract else ""
        doc = concept.getDocumentationLabel() or ""

        calc_info = calc_info_map.get(concept)
        weight_fmt = fmts.row_fmt(
            depth=calc_info.depth if calc_info else 0, italic=italic, style=style, for_label=True
        )
        ws.write_string(row_idx, 0, calc_info.weight if calc_info else "", weight_fmt)
        ws.write(row_idx, 1, calc_info.depth if calc_info else "", rest_fmt)
        ws.write(row_idx, 2, rel.depth, rest_fmt)
        ws.write(row_idx, 3, label, label_fmt)
        ws.write_row(
            row_idx, 4, [str(concept.qname), dtype, period, abstract, doc], rest_fmt
        )
        ws.write(row_idx, 9, kind.name if kind != CalcKind.NONE else "", rest_fmt)

        if kind in _CROSS_KINDS:
            others = [
                a
                for a in concept_appearances.get(concept, ())
                if a.sheet_name != sheet_name
            ]
            for col_offset, a in enumerate(others):
                ws.write_url(row_idx, 10 + col_offset, a.url, fmts.link, a.label)

    # Capture primary calc concepts absent from this presentation group
    primary_calc_concepts: frozenset[Concept] = (
        frozenset(rel.concept for rel in calc_group.relationships)
        if calc_group is not None
        else frozenset()
    )
    pres_concept_set = frozenset(rel.concept for rel in pres_group.relationships)
    primary_calc_not_in_pres = primary_calc_concepts - pres_concept_set

    return GroupCoverage(
        kind_counts=stats,
        primary_calc_not_in_pres=primary_calc_not_in_pres,
        overlays_applied=tuple(g.getLabel() for g in overlay_groups),
    )


def _write_concepts_sheet(
    ws: Worksheet,
    fmts: Formats,
    concept_appearances: dict[Concept, list[ConceptAppearance]],
    taxonomy: Taxonomy,
) -> None:
    max_appearances = max((len(v) for v in concept_appearances.values()), default=0)
    appearance_headers = [f"Appears in {i}" for i in range(1, max_appearances + 1)]
    concept_headers = [
        "Label",
        "QName",
        "Data Type",
        "Period",
        "Documentation",
        "# Calcs",
    ]
    ws.write_row(0, 0, concept_headers + appearance_headers, fmts.header)
    ws.autofilter(0, 0, 0, len(concept_headers) + len(appearance_headers) - 1)
    ws.freeze_panes(1, 0)

    ws.set_column(0, 0, 50)
    ws.set_column(1, 1, 40)
    ws.set_column(2, 2, 25)
    ws.set_column(3, 3, 10)
    ws.set_column(4, 4, 60)
    ws.set_column(5, 5, 8)
    if max_appearances:
        ws.set_column(6, 6 + max_appearances - 1, 35)

    plain = fmts.row_fmt()

    sorted_concepts = sorted(
        concept_appearances.items(),
        key=lambda kv: (_label(kv[0]), kv[0].qname),
    )

    for row_idx, (concept, appearances) in enumerate(sorted_concepts, start=1):
        label = _label(concept)
        dtype = concept.dataType.localName
        period = concept.periodType.value
        doc = concept.getDocumentationLabel() or ""
        num_calcs = (
            len(taxonomy.getCalculationGroupsForConcept(concept))
            if concept.isNumeric
            else ""
        )

        ws.write_row(
            row_idx,
            0,
            [label, str(concept.qname), dtype, period, doc, num_calcs],
            plain,
        )

        for col_offset, a in enumerate(appearances):
            ws.write_url(row_idx, 6 + col_offset, a.url, fmts.link, a.label)


# ---------------------------------------------------------------------------
# Guidance sheet
# ---------------------------------------------------------------------------


def _write_guidance_sheet(ws: Worksheet, fmts: Formats) -> None:
    ws.set_column(0, 0, 4)  # colour swatch
    ws.set_column(1, 1, 25)  # label
    ws.set_column(2, 2, 150)  # description

    wrapped = fmts.row_fmt(text_wrap=True)
    bold = fmts._make_fmt(bold=True)

    # --- Sheet descriptions ---
    ws.merge_range(0, 0, 0, 2, "Sheet Types", fmts.header)

    sheet_descs = [
        ("Guidance", "This sheet."),
        (
            "Index",
            "One row per presentation group. Shows concept count and a breakdown of "
            "calculation totals by kind. Click a group label to jump to its sheet.",
        ),
        (
            "All concepts",
            "All concepts in the taxonomy, sorted alphabetically. Hyperlinks in the"
            " 'Appears in' columns jump to the concept's row on each group sheet.",
        ),
        (
            "Group sheets",
            "One sheet per presentation group, in presentation order. Row 1 contains "
            "the full group label; row 2 is the column header. Concepts are highlighted "
            "by their role in the calculation linkbase (see legend below). "
            "Col A (Calc weight) shows the concept's role in the calculation hierarchy: "
            "'=' root/total, '+' additive contributor, '-' subtractive contributor. "
            "Hidden cols B and C (Calculation Depth, Presentation Depth) hold the "
            "indent levels as integers for filtering. "
            "Rows with a CROSS calc kind have 'Also in' hyperlink columns (col K+) "
            "that jump to the concept's row on each other sheet where it appears.",
        ),
    ]
    for i, (name, desc) in enumerate(sheet_descs, start=2):
        ws.write(i, 1, name, bold)
        ws.write(i, 2, desc, wrapped)

    # --- Colour legend ---
    ws.merge_range(8, 0, 8, 2, "Calculation Row Colours", fmts.header)
    ws.write(9, 1, "Calc kind", bold)
    ws.write(9, 2, "Meaning", bold)

    for i, kind in enumerate(k for k in CalcKind if k != CalcKind.NONE):
        row = 10 + i
        style = _CALC_STYLE[kind]
        ws.write(row, 0, "", fmts._make_fmt(style=style))
        ws.write(row, 1, kind.name, fmts.row_fmt(style=style))
        ws.write(row, 2, _CALC_DESC[kind], fmts.row_fmt(style=style))


# ---------------------------------------------------------------------------
# Calc coverage check
# ---------------------------------------------------------------------------


def _print_calc_coverage(
    calc_cls: CalcGroupClassification,
    coverage_by_group: list[tuple[str, GroupCoverage]],
    concept_appearances: dict[Concept, list[ConceptAppearance]],
) -> None:
    """Print a defensive summary of calc relationships not covered by the presentation."""
    presented_concepts = frozenset(concept_appearances.keys())

    # Concepts in any calc group but appearing in no presentation group at all
    all_primary_calc_concepts: set[Concept] = set()
    for _, cov in coverage_by_group:
        all_primary_calc_concepts.update(cov.primary_calc_not_in_pres)
    invisible_concepts = (
        all_primary_calc_concepts | calc_cls.orphan_calc_concepts
    ) - presented_concepts

    # Matched calc groups with concepts outside their presentation sheet
    partial_groups: list[tuple[str, list[Concept], list[Concept]]] = []
    for group_label, cov in coverage_by_group:
        if not cov.primary_calc_not_in_pres:
            continue
        cross_only = sorted(
            c for c in cov.primary_calc_not_in_pres if c in presented_concepts
        )
        truly_missing = sorted(
            c for c in cov.primary_calc_not_in_pres if c not in presented_concepts
        )
        partial_groups.append((group_label, cross_only, truly_missing))

    print("\nCalculation coverage check:")

    if not calc_cls.unmatched and not invisible_concepts and not partial_groups:
        print("  OK — all calc relationships are covered by the presentation.")
        return

    if calc_cls.unmatched:
        print(
            f"\n  [!] {len(calc_cls.unmatched)} calc group(s) have no matching presentation group"
            " — suggested overlays by concept overlap:"
        )
        for g in calc_cls.unmatched:
            calc_concepts = frozenset(rel.concept for rel in g.relationships)
            total = len(calc_concepts)
            overlap: Counter[str] = Counter()
            for concept in calc_concepts:
                for role_uri in calc_cls.concept_to_pres_roles.get(concept, frozenset()):
                    overlap[role_uri] += 1
            print(f"      {g.getLabel()!r}  ({total} concepts)")
            if overlap:
                for role_uri, count in overlap.most_common(3):
                    pct = count * 100 // total
                    print(
                        f"        → {pct:>3}% match  {calc_cls.pres_labels[role_uri]!r}"
                    )
            else:
                print("        → no concepts appear in any presentation group")

    if invisible_concepts:
        print(
            f"\n  [!] {len(invisible_concepts)} calc concept(s) appear in no presentation group:"
        )
        for c in sorted(invisible_concepts):
            print(f"        {_label(c)}")

    if partial_groups:
        print(
            f"\n  [~] {len(partial_groups)} matched calc group(s) have concepts outside their"
            " presentation sheet:"
        )
        for label, cross_only, truly_missing in partial_groups:
            print(f"      {label!r}")
            for c in cross_only:
                print(f"        [cross]   {_label(c)}")
            for c in truly_missing:
                print(f"        [missing] {_label(c)}")


# ---------------------------------------------------------------------------
# Entry-point selection (same interactive logic as dump-taxonomy.py)
# ---------------------------------------------------------------------------


def _select_entry_point() -> str:
    default = VSME_DEFAULTS["taxonomyEntryPoints"]["supportedEntryPoint"]
    available = {
        str(num): ep for num, ep in enumerate(sorted(listTaxonomies()), start=1)
    }
    print(
        "Available taxonomies:",
        *[f"  {num}: {url}" for num, url in available.items()],
        sep="\n",
    )
    choice = input(
        f"Entry point number/URL or leave blank for default [{default}]: "
    ).strip()
    if not choice:
        return default
    if choice in available:
        return available[choice]
    if choice in available.values():
        return choice
    raise SystemExit(f"Unknown entry point: {choice!r}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "output",
        nargs="?",
        default="taxonomy.xlsx",
        help="Output .xlsx path (default: taxonomy.xlsx)",
    )
    args = parser.parse_args()

    if Path(args.output).exists():
        print(
            f"Warning: {args.output} already exists and will be overwritten.",
            file=sys.stderr,
        )

    mireport.loadTaxonomyJSON()
    entry_point = _select_entry_point()
    taxonomy = getTaxonomy(entry_point)

    groups = taxonomy.presentation
    print(f"Loaded {len(groups)} presentation groups from {entry_point}")

    # Pre-compute overlay assignments and data needed for the coverage report
    calc_cls = CalcGroupClassification.from_taxonomy(taxonomy)

    wb = xlsxwriter.Workbook(args.output)
    fmts = Formats(wb)

    used_names: set[str] = {"guidance", "index", "all concepts"}

    guidance_ws = wb.add_worksheet("Guidance")
    index_ws = wb.add_worksheet("Index")
    concepts_ws = wb.add_worksheet("All concepts")

    # Pass A: assign sheet names (must happen before appearances are recorded)
    sheet_names: list[str] = []
    for group in groups:
        sheet_names.append(_sanitise_sheet_name(group.getLabel(), used_names))

    # Pass B: pre-compute concept appearances across all sheets so that group
    # sheets can write forward-reference hyperlinks for CROSS-kind concepts
    concept_appearances: dict[Concept, list[ConceptAppearance]] = {}
    for group, sheet_name in zip(groups, sheet_names):
        group_label = group.getLabel()
        for row_idx, rel in enumerate(group.relationships, start=_ROW_DATA):
            concept_appearances.setdefault(rel.concept, []).append(
                ConceptAppearance(sheet_name, row_idx, group_label)
            )

    # Pass C: create worksheets and write
    totals: Counter[CalcKind] = Counter()
    coverage_by_group: list[tuple[str, GroupCoverage]] = []
    for group, sheet_name in zip(groups, sheet_names):
        ws = wb.add_worksheet(sheet_name)
        cov = _write_group_sheet(
            ws,
            fmts,
            group,
            taxonomy,
            sheet_name,
            concept_appearances,
            calc_cls,
        )
        totals += cov.kind_counts
        coverage_by_group.append((group.getLabel(), cov))

    _write_guidance_sheet(guidance_ws, fmts)
    _write_index(
        index_ws, fmts, groups, sheet_names, [cov for _, cov in coverage_by_group]
    )
    _write_concepts_sheet(concepts_ws, fmts, concept_appearances, taxonomy)

    wb.close()
    print(f"Written: {args.output}")

    total_rows = sum(totals.values())
    print(f"\nConcept rows across {len(groups)} group sheets ({total_rows} total):")
    for kind in CalcKind:
        print(f"  {kind.name:<14} {totals[kind]:>5}  {_CALC_DESC[kind]}")

    overlaid_count = sum(len(v) for v in calc_cls.overlays.values())
    if overlaid_count:
        print(
            f"\n  {overlaid_count} orphan calc group(s) overlaid onto their 100%-match"
            " presentation group(s)."
        )

    _print_calc_coverage(
        calc_cls,
        coverage_by_group,
        concept_appearances,
    )


if __name__ == "__main__":
    main()
