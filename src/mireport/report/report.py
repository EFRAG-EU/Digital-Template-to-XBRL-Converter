from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping, Sequence
from datetime import date
from itertools import combinations, count
from typing import TYPE_CHECKING, Protocol

from markupsafe import Markup

from mireport.exceptions import InlineReportException
from mireport.report.duplicates import DuplicateClass, duplicateClass
from mireport.report.fact import Fact
from mireport.report.factbuilder import FactBuilder
from mireport.report.footnote import Footnote
from mireport.report.model import FactPeriod, PeriodPart, PeriodRole, ReportPeriod
from mireport.report.periods import (
    DurationPeriodHolder,
    InstantPeriodHolder,
    _Period,
)
from mireport.taxonomy import Concept, QName, Taxonomy
from mireport.typealiases import FactValue
from mireport.xml import LEI_NS

if TYPE_CHECKING:
    from babel import Locale


class InconsistentDuplicateHandler(Protocol):
    """
    Callback invoked once per set of inconsistent duplicate facts (see
    :meth:`Report.reportInconsistentDuplicateFacts`).
    """

    def __call__(self, concept: Concept, facts: Sequence[Fact]) -> None: ...


class Report:
    """An XBRL report as data: its taxonomy, entity, periods, facts and footnotes.

    Nothing here is about how the report looks; InlineReport adds that, and an xBRL-JSON writer
    (mireport.xbrljson) needs nothing more than this.
    """

    def __init__(self, taxonomy: Taxonomy, *, language: str | None = None):
        self._facts: list[Fact] = []
        self._factsByConcept: dict[Concept, list[Fact]] = defaultdict(list)
        self._footnoteCounter: count = count(1)
        self._footnotes: dict[int, Footnote] = {}
        self._taxonomy: Taxonomy = taxonomy
        self._language: str | None = language
        self._periods: dict[str, ReportPeriod] = {}
        self._defaultPeriodName: str = ""
        self._priorPeriodName: str = ""
        # An ordered set: the order they were added in is the order they are written in.
        self._schemaRefs: dict[str, None] = {}
        self._partial_facts: dict[Concept, FactBuilder] = {}
        self._entityIdentifier: str | None = None
        self._entityScheme: str | None = None
        self._entityPrefix: str | None = None
        self._defaultCurrency: QName | None = None

    @property
    def taxonomy(self) -> Taxonomy:
        return self._taxonomy

    @property
    def language(self) -> str:
        """The language of the report (a BCP 47 string, as `xml:lang` has it): the one it was
        given, else the taxonomy's default, else English."""
        return self._language or self._taxonomy.defaultLanguage or "en"

    @property
    def outputLocale(self) -> Locale:
        """The locale numbers and dates are written in. A Report is data, with no say in how its
        facts look; an InlineReport has one."""
        raise InlineReportException(
            f"A {type(self).__name__} is data: only an InlineReport formats its facts."
        )

    @property
    def entityIdentifier(self) -> str | None:
        return self._entityIdentifier

    @property
    def entityScheme(self) -> str | None:
        return self._entityScheme

    @property
    def entityPrefix(self) -> str:
        """The prefix the entity's scheme is written with in xBRL-JSON (``lei:...``): the one
        given with setEntity(), else ``lei`` for an LEI and ``entity`` for any other scheme."""
        if self._entityPrefix:
            return self._entityPrefix
        return "lei" if self._entityScheme == LEI_NS else "entity"

    def setEntity(
        self, scheme: str, identifier: str, *, prefix: str | None = None
    ) -> None:
        """The reporting entity's identifier and the scheme (a URI) that identifier is in, and
        optionally the prefix to write that scheme with (see entityPrefix)."""
        if not (scheme and identifier):
            raise InlineReportException(
                f"Entity not configured correctly. Specifically: '{scheme=}' '{identifier=}'"
            )
        self._entityScheme = scheme
        self._entityIdentifier = identifier
        self._entityPrefix = prefix

    @property
    def defaultCurrency(self) -> QName | None:
        """The currency monetary facts are reported in unless they say otherwise."""
        return self._defaultCurrency

    def setDefaultCurrency(self, currency: QName | str) -> None:
        match currency:
            case str():
                currency = self._taxonomy.QNameMaker.fromString(f"iso4217:{currency}")
        self._defaultCurrency = currency

    def addSchemaRef(self, schemaRef: str) -> None:
        self._schemaRefs[schemaRef] = None

    @property
    def schemaRefs(self) -> tuple[str, ...]:
        """The taxonomy documents the report refers to, in the order they were added; the
        taxonomy's own entry point if none were."""
        return tuple(self._schemaRefs) or tuple(sorted(self._taxonomy.entryPointSet))

    def setDefaultPeriodName(self, name: str) -> None:
        """The report's own (current) period, which a fact with no period of its own is in."""
        if name not in self._periods:
            raise InlineReportException(
                f"Can't set default period as no such period {name=} exists."
            )
        if name == self._priorPeriodName:
            raise InlineReportException(
                f"Period {name=} is the prior period, so cannot also be the current one."
            )
        self._defaultPeriodName = name

    def setPriorPeriodName(self, name: str) -> None:
        """The period before the report's own, which comparative facts are in."""
        if name not in self._periods:
            raise InlineReportException(
                f"Can't set prior period as no such period {name=} exists."
            )
        if name == self._defaultPeriodName:
            raise InlineReportException(
                f"Period {name=} is the current period, so cannot also be the prior one."
            )
        self._priorPeriodName = name

    def addDurationPeriod(self, name: str, periodStart: date, periodEnd: date) -> bool:
        """Declare a period from its first and last day, both included."""
        return self.addPeriod(
            name, DurationPeriodHolder.fromDays(periodStart, periodEnd)
        )

    def addPeriod(self, name: str, duration: DurationPeriodHolder) -> bool:
        if name in self._periods:
            return False
        self._periods[name] = ReportPeriod(name, duration)
        return True

    def hasNamedPeriod(self, name: str) -> bool:
        """
        Returns True if the report has a period with the given name.
        """
        return name in self._periods

    def getReportPeriod(self, name: str) -> ReportPeriod:
        try:
            return self._periods[name]
        except KeyError:
            raise InlineReportException(
                f"Period '{name}' does not exist in the report."
            ) from None

    def locatePeriod(self, oim: _Period) -> FactPeriod:
        """Where an OIM period falls among the report's periods, for a producer that was not told
        which part of one it means (an xBRL-JSON fact, say). An instant that is both the end of one
        period and the start of the next is taken as the end."""
        match oim:
            case DurationPeriodHolder():
                for reportPeriod in self._periods.values():
                    if reportPeriod.duration == oim:
                        return FactPeriod(reportPeriod, PeriodPart.Whole)
            case InstantPeriodHolder():
                for part in (PeriodPart.End, PeriodPart.Start):
                    for reportPeriod in self._periods.values():
                        if (found := FactPeriod(reportPeriod, part)).oim == oim:
                            return found
        raise InlineReportException(
            f"{oim} is not one of the report's periods, nor the start or end of one."
        )

    @property
    def hasDefaultPeriod(self) -> bool:
        return bool(self._defaultPeriodName)

    @property
    def defaultReportPeriod(self) -> ReportPeriod:
        if not (name := self._defaultPeriodName):
            raise InlineReportException("The report has no default period yet.")
        return self._periods[name]

    @property
    def defaultPeriod(self) -> DurationPeriodHolder:
        return self.defaultReportPeriod.duration

    @property
    def priorReportPeriod(self) -> ReportPeriod | None:
        return self._periods[name] if (name := self._priorPeriodName) else None

    @property
    def priorPeriod(self) -> DurationPeriodHolder | None:
        return prior.duration if (prior := self.priorReportPeriod) else None

    def periodRole(self, period: ReportPeriod) -> PeriodRole:
        match period.name:
            case name if name == self._defaultPeriodName:
                return PeriodRole.CURRENT
            case name if name == self._priorPeriodName:
                return PeriodRole.PRIOR
            case _:
                return PeriodRole.OTHER

    @property
    def reportingPeriods(self) -> tuple[ReportPeriod, ...]:
        """Every period, in the order a reader should meet them: current, prior, then the rest in
        the order they were declared."""
        return tuple(
            sorted(
                self._periods.values(),
                key=lambda p: (
                    {PeriodRole.CURRENT: 0, PeriodRole.PRIOR: 1}.get(
                        self.periodRole(p), 2
                    ),
                ),
            )
        )

    def getFactBuilder(self) -> FactBuilder:
        """
        Returns a FactBuilder for the given concept.
        """
        return FactBuilder(self)

    def addFact(self, fact: Fact) -> None:
        """
        Adds a Fact to the report.
        """
        self._facts.append(fact)
        self._factsByConcept[fact.concept].append(fact)

    def reportInconsistentDuplicateFacts(
        self, handler: InconsistentDuplicateHandler
    ) -> None:
        """
        Find sets of duplicate facts that are inconsistent and report each via handler.

        Duplicates and consistency are those of the OIM and the "Handling Duplicate
        Facts" WGN (see mireport.report.duplicates; https://www.xbrl.org/WGN/xbrl-duplicates/WGN-2025-01-14/xbrl-duplicates-2025-01-14.html): facts equal in every aspect but
        the value are duplicates, and a set is inconsistent when two of its facts
        are (e.g. different values at the same accuracy, or numbers whose intervals
        do not overlap). Such facts cannot both be rendered, so one is dropped
        downstream; this surfaces the conflict so the caller can warn the report
        preparer. Each set is reported whole, in the order its first fact was added.
        """
        for group in self.duplicates():
            if any(
                duplicateClass(a, b) is DuplicateClass.INCONSISTENT
                for a, b in combinations(group, 2)
            ):
                handler(group[0].concept, group)

    def _createFootnote(self, content: Markup) -> Footnote:
        fn = Footnote(id=next(self._footnoteCounter), content=content)
        self._footnotes[fn.id] = fn
        return fn

    def addFootnoteToFacts(
        self,
        content: str | Markup,
        facts: list[Fact],
        *,
        group: str | None = None,
    ) -> Footnote:
        """Create a footnote and attach it to the given facts directly. ``group`` is for an
        InlineReport, which shows a grouped footnote under its section's heading."""
        if isinstance(content, str):
            content = Markup.escape(content)
        footnote = self._createFootnote(content)
        for fact in facts:
            fact.footnotes.append(footnote)
            footnote._facts.append(fact)
        return footnote

    def addFootnoteForConcepts(
        self,
        content: str | Markup,
        concepts: list[Concept],
        *,
        group: str | None = None,
    ) -> Footnote:
        """Create a footnote and attach it to all existing facts for each
        specified concept. If no facts exist for a concept, the footnote will
        not be attached to any facts for that concept.

        See also addFootnoteToFacts for attaching footnotes directly to specific facts."""
        facts = [f for c in concepts for f in self.getFacts(c)]
        return self.addFootnoteToFacts(content, facts, group=group)

    def replaceFactValue(
        self, concept: Concept | QName | str, value: FactValue
    ) -> None:
        """
        Replace the value of the only fact for the specified concept.
        """
        if not isinstance(concept, Concept):
            concept = self._taxonomy.getConcept(concept)
        candidates = self.getFacts(concept)

        if not candidates:
            raise InlineReportException(
                f"No existing fact found for concept {concept}. Cannot replace value."
            )
        if len(candidates) != 1:
            raise InlineReportException(
                f"Multiple existing facts found for concept {concept}. Cannot replace value unambiguously."
            )
        candidates[0].value = value

    @property
    def hasFacts(self) -> bool:
        return bool(self._facts)

    @property
    def hasPartialFacts(self) -> bool:
        """True while any partial facts are still awaiting an external value.

        Partial facts are registered with addPartialFact() and cleared by
        completePartialFact(). Iterate partialFactsByConcept to see which
        partial facts are outstanding.
        """
        return bool(self._partial_facts)

    @property
    def partialFactsByConcept(self) -> Mapping[Concept, FactBuilder]:
        """Snapshot of the partial facts still awaiting an externally-supplied value.

        Maps each pending Concept to its placeholder FactBuilder. Registered
        via addPartialFact(); resolved (and removed) via completePartialFact().

        The returned mapping is a *copy* taken at call time, not a live view of
        the internal store: mutating it has no effect, and completing facts does
        not change a snapshot you already hold. This is deliberate so callers can
        safely iterate it while calling completePartialFact() in the same loop.
        Re-read the property (or check hasPartialFacts) to see the current state.
        """
        return dict(self._partial_facts)

    @property
    def factCount(self) -> int:
        return len(self._facts)

    @property
    def facts(self) -> list[Fact]:
        return list(self._facts)

    def duplicates(self) -> list[list[Fact]]:
        """Groups of two or more facts that are duplicates of one another (see
        mireport.report.duplicates), in the order the first of each group was added."""
        groups: dict[tuple, list[Fact]] = defaultdict(list)
        for fact in self._facts:
            groups[fact.duplicateKey].append(fact)
        return [group for group in groups.values() if len(group) > 1]

    def getFacts(self, concept: Concept) -> list[Fact]:
        result = self._factsByConcept.get(concept)
        return [] if result is None else result.copy()

    def addPartialFact(self, concept: Concept, fb: FactBuilder) -> None:
        """Register a partial FactBuilder whose value must be supplied externally.

        Use this for disclosures whose value comes from an external document
        rather than the spreadsheet (e.g. an uploaded Word document). The
        FactBuilder is held without a value until completePartialFact() supplies
        one. While registered, the concept appears in partialFactsByConcept and
        keeps hasPartialFacts True. Raises ValueError if fb.concept does not
        match concept, or if concept is already pending.
        """
        if fb.concept != concept:
            raise ValueError(
                f"FactBuilder concept {fb.concept} does not match expected concept {concept}."
            )
        if concept in self._partial_facts:
            raise ValueError(
                f"Concept {concept} already has a pending external fact registered."
            )
        self._partial_facts[concept] = fb

    def completePartialFact(self, concept: Concept, value: FactValue) -> None:
        """Supply the value for a pending external fact, build it, and add it.

        Completes a concept previously registered with addPartialFact(): sets the
        value, builds the fact, adds it to the report, and removes the concept
        from the pending set (so it no longer appears in partialFactsByConcept
        and hasPartialFacts flips to False once the last one is done). Raises
        ValueError if concept is not currently pending.

        Note this mutates the internal pending store. The mapping returned by
        partialFactsByConcept is a snapshot, so it is safe to iterate that while
        calling this in the same loop.
        """
        if concept not in self._partial_facts:
            raise ValueError(
                f"Concept {concept} is not registered as pending an external value."
            )
        fb = self._partial_facts.pop(concept)
        fb.setValue(value)
        self.addFact(fb.buildFact())
