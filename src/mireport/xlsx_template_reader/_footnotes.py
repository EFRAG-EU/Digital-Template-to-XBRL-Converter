"""FootnoteFactCreator: attach workbook footnotes to already-created facts.

Walks the footnote container table (merged text cells delimit footnotes),
resolves each concept/dimension reference, and attaches the footnote text to
the matching facts in the report.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Callable, Iterator, NamedTuple, Optional

if TYPE_CHECKING:
    from mireport.report import InlineReport
    from mireport.report.fact import Fact
    from mireport.taxonomy import Concept, Taxonomy
    from mireport.xlsx_template_reader._bindings import FootnoteBinding
    from mireport.xlsx_template_reader._constants import CellType
    from mireport.xlsx_template_reader._messages import Messenger
    from mireport.xlsx_template_reader._ranges import CellRangeMetadata

from openpyxl.cell import MergedCell

from mireport.conversionresults import MessageType
from mireport.exceptions import AmbiguousComponentException
from mireport.stringutil import str_to_markupsafe
from mireport.taxonomy import QName
from mireport.xlsx_template_reader._reader import CellValue

L = logging.getLogger(__name__)


class FootnoteFactCreator:
    """Creates footnotes from a resolved FootnoteBinding and attaches them to facts."""

    def __init__(
        self,
        report: InlineReport,
        msg: Messenger,
        binding: FootnoteBinding,
    ) -> None:
        self._report = report
        self._msg = msg
        self._binding = binding

    @property
    def taxonomy(self) -> Taxonomy:
        return self._report.taxonomy

    def createFootnotes(self) -> None:
        binding = self._binding
        table_crm = binding.table
        ref_crm = binding.ref

        origin = table_crm.cellRange.min_col
        text_col = _columnIndex(binding.text, origin)
        ref_col = _columnIndex(ref_crm, origin)
        dim_col: int | None = None
        if binding.ref_dimension is not None:
            dim_col = _columnIndex(binding.ref_dimension, origin)

        def warn_ref(msg: str, cell: Optional[CellType] = None) -> None:
            self._msg.warning(
                msg,
                MessageType.ExcelParsing,
                ref=ref_crm.excelRef(cell),
            )

        for text_value, label_cells in _iterFootnoteRows(
            table_crm, text_col, ref_col, dim_col
        ):
            if not label_cells:
                self._msg.warning(
                    f"Footnote ('{text_value[:60]}') has no concept references; skipping.",
                    MessageType.ExcelParsing,
                    ref=table_crm,
                )
                continue
            resolved_refs = self._resolveFootnoteRefs(label_cells, warn_ref)
            if not resolved_refs:
                self._msg.warning(
                    f"Footnote ('{text_value[:60]}') has no resolvable concept references; skipping.",
                    MessageType.ExcelParsing,
                    ref=table_crm,
                )
                continue
            target_facts: list[Fact] = []
            for concept, member in resolved_refs:
                target_facts.extend(self._factsForReference(concept, member, warn_ref))
            if not target_facts:
                continue
            self._report.addFootnoteToFacts(str_to_markupsafe(text_value), target_facts)

    def _resolveFootnoteRefs(
        self,
        label_cells: list[tuple[str, str | None, CellType]],
        warn: Callable[[str, CellType | None], object],
    ) -> list[tuple[Concept, Concept | None]]:
        """Resolve (label, dim_text, cell) rows to (concept, optional member concept) pairs."""
        resolved: list[tuple[Concept, Concept | None]] = []
        for label, dim_text, cell in label_cells:
            try:
                concept = self.taxonomy.resolveConcept(
                    label, by_label=True, by_name=True, by_qname=True
                )
            except AmbiguousComponentException as exc:
                warn(f"Footnote reference '{label}' is ambiguous: {exc}", cell)
                continue
            if concept is None:
                warn(
                    f"Footnote reference '{label}' could not be matched to a reportable taxonomy concept.",
                    cell,
                )
                continue

            member: Concept | None = None
            if dim_text:
                try:
                    member = self.taxonomy.resolveConcept(
                        dim_text,
                        by_label=True,
                        by_name=True,
                        by_qname=True,
                        only_reportable=False,
                    )
                except AmbiguousComponentException as exc:
                    warn(f"Footnote dimension '{dim_text}' is ambiguous: {exc}", cell)
                else:
                    if member is None:
                        warn(
                            f"Footnote dimension '{dim_text}' could not be resolved; "
                            f"attaching to all facts for '{label}'.",
                            cell,
                        )

            resolved.append((concept, member))
        return resolved

    def _factsForReference(
        self,
        concept: Concept,
        member: Optional[Concept],
        warn: Callable[..., object],
    ) -> list[Fact]:
        """The facts a footnote reference targets: dimension-filtered when a
        member is given, dimensionless facts otherwise."""
        facts = self._report.getFacts(concept)
        if not facts:
            warn(
                f"No facts found for concept '{concept.qname}'; footnote will not be attached.",
            )
        elif member is not None:
            # TODO: typed dimensions store a string value under "typed {axis_qname}"
            # rather than a QName member — if typed domain filtering is ever needed, extend here.
            facts = [f for f in facts if member.qname in f.aspects.values()]
            if not facts:
                warn(
                    f"Dimension member '{member.qname}' not found among facts for "
                    f"'{concept.qname}'; footnote will not be attached.",
                )
        else:
            # No member specified — restrict to facts that carry no taxonomy-defined
            # dimension context (no explicit QName key, no typed-dimension string key).
            facts = [
                f
                for f in facts
                if not any(
                    isinstance(k, QName)
                    or (isinstance(k, str) and k.startswith("typed "))
                    for k in f.aspects
                )
            ]
            if not facts:
                warn(
                    f"All facts for concept '{concept.qname}' have dimensional context; "
                    f"footnote will not be attached.",
                )
        return facts


def _columnIndex(crm: CellRangeMetadata, origin: int) -> int:
    """Column offset of a sub-range's first column relative to its container's
    first column. Footnote sub-ranges are single-column; the binding resolver
    warns about wider ones, which fall back to their first column here."""
    return crm.cellRange.min_col - origin


class _FootnoteRow(NamedTuple):
    """One physical row of the footnote table, parsed to plain values."""

    is_boundary: bool
    text: str | None
    dim_text: str | None
    ref: tuple[str, CellType] | None


def _readFootnoteRow(
    row_cells: tuple[CellType, ...],
    text_col: int,
    ref_col: int,
    dim_col: int | None,
) -> _FootnoteRow:
    """Parse one physical row of the footnote table.

    A real (non-merged) cell in the text column marks a boundary between
    footnote blocks; a MergedCell continues the block above. The dimension and
    reference columns need no merged-cell handling: a MergedCell never holds a
    value, so it reads as blank like any other empty cell.
    """
    is_boundary = False
    text: str | None = None
    cell = row_cells[text_col]
    if not isinstance(cell, MergedCell):
        is_boundary = True
        if not (value := CellValue.fromCell(cell)).isBlank:
            text = value.as_str_stripped()

    dim_text: str | None = None
    if dim_col is not None:
        if not (value := CellValue.fromCell(row_cells[dim_col])).isBlank:
            dim_text = value.as_str_stripped()

    ref: tuple[str, CellType] | None = None
    cell = row_cells[ref_col]
    if not (value := CellValue.fromCell(cell)).isBlank:
        ref = (value.as_str_stripped(), cell)

    return _FootnoteRow(is_boundary, text, dim_text, ref)


def _iterFootnoteRows(
    table_crm: CellRangeMetadata,
    text_col: int,
    ref_col: int,
    dim_col: int | None = None,
) -> Iterator[tuple[str, list[tuple[str, str | None, CellType]]]]:
    """Yields (footnote_text, [(label, dim_text_or_None, cell), ...]) for each footnote."""
    current_text: str | None = None
    current_label_cells: list[tuple[str, str | None, CellType]] = []

    for _, row_cells in table_crm.rows():
        row = _readFootnoteRow(row_cells, text_col, ref_col, dim_col)
        if row.is_boundary:
            if current_text is not None:
                yield current_text, current_label_cells
            # Refs accumulated under a blank text block belong to no
            # footnote — drop them at every boundary, not just yielded ones.
            current_label_cells = []
            current_text = row.text
        if row.ref is not None:
            label, cell = row.ref
            current_label_cells.append((label, row.dim_text, cell))

    if current_text is not None:
        yield current_text, current_label_cells
