from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass
from itertools import groupby
from typing import TYPE_CHECKING, ClassVar

from mireport.data.disclosures import getLayoutStrategyName
from mireport.entrypoints import describeEntryPointSet
from mireport.stringutil import stripLabelPrefix

if TYPE_CHECKING:
    from mireport.entrypoints import EntryPointSet
    from mireport.report.layout import ReportSection

L = logging.getLogger(__name__)


@dataclass(frozen=True)
class TocItem:
    idx: int
    label: str


@dataclass(frozen=True)
class TocGroup:
    heading: str | None  # None = no heading; each item renders as a flat <li>
    items: list[TocItem]


class DisclosureLayoutStrategy(ABC):
    _STRATEGY_MAP: ClassVar[dict[str, type[DisclosureLayoutStrategy]]] = {}
    _FALLBACK_STRATEGY: ClassVar[type[DisclosureLayoutStrategy] | None] = None

    def __init_subclass__(
        cls, strategy_name: str, fallback: bool = False, **kwargs: object
    ) -> None:
        super().__init_subclass__(**kwargs)
        if strategy_name in DisclosureLayoutStrategy._STRATEGY_MAP:
            raise ValueError(
                f"Strategy name {strategy_name!r} is already registered"
                f" by {DisclosureLayoutStrategy._STRATEGY_MAP[strategy_name].__name__}"
            )
        DisclosureLayoutStrategy._STRATEGY_MAP[strategy_name] = cls
        if fallback:
            if (existing := DisclosureLayoutStrategy._FALLBACK_STRATEGY) is not None:
                raise ValueError(
                    f"Fallback strategy already registered by {existing.__name__}"
                )
            DisclosureLayoutStrategy._FALLBACK_STRATEGY = cls

    @classmethod
    def _fallback(cls) -> DisclosureLayoutStrategy:
        if (fallback := cls._FALLBACK_STRATEGY) is None:
            raise ValueError("No fallback layout strategy registered")
        return fallback()

    @classmethod
    def for_entry_point(cls, entryPointSet: EntryPointSet) -> DisclosureLayoutStrategy:
        """The strategy for an entry-point set; each URL the config names is a
        one-document set."""
        if (strategy_name := getLayoutStrategyName(entryPointSet)) is None:
            return cls._fallback()
        if (strategy_cls := cls._STRATEGY_MAP.get(strategy_name)) is not None:
            return strategy_cls()
        L.warning(
            "Unknown layout strategy %r for entry point %r; using fallback",
            strategy_name,
            describeEntryPointSet(entryPointSet),
        )
        return cls._fallback()

    def organise_sections(self, sections: list[ReportSection]) -> list[ReportSection]:
        return sections

    @abstractmethod
    def build_toc(
        self,
        sections: list[ReportSection],
        language: str,
    ) -> list[TocGroup]: ...

    def section_label(self, section: ReportSection, language: str) -> str:
        return section.getLabel(language)

    @abstractmethod
    def page_group_key(self, section: ReportSection, language: str) -> str: ...


class SimpleLayoutStrategy(
    DisclosureLayoutStrategy, strategy_name="simple", fallback=True
):
    """Generic fallback: flat TOC, one section per page, labels unchanged."""

    def page_group_key(self, section: ReportSection, language: str) -> str:
        return section.presentation.roleUri

    def build_toc(
        self,
        sections: list[ReportSection],
        language: str,
    ) -> list[TocGroup]:
        return [
            TocGroup(
                heading=None,
                items=[TocItem(idx=idx, label=s.getLabel(language))],
            )
            for idx, s in enumerate(sections, start=1)
        ]


_VSME_SECTION_AFFINITY: dict[str, str] = {
    "B7": "B6",
    "C7": "C6",
    "C9": "C8",
}


def _old_vsme_section_code(section: ReportSection) -> str:
    """Canonical short code for an old-VSME section definition,
    e.g. '[B07.000] - General information …' -> 'B7'.

    Falls back to the raw token (minus any leading '[') when it doesn't match
    the expected <letter><digits> shape."""
    token = section.presentation.definition.split(".", 1)[0].removeprefix("[")
    if (digits := token[1:]).isdigit():
        return f"{token[0]}{int(digits)}"
    L.warning(
        "Old-VSME section definition %r does not match the expected"
        " <letter><digits> shape; using %r as its section code.",
        section.presentation.definition,
        token,
    )
    return token


def _move_sections_after(
    sections: list[ReportSection], source: str, target: str
) -> list[ReportSection]:
    """Move every section whose code is `source` to immediately after the last
    section whose code is `target`. No-op if either group is absent."""
    coded = [(s, _old_vsme_section_code(s)) for s in sections]
    to_move = [s for s, code in coded if code == source]
    remaining = [s for s, code in coded if code != source]
    last_target = max(
        (i for i, (_, code) in enumerate(coded) if code == target and code != source),
        default=None,
    )
    if not to_move or last_target is None:
        return sections
    # last_target indexes `coded`; translate to an index within `remaining`
    insert_at = sum(1 for _, code in coded[: last_target + 1] if code != source)
    return remaining[:insert_at] + to_move + remaining[insert_at:]


def _split_label(label: str) -> list[str]:
    return [p.strip() for p in label.split(" - ")]


def _item_label(parts: list[str]) -> str:
    if len(parts) >= 3:
        return " - ".join(parts[2:])
    return parts[1] if len(parts) >= 2 else parts[0]


class OldVsmeLayoutStrategy(DisclosureLayoutStrategy, strategy_name="old_vsme"):
    """Handles definitions like '[B01.000] - General information - Basis for Preparation'."""

    def organise_sections(self, sections: list[ReportSection]) -> list[ReportSection]:
        return _move_sections_after(sections, "C2", "B2")

    def section_label(self, section: ReportSection, language: str) -> str:
        """Replace the '[C06.000]' prefix with the canonical code, e.g.
        '[C06.000] - General - Basis' -> 'C6 - General - Basis'."""
        code = _old_vsme_section_code(section)
        _, *rest = _split_label(section.getLabel(language))
        return " - ".join([code, *rest])

    def page_group_key(self, section: ReportSection, language: str) -> str:
        code = _old_vsme_section_code(section)
        return _VSME_SECTION_AFFINITY.get(code, code)

    def build_toc(
        self,
        sections: list[ReportSection],
        language: str,
    ) -> list[TocGroup]:
        groups: list[TocGroup] = []
        for code, group_iter in groupby(
            enumerate(sections, start=1),
            key=lambda t: _old_vsme_section_code(t[1]),
        ):
            labelled = [
                (idx, _split_label(s.getLabel(language))) for idx, s in group_iter
            ]

            # Category from the first section in the group
            first_parts = labelled[0][1]
            category = first_parts[1] if len(first_parts) >= 2 else first_parts[0]

            heading = f"[{code}] - {category}"
            items = [
                TocItem(idx=idx, label=_item_label(parts)) for idx, parts in labelled
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

    def build_toc(
        self,
        sections: list[ReportSection],
        language: str,
    ) -> list[TocGroup]:
        labelled = [
            (idx, _split_label(stripLabelPrefix(s.getLabel(language))))
            for idx, s in enumerate(sections, start=1)
        ]
        groups: list[TocGroup] = []
        # Group by first part of stripped label, e.g. 'B1' from 'B1 - General information - …'
        for _, group_iter in groupby(labelled, key=lambda t: t[1][0]):
            group = list(group_iter)
            first_parts = group[0][1]
            heading = (
                " - ".join(first_parts[:2]) if len(first_parts) >= 2 else first_parts[0]
            )
            items = [TocItem(idx=idx, label=_item_label(parts)) for idx, parts in group]
            groups.append(TocGroup(heading=heading, items=items))

        return groups
