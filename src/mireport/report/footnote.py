from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass, field
from itertools import count
from typing import TYPE_CHECKING, Self, cast

from markupsafe import Markup

from mireport.report.layout import ReportSection, TabularReportSection

if TYPE_CHECKING:
    from mireport.report.fact import Fact


@dataclass(slots=True, frozen=True, eq=True)
class Footnote:
    """An immutable footnote that can be shared across many facts."""

    id: int
    content: Markup
    _facts: list[Fact] = field(
        default_factory=list, compare=False, hash=False, repr=False
    )

    def __html__(self) -> Markup:
        if self._facts:
            return self.as_aoix()
        return self.content

    def as_aoix(self) -> Markup:
        return Markup(f"{{{{ footnote {self.id} }}}}{self.content}{{{{ end }}}}")


@dataclass(frozen=True)
class FootnoteRefData:
    ref_id: str  # unique anchor, e.g. "fnref-3"
    fn_id: int  # for href="#fn-{fn_id}"
    label: str  # e.g. "1" (sole ref) or "1.2" (second ref to footnote 1)


@dataclass(frozen=True)
class FootnoteBacklink:
    ref_id: str  # href target
    label: str  # display label


@dataclass(frozen=True)
class FootnoteEntry:
    fn: Footnote
    anchor_id: str  # e.g. "fn-3"
    backlinks: tuple[FootnoteBacklink, ...]


class FootnoteManager:
    """
    Manages footnote ref assignment and emission across a report render.

    Usage:
      1. Call register_refs(sections, footnotes_by_group) once before rendering.
         This pre-assigns globally stable ref IDs and labels for every footnote
         occurrence in document order, so labels are consistent and backlinks
         include forward references later in the document. Returns self.
      2. During rendering: call ref_data(fn) for each fact/heading footnote occurrence.
      3. Call take_footnotes() to flush and emit all footnotes accumulated since the
         last call. Safe to call when the buffer is empty — returns an empty list.
    """

    def __init__(self, footnotes: dict[int, Footnote]) -> None:
        self._footnotes = footnotes  # fn_id → Footnote (owned by InlineReport)
        # Pre-scan data (populated by register_refs, read-only during render)
        self._ref_counter: count[int] = count()
        self._fn_counter: count[int] = count(
            1
        )  # global footnote number counter (1-based)
        self._fn_global_num: dict[int, int] = {}  # fn_id → global number
        self._all_refs: dict[int, list[str]] = defaultdict(
            list
        )  # fn_id → ref_ids in document order
        self._ref_queue: dict[int, deque[str]] = defaultdict(
            deque
        )  # fn_id → ref_ids to consume during render

        # Render-time state
        self._emitted: set[int] = set()  # fn_ids already emitted
        self._pending: dict[
            int, None
        ] = {}  # ordered set of fn_ids seen since last take_footnotes()

    def register_refs(
        self,
        sections: list[ReportSection | TabularReportSection],
        footnotes_by_group: dict,
    ) -> Self:
        """
        Pre-scan all sections in document order — matching the template's iteration —
        and assign globally stable ref IDs and labels to every footnote occurrence.
        Must be called once before rendering begins.
        """
        for section in sections:
            if grp_fn := footnotes_by_group.get(section.presentation.roleUri):
                self._register_one(grp_fn)
            if section.tabular:
                section = cast(TabularReportSection, section)
                for row in section.table.rows:
                    for cell in row.cells:
                        if cell.fact is not None:
                            for fn in cell.fact.footnotes:
                                self._register_one(fn)
            else:
                for facts in section.relationshipToFact.values():
                    for fact in facts:
                        for fn in fact.footnotes:
                            self._register_one(fn)
        return self

    def _register_one(self, fn: Footnote) -> None:
        if fn.id not in self._fn_global_num:
            self._fn_global_num[fn.id] = next(self._fn_counter)
        ref_id = f"fnref-{next(self._ref_counter)}"
        self._all_refs[fn.id].append(ref_id)
        self._ref_queue[fn.id].append(ref_id)

    def _label(self, fn_id: int, ref_id: str) -> str:
        refs = self._all_refs[fn_id]
        num = self._fn_global_num[fn_id]
        return str(num) if len(refs) == 1 else f"{num}.{refs.index(ref_id) + 1}"

    def ref_data(self, fn: Footnote) -> FootnoteRefData:
        """
        Called once per (fact, footnote) occurrence during rendering.
        Pops the next pre-assigned ref from the queue.
        """
        ref_id = self._ref_queue[fn.id].popleft()
        self._pending.setdefault(fn.id, None)
        return FootnoteRefData(
            ref_id=ref_id, fn_id=fn.id, label=self._label(fn.id, ref_id)
        )

    def take_footnotes(self) -> list[FootnoteEntry]:
        """
        Returns one FootnoteEntry per footnote accumulated since the last call that has not
        yet been emitted. Each entry's backlinks include ALL refs in document order
        (including forward references later in the document). Clears the buffer and marks
        returned footnotes as emitted.
        """
        entries = []
        for fn_id in self._pending:
            if fn_id not in self._emitted:
                self._emitted.add(fn_id)
                backlinks = tuple(
                    FootnoteBacklink(ref_id=ref_id, label=self._label(fn_id, ref_id))
                    for ref_id in self._all_refs[fn_id]
                )
                entries.append(
                    FootnoteEntry(
                        fn=self._footnotes[fn_id],
                        anchor_id=f"fn-{fn_id}",
                        backlinks=backlinks,
                    )
                )
        self._pending = {}
        return entries
