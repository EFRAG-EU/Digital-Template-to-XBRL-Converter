"""Comparatives from last year's report: its own period's numbers and booleans, and what sits beside
them in a table, become this report's prior period.

Last year's report is read as a whole report on its own (see mireport.comparatives), so nothing has
to guess which of a file's periods is the prior one: it is the duration that ends where this
report's own period starts. Only the Report model is used here, so where last year's report came
from does not matter; the facts taken from one on another version of this report's taxonomy
are matched to it by name (alignToTaxonomy).
"""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass
from enum import StrEnum

from mireport.conversionresults import ConversionResultsBuilder, MessageType, Severity
from mireport.data.disclosures import areVersionsOfOneTaxonomy
from mireport.entrypoints import describeEntryPointSet
from mireport.exceptions import InlineReportException
from mireport.report.duplicates import DuplicateClass, duplicateClass
from mireport.report.fact import Fact
from mireport.report.factbuilder import FactBuilder
from mireport.report.model import PeriodPart, ReportPeriod
from mireport.report.periods import DurationPeriodHolder, _Period
from mireport.report.report import Report
from mireport.taxonomy import Concept, PresentationGroup, QName, Taxonomy

PRIOR_PERIOD_NAME = "prior"


class SkipReason(StrEnum):
    """Why a fact in last year's report was not taken."""

    OTHER_PERIOD = "not in the prior period"
    NOT_COMPARABLE = "neither a number nor a boolean, nor in a table row with one"
    NIL = "nil"
    NOTHING_THIS_YEAR = "nothing in this report's own period to compare it with"
    ALREADY_REPORTED = "already in this report"
    CONFLICT = "different from this report"
    INVALID = "could not be added"
    NOT_IN_TAXONOMY = "a name not in this report's taxonomy"
    OTHER_TYPE = "another period type or data type in this report's taxonomy"


@dataclass(frozen=True, slots=True)
class PriorMerge:
    """What mergePriorReport did: how many facts it took, and how many it skipped, by reason."""

    taken: int
    skipped: Mapping[SkipReason, int]


# What a reader is told about each reason, if anything: a fact that is already reported, the same,
# in this report is no news.
_SKIP_MESSAGES: Mapping[SkipReason, tuple[Severity, str]] = {
    SkipReason.NOT_COMPARABLE: (
        Severity.INFO,
        (
            "{count} facts in {fileName} left out: neither numbers nor booleans, nor in a table "
            "row with one."
        ),
    ),
    SkipReason.NIL: (Severity.INFO, "{count} nil facts in {fileName} left out."),
    SkipReason.NOTHING_THIS_YEAR: (
        Severity.INFO,
        (
            "{count} facts in {fileName} left out: this report has nothing for this year to "
            "compare them with."
        ),
    ),
    SkipReason.CONFLICT: (
        Severity.WARNING,
        "{count} facts in {fileName} differ from the same facts in this report; this report's are kept.",
    ),
    SkipReason.INVALID: (
        Severity.WARNING,
        "{count} facts in {fileName} could not be added to this report; see the developer messages.",
    ),
    SkipReason.NOT_IN_TAXONOMY: (
        Severity.WARNING,
        "{count} facts in {fileName} name a concept, dimension or member that this report's taxonomy does not have; they are left out.",
    ),
    SkipReason.OTHER_TYPE: (
        Severity.WARNING,
        "{count} facts in {fileName} are for concepts with another period type or data type in this report's taxonomy; they are left out.",
    ),
}


# The part of an entry point's URL that changes from one version of a taxonomy to the next.
def mergePriorReport(
    report: Report, prior: Report, results: ConversionResultsBuilder, *, fileName: str
) -> PriorMerge:
    """Add ``prior``'s numbers and booleans for the year before ``report``'s as its prior period.

    ``report``'s own period is its default duration; it must not have comparatives already.
    From ``prior`` the one duration that ends where that starts is taken, at any length, with the
    instants at its start and end; facts in its other periods are left out. Only non-nil numbers
    and booleans are taken, and any other fact in a table row with one taken (the same
    presentation group and dimensions, so the row is drawn for the prior period anyway), without
    footnotes, and only where ``report`` has its own period's fact for the same concept and
    dimensions to compare them with; a fact ``report`` already has is skipped: this report wins.
    From ``prior`` on another version of ``report``'s taxonomy, those facts are matched to it by
    name (alignToTaxonomy). Raises InlineReportException, before changing ``report``, when the two
    cannot be merged.
    """
    _checkSameReporter(report, prior)
    theirs = prior.taxonomy
    ours = alignToTaxonomy(theirs, report.taxonomy)
    current = report.defaultPeriod
    _checkNoComparatives(report)
    lined = _linedUpDuration(report, prior)
    if not report.addPeriod(PRIOR_PERIOD_NAME, lined):
        raise InlineReportException(
            f"The report already has a period named {PRIOR_PERIOD_NAME!r}."
        )
    report.setPriorPeriodName(PRIOR_PERIOD_NAME)

    existing: dict[tuple, list[Fact]] = defaultdict(list)
    for fact in report.facts:
        existing[fact.duplicateKey].append(fact)
    # What this report says for its own year: a comparative is shown beside one of these, or not at all.
    thisYears = {
        _subject(f)
        for f in report.facts
        if f.period.reportPeriod == report.defaultReportPeriod
    }
    dropped: Counter[ReportPeriod] = Counter()
    skipped: Counter[SkipReason] = Counter()
    taken: list[Fact] = []
    # The table rows the prior period is drawn for: those of the numbers and booleans taken, which
    # come first, so that the other facts in those rows can follow them.
    rows: set[tuple[PresentationGroup, frozenset]] = set()
    for fact in sorted(prior.facts, key=lambda f: not _comparable(f)):
        if (part := _partOf(fact.period.oim, lined)) is None:
            dropped[fact.period.reportPeriod] += 1
            skipped[SkipReason.OTHER_PERIOD] += 1
            continue
        if not _comparable(fact) and rows.isdisjoint(_rows(fact)):
            skipped[SkipReason.NOT_COMPARABLE] += 1
            continue
        if fact.isNil:
            skipped[SkipReason.NIL] += 1
            continue
        if any(c.qname not in ours for c in _named(fact)):
            skipped[SkipReason.NOT_IN_TAXONOMY] += 1
            continue
        if _typeOf(ours[fact.concept.qname]) != _typeOf(fact.concept):
            skipped[SkipReason.OTHER_TYPE] += 1
            continue
        try:
            built = (
                _onOurTaxonomy(report, fact, ours)
                .setNamedPeriod(
                    PRIOR_PERIOD_NAME, at=None if part is PeriodPart.Whole else part
                )
                .buildFact()
            )
        except InlineReportException as e:
            skipped[SkipReason.INVALID] += 1
            _devInfo(results, f"Comparative {fact} from {fileName} not added: {e}")
            continue
        if _subject(built) not in thisYears:
            skipped[SkipReason.NOTHING_THIS_YEAR] += 1
            continue
        if same := existing.get(built.duplicateKey):
            inconsistent = any(
                duplicateClass(s, built) is DuplicateClass.INCONSISTENT for s in same
            )
            skipped[
                SkipReason.CONFLICT if inconsistent else SkipReason.ALREADY_REPORTED
            ] += 1
            continue
        report.addFact(built)
        taken.append(built)
        if _comparable(fact):
            rows.update(_rows(fact))

    if theirs.entryPointSet != report.taxonomy.entryPointSet:
        results.addMessage(
            f"{fileName} is on taxonomy {describeEntryPointSet(theirs.entryPointSet)}: its "
            f"figures are matched by name to this report's "
            f"{describeEntryPointSet(report.taxonomy.entryPointSet)}.",
            Severity.INFO,
            MessageType.Conversion,
        )
    results.addMessage(
        f"Comparative figures for {_days(lined)}: {len(taken)} taken from {fileName}.",
        Severity.INFO,
        MessageType.Conversion,
    )
    if dropped:
        periods = ", ".join(
            f"{_days(p.duration)} ({count} facts)" for p, count in dropped.items()
        )
        results.addMessage(
            f"Facts in {fileName} for periods other than the one before {_days(current)} "
            f"left out: {periods}.",
            Severity.INFO,
            MessageType.Conversion,
        )
    for reason, (severity, text) in _SKIP_MESSAGES.items():
        if count := skipped[reason]:
            results.addMessage(
                text.format(count=count, fileName=fileName),
                severity,
                MessageType.Conversion,
            )
    _warnOfOtherCurrencies(report, taken, results, fileName)
    return PriorMerge(len(taken), dict(skipped))


def alignToTaxonomy(theirs: Taxonomy, ours: Taxonomy) -> Mapping[QName, Concept]:
    """Our concept for each of ``theirs``' that it has. ``theirs`` is ``ours``, or another version of
    it (one the same disclosure config names, areVersionsOfOneTaxonomy) whose concepts are matched
    by local name; a match's period type and data type may still differ (``_typeOf``). Raises
    InlineReportException if ``theirs`` is neither.
    """
    if theirs.entryPointSet == ours.entryPointSet:
        return {c.qname: c for c in ours.concepts}
    if not areVersionsOfOneTaxonomy(theirs.entryPointSet, ours.entryPointSet):
        raise InlineReportException(
            f"Last year's report is on taxonomy {sorted(theirs.entryPointSet)}, not this "
            f"report's {sorted(ours.entryPointSet)} or another version of it."
        )
    names = Counter(c.qname.localName for c in ours.concepts)
    # A name in more than one of its namespaces matches neither.
    byName = {
        c.qname.localName: c for c in ours.concepts if names[c.qname.localName] == 1
    }
    return {
        c.qname: byName[c.qname.localName]
        for c in theirs.concepts
        if c.qname.localName in byName
    }


def _named(fact: Fact) -> Iterator[Concept]:
    """Every concept ``fact`` names: its own, its dimensions and members, and the members it holds
    (an enumeration)."""
    yield fact.concept
    for d in fact.explicit_values:
        yield d.dimension
        yield d.member
    for t in fact.typed_values:
        yield t.dimension
    yield from fact.enumeration or ()


def _onOurTaxonomy(
    report: Report, fact: Fact, ours: Mapping[QName, Concept]
) -> FactBuilder:
    """A builder for ``fact``, a non-nil fact all of whose concepts ``ours`` has, on ``report``'s
    taxonomy. Building re-runs every check."""
    builder = (
        FactBuilder(report).setConcept(ours[fact.concept.qname]).setValue(fact.value)
    )
    for d in fact.explicit_values:
        builder.setExplicitDimension(ours[d.dimension.qname], ours[d.member.qname])
    for t in fact.typed_values:
        builder.setTypedDimension(ours[t.dimension.qname], t.value)
    if fact.unit is not None:
        builder.setUnit(fact.unit)
    if fact.decimals is not None:
        builder.setDecimals(fact.decimals)
    if fact.scale is not None:
        builder.setScale(fact.scale)
    if fact.rounding is not None:
        builder.setRounding(fact.rounding)
    if fact.enumeration is not None:
        builder.setEnumerationSet(ours[m.qname] for m in fact.enumeration)
    return builder


def _typeOf(concept: Concept) -> tuple[object, ...]:
    """What a fact's value is: its concept's period type and data type (by namespace and local
    name, since prefixes may differ from one version to the next)."""
    return (concept.periodType, concept.dataType.namespace, concept.dataType.localName)


def _checkSameReporter(report: Report, prior: Report) -> None:
    ours = (report.entityScheme, report.entityIdentifier)
    theirs = (prior.entityScheme, prior.entityIdentifier)
    if ours != theirs:
        raise InlineReportException(
            f"Last year's report is for entity {theirs[1]} ({theirs[0]}), "
            f"not this report's {ours[1]} ({ours[0]})."
        )


def _checkNoComparatives(report: Report) -> None:
    """Comparatives come from last year's report, so this one must not have any already: a prior
    period, or a duration that overlaps its own or ends where it starts. Other durations, such as
    baseline and target years, are left alone."""
    current = report.defaultPeriod
    if report.priorReportPeriod is not None or any(
        _overlapsOrPrecedes(p.duration, current)
        for p in report.reportingPeriods
        if p.duration != current
    ):
        raise InlineReportException(
            f"This report already has comparatives for {_days(current)}, so last year's "
            f"report cannot add them. Its periods: {_listPeriods(report.reportingPeriods)}."
        )


def _overlapsOrPrecedes(
    duration: DurationPeriodHolder, current: DurationPeriodHolder
) -> bool:
    return duration.end == current.start or (
        duration.start < current.end and current.start < duration.end
    )


def _linedUpDuration(report: Report, prior: Report) -> DurationPeriodHolder:
    """The one duration of ``prior`` that ends exactly where ``report``'s own period starts. When
    there is none, and ``prior``'s own period is ``report``'s or comes after it, the two reports
    were most likely given the wrong way round, or one twice, and the error says so."""
    current = report.defaultPeriod
    candidates = {
        p.duration for p in prior.reportingPeriods if p.duration.end == current.start
    }
    if len(candidates) == 1:
        return next(iter(candidates))
    theirs = prior.defaultPeriod
    if not candidates and theirs == current:
        raise InlineReportException(
            f"Last year's report is for {_days(theirs)}, the same period as this report: is it "
            f"this year's report given twice?"
        )
    if not candidates and theirs.start >= current.end:
        raise InlineReportException(
            f"Last year's report is for {_days(theirs)}, after this report's {_days(current)}: "
            f"are the two the wrong way round?"
        )
    found = "no" if not candidates else "more than one"
    raise InlineReportException(
        f"Last year's report has {found} period ending where this report's {_days(current)} "
        f"starts. Its periods: {_listPeriods(prior.reportingPeriods)}; "
        f"this report's: {_listPeriods(report.reportingPeriods)}."
    )


def _comparable(fact: Fact) -> bool:
    """Whether ``fact`` is taken as a comparative wherever it is: a number or a boolean."""
    return fact.concept.isNumeric or fact.concept.isBoolean


def _dimensions(fact: Fact) -> frozenset:
    """``fact``'s dimensions and their values."""
    return frozenset(fact.explicit_dimensions.items()) | frozenset(
        fact.typed_dimensions.items()
    )


def _subject(fact: Fact) -> tuple[object, frozenset]:
    """What ``fact`` is about, whatever its period: its concept and its dimensions."""
    return fact.concept, _dimensions(fact)


def _rows(fact: Fact) -> set[tuple[PresentationGroup, frozenset]]:
    """The table rows ``fact`` is in: each presentation group it is in (which draws its dimensional
    facts as tables, whether or not it presents their hypercube), with its dimensions and their
    values. None for a fact with no dimensions, which is a line of a list, not of a table."""
    if not (dimensions := _dimensions(fact)):
        return set()
    return {(group, dimensions) for group in fact.concept.presentationGroups}


def _partOf(period: _Period, duration: DurationPeriodHolder) -> PeriodPart | None:
    """Which part of ``duration`` an OIM period is, if any."""
    parts: dict[_Period, PeriodPart] = {
        duration: PeriodPart.Whole,
        duration.startInstant: PeriodPart.Start,
        duration.endInstant: PeriodPart.End,
    }
    return parts.get(period)


def _warnOfOtherCurrencies(
    report: Report,
    taken: Iterable[Fact],
    results: ConversionResultsBuilder,
    fileName: str,
) -> None:
    """A comparative keeps its own currency, but one that differs from the report's is worth
    saying once."""
    if (currency := report.defaultCurrency) is None:
        return
    others = {
        str(unit.measure)
        for fact in taken
        if fact.concept.isMonetary
        and (unit := fact.unit) is not None
        and unit.is_currency
        and unit.measure != currency
    }
    if others:
        results.addMessage(
            f"Comparative figures from {fileName} are in {', '.join(sorted(others))}, "
            f"not this report's {currency}.",
            Severity.WARNING,
            MessageType.Conversion,
        )


def _days(duration: DurationPeriodHolder) -> str:
    return f"{duration.firstDay} – {duration.lastDay}"


def _listPeriods(periods: Iterable[ReportPeriod]) -> str:
    return ", ".join(_days(p.duration) for p in periods) or "none"


def _devInfo(results: ConversionResultsBuilder, message: str) -> None:
    results.addMessage(message, Severity.INFO, MessageType.DevInfo)
