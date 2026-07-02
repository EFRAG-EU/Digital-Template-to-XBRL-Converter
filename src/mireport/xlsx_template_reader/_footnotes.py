"""FootnoteFactCreator: attach workbook footnotes to already-created facts.

Walks the footnote container table (merged text cells delimit footnotes),
resolves each concept/dimension reference, and attaches the footnote text to
the matching facts in the report.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Callable, Iterator, Optional

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
        text_col_indices = _columnIndices(binding.text, origin)
        ref_col_indices = _columnIndices(ref_crm, origin)
        dim_col_indices: range | None = None
        if binding.ref_dimension is not None:
            dim_col_indices = _columnIndices(binding.ref_dimension, origin)

        def warn_ref(msg: str, cell: Optional[CellType] = None) -> None:
            self._msg.warning(
                msg,
                MessageType.ExcelParsing,
                ref=ref_crm.excelRef(cell),
            )

        for text_value, label_cells in self._iterFootnoteRows(
            table_crm, text_col_indices, ref_col_indices, dim_col_indices
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

    def _iterFootnoteRows(
        self,
        table_crm: CellRangeMetadata,
        text_col_indices: range,
        ref_col_indices: range,
        dim_col_indices: range | None = None,
    ) -> Iterator[tuple[str, list[tuple[str, str | None, CellType]]]]:
        """Yields (footnote_text, [(label, dim_text_or_None, cell), ...]) for each footnote."""
        current_text: str | None = None
        current_label_cells: list[tuple[str, str | None, CellType]] = []

        for _, row_cells in table_crm.rows():
            for ci in text_col_indices:
                cell = row_cells[ci]
                if isinstance(cell, MergedCell):
                    continue
                # Non-MergedCell in text column = boundary between footnotes
                if current_text is not None:
                    yield current_text, current_label_cells
                # Refs accumulated under a blank text block belong to no
                # footnote — drop them at every boundary, not just yielded ones.
                current_label_cells = []
                if cell.value is not None:
                    raw = str(cell.value).strip()
                    current_text = raw or None
                else:
                    current_text = None
                break

            dim_text: str | None = None
            if dim_col_indices is not None:
                for ci in dim_col_indices:
                    cell = row_cells[ci]
                    if not isinstance(cell, MergedCell) and cell.value is not None:
                        raw = str(cell.value).strip()
                        if raw:
                            dim_text = raw
                            break

            for ci in ref_col_indices:
                cell = row_cells[ci]
                if not isinstance(cell, MergedCell) and cell.value is not None:
                    label = str(cell.value).strip()
                    if label:
                        current_label_cells.append((label, dim_text, cell))

        if current_text is not None:
            yield current_text, current_label_cells

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


def _columnIndices(crm: CellRangeMetadata, origin: int) -> range:
    """Column offsets of a sub-range relative to its container's first column."""
    return range(
        crm.cellRange.min_col - origin,
        crm.cellRange.max_col - origin + 1,
    )
