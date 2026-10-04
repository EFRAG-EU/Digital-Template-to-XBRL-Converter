"""The lines of a list section: one per concept, with comparatives from other periods alongside."""

from __future__ import annotations

from collections.abc import Callable

from mireport.report.fact import Fact
from mireport.report.layout.model import ListEntry
from mireport.report.model import FactPeriod, ReportPeriod
from mireport.taxonomy import Relationship


def list_entries(
    relationship_to_fact: dict[Relationship, list[Fact]],
    period_rank: Callable[[ReportPeriod], int],
) -> tuple[ListEntry, ...]:
    """A line for each fact of a concept in its first period, with that concept's facts in the
    other periods beside it; a second fact in the same period is a line of its own.

    A concept with facts in one period gets one line per fact, as a list always had.
    """
    entries: list[ListEntry] = []
    for group, (rel, facts) in enumerate(relationship_to_fact.items()):
        by_period: dict[FactPeriod, list[Fact]] = {}
        for fact in facts:
            by_period.setdefault(fact.period, []).append(fact)
        # The report's rank puts every one of its periods in a definite order. Within one, the
        # closing instant leads (it is the figure) and the opening one follows as its comparative;
        # the name only keeps the sort stable for a period the report has not declared.
        periods = sorted(
            by_period,
            key=lambda p: (
                period_rank(p.reportPeriod),
                -p.partRank,
                p.reportPeriod.name,
            ),
        )
        for n in range(max(map(len, by_period.values()), default=0)):
            line = [by_period[p][n] for p in periods if n < len(by_period[p])]
            entries.append(ListEntry(rel, line[0], tuple(line[1:]), group))
    return tuple(entries)
