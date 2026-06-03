from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from itertools import groupby
from typing import TYPE_CHECKING, ClassVar

from mireport.data.disclosures import VSME_DEFAULTS
from mireport.stringutil import stripLabelPrefix

if TYPE_CHECKING:
    from mireport.report.layout import ReportSection


@dataclass(frozen=True)
class TocItem:
    idx: int
    label: str


@dataclass(frozen=True)
class TocGroup:
    heading: str | None  # None = no heading; each item renders as a flat <li>
    items: list[TocItem]


def _old_vsme_prefix(section: ReportSection) -> str:
    return section.presentation.definition.split(".")[0]


def _move_sections_after(
    sections: list[ReportSection], source_prefix: str, target_prefix: str
) -> list[ReportSection]:
    prefixes = {id(s): _old_vsme_prefix(s) for s in sections}
    to_move = [s for s in sections if prefixes[id(s)] == source_prefix]
    if not to_move:
        return sections
    remaining = [s for s in sections if prefixes[id(s)] != source_prefix]
    insert_pos = next(
        (i + 1 for i, s in enumerate(remaining) if prefixes[id(s)] == target_prefix),
        None,
    )
    if insert_pos is None:
        return sections
    return remaining[:insert_pos] + to_move + remaining[insert_pos:]


def _split_label(label: str) -> list[str]:
    return [p.strip() for p in label.split(" - ")]


def _item_label(parts: list[str]) -> str:
    if len(parts) >= 3:
        return " - ".join(parts[2:])
    return parts[1] if len(parts) >= 2 else parts[0]


class DisclosureLayoutStrategy(ABC):
    _STRATEGY_MAP: ClassVar[dict[str, type[DisclosureLayoutStrategy]]] = {}

    def __init_subclass__(cls, strategy_name: str, **kwargs: object) -> None:
        super().__init_subclass__(**kwargs)
        if strategy_name in DisclosureLayoutStrategy._STRATEGY_MAP:
            raise ValueError(
                f"Strategy name {strategy_name!r} is already registered"
                f" by {DisclosureLayoutStrategy._STRATEGY_MAP[strategy_name].__name__}"
            )
        DisclosureLayoutStrategy._STRATEGY_MAP[strategy_name] = cls

    @classmethod
    def for_entry_point(cls, entry_point: str) -> DisclosureLayoutStrategy:
        taxonomy_eps = VSME_DEFAULTS["taxonomyEntryPoints"]
        all_vsme = frozenset(
            [
                taxonomy_eps["supportedEntryPoint"],
                *taxonomy_eps.get("oldEntryPoints", []),
            ]
        )
        if entry_point not in all_vsme:
            return DefaultLayoutStrategy()
        layout = VSME_DEFAULTS["layoutStrategy"]
        strategy_name = layout["entryPoints"].get(entry_point, layout["default"])
        return cls._STRATEGY_MAP.get(strategy_name, DefaultLayoutStrategy)()

    def organise_sections(self, sections: list[ReportSection]) -> list[ReportSection]:
        return sections

    @abstractmethod
    def build_toc(
        self,
        sections_with_idx: list[tuple[int, ReportSection]],
        language: str,
    ) -> list[TocGroup]: ...

    def section_label(self, section: ReportSection, language: str) -> str:
        return section.getLabel(language)

    @abstractmethod
    def page_group_key(self, section: ReportSection, language: str) -> str: ...


_VSME_SECTION_AFFINITY: dict[str, str] = {
    "B7": "B6",
    "C7": "C6",
    "C9": "C8",
}


class OldVsmeLayoutStrategy(DisclosureLayoutStrategy, strategy_name="old_vsme"):
    """Handles definitions like '[B01.000] - General information - Basis for Preparation'."""

    def organise_sections(self, sections: list[ReportSection]) -> list[ReportSection]:
        return _move_sections_after(sections, "[C02", "[B02")

    def page_group_key(self, section: ReportSection, language: str) -> str:
        raw = section.presentation.definition.split(".")[0].lstrip("[")  # e.g. 'B07'
        short = raw[0] + str(int(suffix)) if (suffix := raw[1:]).isdigit() else raw
        return _VSME_SECTION_AFFINITY.get(short, short)

    def build_toc(
        self,
        sections_with_idx: list[tuple[int, ReportSection]],
        language: str,
    ) -> list[TocGroup]:
        sorted_sections = sorted(
            sections_with_idx,
            key=lambda t: t[1].presentation.definition,
        )

        groups: list[TocGroup] = []
        for prefix, group_iter in groupby(
            sorted_sections,
            key=lambda t: t[1].presentation.definition.split(".")[0],
        ):
            items_list = list(group_iter)
            # Short prefix: strip '[', e.g. '[B01' → 'B1'
            raw = prefix.lstrip("[")
            short_prefix = (
                raw[0] + str(int(suffix)) if (suffix := raw[1:]).isdigit() else raw
            )

            # Category from the first section in the group
            first_label = items_list[0][1].getLabel(language)
            first_parts = _split_label(first_label)
            category = first_parts[1] if len(first_parts) >= 2 else first_label

            heading = f"[{short_prefix}] — {category}"
            items = [
                TocItem(idx=idx, label=_item_label(_split_label(s.getLabel(language))))
                for idx, s in items_list
            ]
            groups.append(TocGroup(heading=heading, items=items))

        return groups


class VsmeLayoutStrategy(DisclosureLayoutStrategy, strategy_name="vsme"):
    """Handles definitions like '[1010] B1 - General information - Basis for Preparation'."""

    def section_label(self, section: ReportSection, language: str) -> str:
        return stripLabelPrefix(section.getLabel(language))

    def page_group_key(self, section: ReportSection, language: str) -> str:
        prefix = _split_label(stripLabelPrefix(section.getLabel(language)))[0]
        return _VSME_SECTION_AFFINITY.get(prefix, prefix)

    @staticmethod
    def _toc_group_key(section: ReportSection, language: str) -> str:
        # Group by first part of stripped label, e.g. 'B1' from 'B1 - General information - …'
        return _split_label(stripLabelPrefix(section.getLabel(language)))[0]

    def build_toc(
        self,
        sections_with_idx: list[tuple[int, ReportSection]],
        language: str,
    ) -> list[TocGroup]:
        sorted_sections = sorted(
            sections_with_idx,
            key=lambda t: t[1].presentation.definition,
        )

        groups: list[TocGroup] = []
        for _, group_iter in groupby(
            sorted_sections,
            key=lambda t: self._toc_group_key(t[1], language),
        ):
            items_list = list(group_iter)

            first_parts = _split_label(
                stripLabelPrefix(items_list[0][1].getLabel(language))
            )
            heading = (
                " - ".join(first_parts[:2]) if len(first_parts) >= 2 else first_parts[0]
            )
            items = [
                TocItem(
                    idx=idx,
                    label=_item_label(
                        _split_label(stripLabelPrefix(s.getLabel(language)))
                    ),
                )
                for idx, s in items_list
            ]
            groups.append(TocGroup(heading=heading, items=items))

        return groups


class DefaultLayoutStrategy(DisclosureLayoutStrategy, strategy_name="default"):
    """Generic strategy: flat TOC, one section per page, labels unchanged."""

    def page_group_key(self, section: ReportSection, language: str) -> str:
        return section.presentation.roleUri

    def build_toc(
        self,
        sections_with_idx: list[tuple[int, ReportSection]],
        language: str,
    ) -> list[TocGroup]:
        return [
            TocGroup(
                heading=None,
                items=[TocItem(idx=idx, label=s.getLabel(language))],
            )
            for idx, s in sections_with_idx
        ]
