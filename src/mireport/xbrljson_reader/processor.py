"""Turn an xBRL-JSON document into an InlineReport via the FactBuilder API.

The xlsx reader builds facts from workbook cells; this one builds the same facts from OIM
fact objects, so the two share everything downstream (layout, theming, aoix, Arelle).

Deliberately narrow: one entity, durations that declare the report's periods (an instant fact must
be at the start or end of one), and facts that carry a value. A fact outside that, or one the
taxonomy or the UTR does not allow, is skipped with a message saying why (and, where the
specifications name one, the error code); only a document that cannot be a report at all raises.
"""

from __future__ import annotations

import logging
from collections import Counter, defaultdict
from collections.abc import Mapping
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from lxml import etree
from markupsafe import Markup

from mireport.conversionresults import ConversionResultsBuilder, MessageType, Severity
from mireport.entrypoints import describeEntryPointSet, entryPointSetOf
from mireport.exceptions import InlineReportException, TaxonomyException, UnitException
from mireport.report import InlineReport
from mireport.report.fact import Fact
from mireport.report.factbuilder import FactBuilder
from mireport.report.model import Unit
from mireport.report.periods import DurationPeriodHolder, _Period
from mireport.taxonomy import Concept, QName, Taxonomy, getTaxonomy, listTaxonomies
from mireport.typealiases import DecimalPlaces
from mireport.xbrljson_reader._periods import parseDuration, parsePeriod
from mireport.xbrljson_reader.document import (
    INVALID_DIMENSION_VALUE,
    INVALID_JSON_STRUCTURE,
    INVALID_LANGUAGE,
    INVALID_UNIT,
    UNSUPPORTED,
    FactProblem,
    XbrlJsonDocument,
    XbrlJsonException,
    XbrlJsonFact,
)
from mireport.xml import XBRLI_NS

L = logging.getLogger(__name__)

XHTML_NS = "http://www.w3.org/1999/xhtml"
# Extension properties the snapshot files write (see tests/unitTests/xbrl_json_snapshot.py).
EXTENSION_NS = "urn:mireport:xbrl-json-extension"
FACT_FOOTNOTE_ARCROLE = "http://www.xbrl.org/2003/arcrole/fact-footnote"
# What the xlsx reader shows for an empty enumeration set (EE_SET_DESIRED_EMPTY_PLACEHOLDER_VALUE).
EMPTY_SET_TEXT = "None"
# Features that change nothing about how a document is read here.
_KNOWN_FEATURES = frozenset({"xbrl:canonicalValues", "xbrl:allowedDuplicates"})
_BOOLEANS = {"true": True, "1": True, "false": False, "0": False}

__all__ = ["XbrlJsonException", "XbrlJsonProcessor"]


class XbrlJsonProcessor:
    def __init__(
        self,
        document: XbrlJsonDocument | Mapping[str, Any],
        results: ConversionResultsBuilder,
        *,
        strict: bool = False,
        entityNameConcept: str | None = None,
    ) -> None:
        self._document = (
            document
            if isinstance(document, XbrlJsonDocument)
            else XbrlJsonDocument.fromDict(document)
        )
        self._entityNameConcept = entityNameConcept
        self._results = results
        self._strict = strict
        self._report: InlineReport | None = None
        self.factsAdded = 0
        self.factsSkipped = 0
        self._skippedBy: Counter[str] = Counter()

    @classmethod
    def fromFile(
        cls,
        path: Path | str,
        results: ConversionResultsBuilder,
        *,
        strict: bool = False,
        entityNameConcept: str | None = None,
    ) -> XbrlJsonProcessor:
        return cls(
            XbrlJsonDocument.fromFile(path),
            results,
            strict=strict,
            entityNameConcept=entityNameConcept,
        )

    @property
    def document(self) -> XbrlJsonDocument:
        return self._document

    @property
    def taxonomy(self) -> Taxonomy:
        assert self._report is not None
        return self._report.taxonomy

    @property
    def report(self) -> InlineReport:
        assert self._report is not None
        return self._report

    def createReport(self) -> InlineReport:
        self.factsAdded = self.factsSkipped = 0
        self._skippedBy.clear()
        self._report = self._startReport()
        self._reportIgnoredProperties()
        for invalid in self._document.invalidFacts:
            self._skip(invalid.id, invalid.problem)

        facts = [f for f in self._document.facts if not f.isNote]
        notes = {f.id: f for f in self._document.facts if f.isNote}
        if facts:
            periods = self._parsePeriods(facts)
            facts = [f for f in facts if f.id in periods]
            self._setEntity(facts)
            self._setPeriods(periods)
            self._setEntityName(facts)
            self._setDefaultCurrency(facts)
            built: dict[str, Fact] = {}
            for fact in facts:
                if (added := self._addFact(fact, periods[fact.id])) is not None:
                    built[fact.id] = added
            self.factsAdded = len(built)
            self._addFootnotes(facts, notes, built)

        total = len(self._document.facts) + len(self._document.invalidFacts)
        if self.factsSkipped and self._strict:
            reasons = ", ".join(
                f"{count} {code}" for code, count in sorted(self._skippedBy.items())
            )
            raise XbrlJsonException(
                f"{self.factsSkipped} of {total} facts could not be added ({reasons}); see the messages."
            )
        return self._report

    def _skip(
        self, factId: str, problem: Exception, concept: Concept | None = None
    ) -> None:
        self.factsSkipped += 1
        self._skippedBy[getattr(problem, "code", UNSUPPORTED)] += 1
        self._results.addMessage(
            f"Fact {factId} not added: {problem}",
            Severity.WARNING,
            MessageType.Conversion,
            taxonomy_concept=concept,
        )

    def _devInfo(self, message: str) -> None:
        self._results.addMessage(message, Severity.INFO, MessageType.DevInfo)

    # -- report level ---------------------------------------------------------------------------

    def _startReport(self) -> InlineReport:
        documents = list(self._document.taxonomy)
        try:
            entryPointSet = entryPointSetOf(documents)
        except TaxonomyException as e:
            raise XbrlJsonException(f"The document's taxonomy is unusable: {e}") from e
        if entryPointSet not in listTaxonomies():
            raise XbrlJsonException(
                f"No baked taxonomy for entry point {describeEntryPointSet(entryPointSet)}. "
                f"Bake it with scripts/update-taxonomy.py and load it with loadTaxonomyJSON(). "
                f"Available: {[describeEntryPointSet(eps) for eps in listTaxonomies()]}"
            )
        report = InlineReport(getTaxonomy(entryPointSet))
        report.requireAllFactsRendered = self._strict
        for document in sorted(entryPointSet):
            report.addSchemaRef(document)
        return report

    def _reportIgnoredProperties(self) -> None:
        """Say what in the document is not acted on, rather than ignore it silently."""
        doc = self._document
        for feature in sorted(set(doc.features) - _KNOWN_FEATURES):
            self._devInfo(f"documentInfo feature {feature} is not supported; ignored.")
        for name in sorted({*doc.documentInfoExtensions, *doc.extensions}):
            self._devInfo(f"Document property {name} is not used; ignored.")
        factExtensions = {
            name
            for fact in doc.facts
            for name in fact.extensions
            if not self._isExtension(name, "scale")
        }
        for name in sorted(factExtensions):
            self._devInfo(f"Fact property {name} is not used; ignored.")

    def _isExtension(self, prefixed: str, localName: str) -> bool:
        prefix, _, local = prefixed.partition(":")
        return (
            local == localName and self._document.namespaces.get(prefix) == EXTENSION_NS
        )

    def _parsePeriods(self, facts: list[XbrlJsonFact]) -> dict[str, _Period]:
        """Each fact's period. A fact without a usable one is skipped here, so one bad period
        costs that fact rather than the document."""
        periods: dict[str, _Period] = {}
        for fact in facts:
            try:
                if fact.period is None:
                    raise FactProblem(
                        UNSUPPORTED, "A fact for all time (no period) is not supported."
                    )
                periods[fact.id] = (
                    parseDuration(fact.period)
                    if "/" in fact.period
                    else parsePeriod(fact.period)
                )
            except FactProblem as problem:
                self._skip(fact.id, problem)
        return periods

    def _setEntity(self, facts: list[XbrlJsonFact]) -> None:
        entities = {f.entity for f in facts}
        if len(entities) != 1 or None in entities:
            raise XbrlJsonException(
                f"Exactly one reporting entity is supported; found {sorted(map(str, entities))}."
            )
        entity = next(iter(entities))
        assert entity is not None
        try:
            scheme, identifier = self._document.resolve(entity)
        except FactProblem as e:
            raise XbrlJsonException(
                f"Entity {entity!r} has no declared scheme prefix."
            ) from e
        self.report.setEntity(scheme, identifier)

    def _setPeriods(self, periods: Mapping[str, _Period]) -> None:
        """Declare every duration the facts are reported over, earliest first. The one with the
        most facts is the default period; a tie goes to the one that ends last.

        Nothing else is inferred. In OIM every fact says its own period, so no period is "the prior
        one" unless something says so."""
        factsPerPeriod = Counter(
            p for p in periods.values() if isinstance(p, DurationPeriodHolder)
        )
        if not factsPerPeriod:
            raise XbrlJsonException("The report has no duration period to anchor on.")
        for duration in sorted(factsPerPeriod, key=lambda d: (d.end, d.start)):
            self.report.addPeriod(self._periodName(duration), duration)
        default = max(factsPerPeriod, key=lambda d: (factsPerPeriod[d], d.end, d.start))
        self.report.setDefaultPeriodName(self._periodName(default))

    @staticmethod
    def _periodName(duration: DurationPeriodHolder) -> str:
        """A name for a period that follows from its dates, so the same document always gets the
        same names."""
        return f"p{duration.firstDay:%Y%m%d}_{duration.lastDay:%Y%m%d}"

    def _setEntityName(self, facts: list[XbrlJsonFact]) -> None:
        """The report title block wants a name: take it from the fact named by
        entityNameConcept (a local name), else fall back to the entity identifier."""
        name = None
        if self._entityNameConcept:
            name = next(
                (
                    f.value
                    for f in facts
                    if f.concept.partition(":")[2] == self._entityNameConcept
                    and f.value
                ),
                None,
            )
        self.report.setEntityName(name or self.report.entityIdentifier or "")

    def _setDefaultCurrency(self, facts: list[XbrlJsonFact]) -> None:
        """If the facts use exactly one currency, make it the report currency."""
        currencies: set[QName] = set()
        for fact in facts:
            if fact.unit is None:
                continue
            try:
                unit = Unit.fromUnitString(fact.unit, self._qname)
            except (UnitException, FactProblem, ValueError):
                continue  # the fact itself will say what is wrong
            if unit.is_currency:
                currencies.add(unit.measure)
        if len(currencies) == 1:
            self.report.setDefaultCurrency(next(iter(currencies)))

    # -- facts ----------------------------------------------------------------------------------

    def _qname(self, text: str) -> QName:
        namespace, local = self._document.resolve(text)
        return self.taxonomy.QNameMaker.fromNamespaceAndLocalName(namespace, local)

    def _concept(self, text: str) -> Concept:
        try:
            return self.taxonomy.getConcept(self._qname(text))
        except KeyError as e:
            raise FactProblem(
                INVALID_JSON_STRUCTURE, f"{text!r} is not in the taxonomy."
            ) from e

    def _addFact(self, fact: XbrlJsonFact, period: _Period) -> Fact | None:
        concept: Concept | None = None
        try:
            concept = self._concept(fact.concept)
            if fact.value is None:
                raise FactProblem(UNSUPPORTED, "Nil facts are not supported yet.")
            self._checkLanguage(fact)
            fb = self.report.getFactBuilder().setConcept(concept).setPeriod(period)
            self._setValue(fb, concept, fact)
            for name, member in fact.dimensions.items():
                self._setDimension(fb, name, member)
            built = fb.buildFact()
            self.report.addFact(built)
            return built
        except (FactProblem, InlineReportException, UnitException) as e:
            self._skip(fact.id, e, concept)
            return None

    def _checkLanguage(self, fact: XbrlJsonFact) -> None:
        """A text fact may say its language; the report is in one language."""
        if fact.language is None:
            return
        if (
            fact.language.split("-")[0].lower()
            != self.report.language.split("-")[0].lower()
        ):
            raise FactProblem(
                INVALID_LANGUAGE,
                f"The fact is in {fact.language}; the report is in {self.report.language}.",
            )

    def _setValue(self, fb: FactBuilder, concept: Concept, fact: XbrlJsonFact) -> None:
        value = fact.value
        assert value is not None
        if fact.decimals is not None and not concept.isNumeric:
            raise FactProblem(
                INVALID_JSON_STRUCTURE, "decimals is only for numeric facts."
            )
        if concept.isEnumerationSingle:
            member = self._member(concept, value)
            fb.setEnumerationValue(member).setValue(self._label(member))
        elif concept.isEnumerationSet:
            tokens = value.split()
            if len(set(tokens)) != len(tokens):
                raise FactProblem(
                    INVALID_DIMENSION_VALUE, f"The set {value!r} names a member twice."
                )
            members = [self._member(concept, m) for m in tokens]
            fb.setEnumerationSet(members)
            # An empty set is a valid value, but a fact needs something human readable.
            fb.setValue("\n".join(self._label(m) for m in members) or EMPTY_SET_TEXT)
        elif concept.isBoolean:
            if (flag := _BOOLEANS.get(value.strip())) is None:
                raise FactProblem(
                    INVALID_JSON_STRUCTURE, f"{value!r} is not a boolean."
                )
            fb.setValue(flag)
        elif concept.isNumeric:
            self._setNumeric(fb, concept, fact)
        elif concept.isTextblock:
            fb.setValue(self._xhtml(value))
        else:
            fb.setValue(value)

    def _member(self, concept: Concept, text: str) -> Concept:
        """An enumeration value: a member of the concept's domain."""
        member = self._concept(text)
        if (domain := concept.getEEDomain()) and member not in domain:
            raise FactProblem(
                INVALID_DIMENSION_VALUE,
                f"{text} is not in the domain of {concept.qname}.",
            )
        return member

    @staticmethod
    def _xhtml(text: str) -> Markup:
        """A text block's value is XHTML. It is emitted as markup, not escaped text, so it must be
        well-formed: a broken fragment would make the whole report unreadable."""
        try:
            etree.fromstring(f"<div xmlns='{XHTML_NS}'>{text}</div>")
        except etree.XMLSyntaxError as e:
            raise FactProblem(
                INVALID_JSON_STRUCTURE, f"Text is not well-formed XHTML: {e}"
            ) from e
        return Markup(text)

    @staticmethod
    def _label(member: Concept) -> str:
        """What a human reads for an enumeration member: its label, or its name if it has none."""
        return member.getStandardLabel(fallbackIfMissing=str(member.qname))

    def _setNumeric(
        self, fb: FactBuilder, concept: Concept, fact: XbrlJsonFact
    ) -> None:
        assert fact.value is not None
        self._setUnit(fb, concept, fact.unit)
        try:
            number = Decimal(fact.value.strip())
        except InvalidOperation as e:
            raise FactProblem(
                INVALID_JSON_STRUCTURE, f"{fact.value!r} is not a number."
            ) from e
        if not number.is_finite():
            raise FactProblem(
                INVALID_JSON_STRUCTURE, f"{fact.value!r} is not a finite number."
            )
        # No decimals means no accuracy was claimed; INF keeps every digit as written.
        places: DecimalPlaces = "INF" if fact.decimals is None else fact.decimals
        if concept.dataType.localName == "percentItemType":
            # A fraction with XBRL decimals, shown x100 with ix:scale -2; see setPercentageFact.
            fb.setPercentageFact(number, places)
            return
        scale = self._scale(fact)
        shown = number.scaleb(-scale)
        fb.setValue(
            int(shown) if shown == shown.to_integral() else float(shown)
        ).setDecimals(places)
        if scale:
            fb.setScale(scale)

    def _scale(self, fact: XbrlJsonFact) -> int:
        """The ix:scale a snapshot file records in its extension property, else 0."""
        for name, value in fact.extensions.items():
            if self._isExtension(name, "scale"):
                if isinstance(value, bool) or not isinstance(value, int):
                    raise FactProblem(
                        INVALID_JSON_STRUCTURE, f"{name} must be an integer."
                    )
                return value
        return 0

    def _setUnit(self, fb: FactBuilder, concept: Concept, text: str | None) -> None:
        if text is None:
            if concept.isMonetary:
                raise FactProblem(
                    INVALID_UNIT, "A monetary fact needs a currency unit."
                )
            # OIM convention: a dimensionless value omits the unit, and xbrli:pure is implied.
            fb.setSimpleUnit(
                self.taxonomy.QNameMaker.fromNamespaceAndLocalName(XBRLI_NS, "pure")
            )
            return
        try:
            unit = Unit.fromUnitString(text, self._qname)
        except (UnitException, ValueError) as e:
            raise FactProblem(INVALID_UNIT, f"Unit {text!r}: {e}") from e
        if unit.is_currency:
            fb.setCurrency(unit.measure)  # checks it is a real currency
            return
        if concept.isMonetary:
            raise FactProblem(
                INVALID_UNIT, f"A monetary fact needs a currency unit, not {text}."
            )
        isSimple = not unit.is_divide and len(unit.numerator) == 1
        if isSimple and not self.taxonomy.UTR.valid(concept.dataType, unit.measure):
            raise FactProblem(
                INVALID_UNIT,
                f"The UTR does not allow {text} for {concept.dataType.localName}.",
            )
        fb.setUnit(unit)

    def _setDimension(self, fb: FactBuilder, name: str, member: str | None) -> None:
        dimension = self._concept(name)
        if dimension.isTypedDimension:
            if member is None:
                raise FactProblem(
                    UNSUPPORTED, f"A nil value for {name} is not supported."
                )
            fb.setTypedDimension(dimension, member)
        elif dimension.isExplicitDimension:
            if member is None:
                raise FactProblem(
                    INVALID_DIMENSION_VALUE, f"Explicit dimension {name} has no member."
                )
            value = self._concept(member)
            if self.taxonomy.getDimensionDefault(dimension) == value:
                raise FactProblem(
                    INVALID_DIMENSION_VALUE,
                    f"{member} is the default of {name}, which is written by leaving "
                    f"the dimension out.",
                )
            fb.setExplicitDimension(dimension, value)
        else:
            raise FactProblem(INVALID_JSON_STRUCTURE, f"{name} is not a dimension.")

    # -- footnotes ------------------------------------------------------------------------------

    def _footnoteLinkTypes(self) -> set[str]:
        """The link type names that mean fact-to-footnote: those the document binds to the
        arcrole, and ``footnote``, the name xBRL-JSON predefines for it."""
        return {"footnote"} | {
            name
            for name, uri in self._document.linkTypes.items()
            if uri == FACT_FOOTNOTE_ARCROLE
        }

    def _addFootnotes(
        self,
        facts: list[XbrlJsonFact],
        notes: Mapping[str, XbrlJsonFact],
        built: Mapping[str, Fact],
    ) -> None:
        """A footnote for each note fact, attached to the facts that link to it."""
        linkTypes = self._footnoteLinkTypes()
        linked: dict[str, list[Fact]] = defaultdict(list)
        for fact in facts:
            if (target := built.get(fact.id)) is None:
                continue
            for linkType, groups in fact.links.items():
                if linkType not in linkTypes:
                    self._devInfo(
                        f"Fact {fact.id} has {linkType} links, which are not supported; ignored."
                    )
                    continue
                for noteIds in groups.values():
                    for noteId in noteIds:
                        linked[noteId].append(target)
        for noteId, targets in linked.items():
            if (note := notes.get(noteId)) is None or note.value is None:
                self._devInfo(
                    f"Footnote {noteId} is linked to but is not a note fact; ignored."
                )
                continue
            try:
                content = self._xhtml(note.value)
            except FactProblem as problem:
                self._devInfo(f"Footnote {noteId} not added: {problem}")
                continue
            self.report.addFootnoteToFacts(content, targets)
