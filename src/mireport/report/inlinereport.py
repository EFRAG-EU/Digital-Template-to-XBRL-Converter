from __future__ import annotations

import logging
import time
from collections import defaultdict
from collections.abc import Mapping, Sequence
from datetime import UTC, date, datetime
from itertools import count
from unicodedata import name as unicode_name

import ixbrltemplates
from babel import Locale
from jinja2 import Environment, PackageLoader, StrictUndefined, Undefined
from markupsafe import Markup
from rcssmin import cssmin

import mireport
from mireport.exceptions import InlineReportException
from mireport.filesupport import FilelikeAndFileName, zipSafeString
from mireport.localise import (
    as_xmllang,
    decimal_symbol,
    get_locale_from_str,
    group_symbol,
)
from mireport.report.disclosure_layout import DisclosureLayoutStrategy
from mireport.report.fact import Fact, Symbol, tidyTdValue
from mireport.report.factbuilder import FactBuilder
from mireport.report.footnote import Footnote, FootnoteManager
from mireport.report.layout import ReportLayoutOrganiser, TableStyle
from mireport.report.periods import DurationPeriodHolder, PeriodHolder
from mireport.report.reportpackage import buildReportPackage
from mireport.report.theme import ReportTheme
from mireport.stringutil import NumberGroupingApostrophes
from mireport.taxonomy import Concept, PresentationStyle, QName, Taxonomy
from mireport.typealiases import FactValue

L = logging.getLogger(__name__)


class InlineReport:
    def __init__(self, taxonomy: Taxonomy, outputLocale: Locale | None = None):
        self._facts: list[Fact] = []
        self._factsByConcept: dict[Concept, list[Fact]] = defaultdict(list)
        self._footnoteCounter: count = count(1)
        self._footnotes: dict[int, Footnote] = {}
        self._taxonomy: Taxonomy = taxonomy
        self._periods: dict[str, DurationPeriodHolder] = {}
        self._entityName: str = "Sample"
        self._generatedReport: str | None = None
        self._defaultPeriodName: str = ""
        self._schemaRefs: set[str] = set()
        self._reportTitle: str = ""
        self._reportSubtitle: str = ""
        self._introduction: str | None = None
        self._backCoverMatter: str | None = None
        self._theme: ReportTheme = ReportTheme.default()
        self._footnotesByGroup: dict[str, Footnote] = {}
        self._labelOverrides: dict[str, str] = {}
        self._partial_facts: dict[Concept, FactBuilder] = {}
        if not outputLocale:
            outputLocale = (
                get_locale_from_str(taxonomy.defaultLanguage or "") or Locale.default()
            )
        self._outputLocale: Locale = outputLocale

        decimal_separator = decimal_symbol(self._outputLocale)
        group_is_apos = group_symbol(self._outputLocale) in NumberGroupingApostrophes

        match (decimal_separator, group_is_apos):
            case (".", True):
                numeric_transform = "num-dot-decimal-apos"
            case (".", False):
                numeric_transform = "num-dot-decimal"
            case (",", True):
                numeric_transform = "num-comma-decimal-apos"
            case (",", False):
                numeric_transform = "num-comma-decimal"
            case _:
                raise InlineReportException(
                    f"Unsupported decimal separator '{decimal_separator}' in locale {self._outputLocale}."
                )

        self._defaultAspects: dict[str, str] = {
            "numeric-transform": numeric_transform,
            "decimals": "INF",
        }

    def setLabelOverrides(self, overrides: dict[str, str]) -> None:
        self._labelOverrides = overrides

    def setReportTitle(self, title: str) -> None:
        self._reportTitle = title

    def setReportSubtitle(self, subtitle: str) -> None:
        self._reportSubtitle = subtitle

    def setIntroduction(self, introduction: str) -> None:
        self._introduction = introduction

    def setBackCoverMatter(self, backCoverMatter: str) -> None:
        self._backCoverMatter = backCoverMatter

    @property
    def taxonomy(self) -> Taxonomy:
        return self._taxonomy

    @property
    def defaultAspects(self) -> dict[str, str]:
        return self._defaultAspects.copy()

    def getDefaultAspectsForAoix(self) -> str:
        defaults = self._defaultAspects.copy()
        aoix = []
        for key, value in defaults.items():
            if not (key and value):
                raise InlineReportException(
                    f"Default aspects not configured correctly. Specifically: '{key=}' '{value=}'"
                )
            if key in {"entity-identifier", "entity-scheme", "decimals"}:
                value = f'"{value}"'
            aoix.append(f"{{{{ default {key} = {value} }}}}")
        return "\n".join(aoix)

    def setDefaultAspect(self, key: str, value: str) -> None:
        self._defaultAspects[key] = value

    @property
    def theme(self) -> ReportTheme:
        return self._theme

    @theme.setter
    def theme(self, value: ReportTheme) -> None:
        self._theme = value

    def setEntityName(self, name: str) -> None:
        self._entityName = name

    def setDefaultPeriodName(self, name: str) -> None:
        if name not in self._periods:
            raise InlineReportException(
                f"Can't set default period as no such period {name=} exists."
            )
        self._defaultPeriodName = name

    def addDurationPeriod(self, name: str, periodStart: date, periodEnd: date) -> bool:
        if name in self._periods:
            return False
        self._periods[name] = DurationPeriodHolder(periodStart, periodEnd)
        return True

    def hasNamedPeriod(self, name: str) -> bool:
        """
        Returns True if the InlineReport has a period with the given name.
        """
        return name in self._periods

    def addSchemaRef(self, schemaRef: str) -> None:
        self._schemaRefs.add(schemaRef)

    @property
    def defaultPeriod(self) -> DurationPeriodHolder:
        return self._periods[self._defaultPeriodName]

    @property
    def language(self) -> str:
        """Returns the language of the report (as a BCP 47 string like `xml:lang` uses).

        Useful for requesting the right taxonomy labels and other language-sensitive output."""
        return as_xmllang(self._outputLocale)

    def getPeriodsForAoix(self) -> str:
        p = []
        for name, period in self._periods.items():
            p.append(f'{{{{ period {name} "{period.start}" "{period.end}" }}}}')
        p.append(f"{{{{ default period = {self._defaultPeriodName} }}}}")
        return "\n".join(p)

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
        """Create a footnote and attach it to the given facts directly."""
        if isinstance(content, str):
            content = Markup.escape(content)
        footnote = self._createFootnote(content)
        for fact in facts:
            fact.footnotes.append(footnote)
            footnote._facts.append(fact)
        if group is not None:
            self._footnotesByGroup[group] = footnote
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

    def getNamespacesForAoix(self) -> str:
        # {{ namespace utr = "http://www.xbrl.org/2009/utr" }}
        lines = []
        for p, n in self.taxonomy.namespacePrefixesMap.items():
            lines.append(f'{{{{ namespace {p} = "{n}" }}}}')
        return "\n".join(lines)

    def getSchemaRefForAoix(self) -> str:
        # {{ schema-ref "https://xbrl.efrag.org/taxonomy/vsme/2024-12-17/vsme-all.xsd" }}
        if not self._schemaRefs:
            self._schemaRefs.add(self.taxonomy.entryPoint)
        lines = []
        for url in sorted(self._schemaRefs):
            lines.append(f'{{{{ schema-ref "{url}" }}}}')
        return "\n".join(lines)

    def getDocumentInformation(self) -> list[dict[str, str | PeriodHolder | Symbol]]:
        bits: list[dict[str, str | PeriodHolder | Symbol]] = []

        def addDict(
            key: str,
            value: str | PeriodHolder | Symbol,
            format_macro: str | None = None,
        ) -> None:
            d: dict[str, str | PeriodHolder | Symbol] = {"key": key, "value": value}
            if format_macro is not None:
                d["format_macro"] = format_macro
            bits.append(d)

        meta = {
            "Entity Name": self._entityName,
            "Entity Identifier": self._defaultAspects["entity-identifier"],
            "Entity Identifier Scheme": self._defaultAspects["entity-scheme"],
            "Report currency": self._defaultAspects["monetary-units"],
        }
        for k, v in meta.items():
            addDict(k, v)
        addDict("Report period", self.defaultPeriod, "render_duration_period")

        separator = decimal_symbol(self._outputLocale)
        bits.append(
            {
                "key": "Decimal separator",
                "value": Symbol(symbol=separator, name=unicode_name(separator)),
                "format_macro": "render_symbol",
            }
        )
        return bits

    def _constructInlineReport(self) -> str:
        if not (self.hasFacts and self._defaultPeriodName):
            raise InlineReportException(
                "Cannot generate a report with no facts or period."
            )
        if self._partial_facts:
            concepts = ", ".join(sorted(str(c) for c in self._partial_facts))
            raise InlineReportException(
                f"Cannot generate report while there are partial facts for the following concepts: {concepts}."
            )

        if self._generatedReport is not None:
            return self._generatedReport

        label_language = self._taxonomy.getBestSupportedLanguage(self.language)
        lang = label_language or ""

        layout = DisclosureLayoutStrategy.for_entry_point(self._taxonomy.entryPoint)
        rl = ReportLayoutOrganiser(self._taxonomy, self)
        sections = rl.organise(layout)
        toc = layout.build_toc(sections, lang)

        env = Environment(
            loader=PackageLoader("mireport.report", "inline_report_templates"),
            keep_trailing_newline=True,
            trim_blocks=True,
            lstrip_blocks=True,
            undefined=StrictUndefined if L.isEnabledFor(logging.DEBUG) else Undefined,
        )
        env.globals.update(
            {
                PresentationStyle.__name__: PresentationStyle,
                TableStyle.__name__: TableStyle,
                "now_utc": lambda: datetime.now(UTC),
                "labelLanguage": label_language,
                "labelQNameFallback": label_language is None,
                "label_overrides_by_concept": self._labelOverrides,
                "section_label": lambda s: layout.section_label(s, lang),
                "page_group_key": lambda s: layout.page_group_key(s, lang),
            }
        )
        env.filters.update(
            {
                "tidyTdValue": tidyTdValue,
                "cssmin": cssmin,
            }
        )
        template = env.get_template("inline-report-presentation.html.jinja")
        fn_manager = FootnoteManager(self._footnotes).register_refs(
            sections, self._footnotesByGroup
        )

        background_image_data_url = (
            self.theme.background_image.as_data_url(max_width=200)
            if self.theme.background_image
            else ""
        )
        logo_image_data_url = (
            self.theme.logo_image.as_data_url(max_width=200)
            if self.theme.logo_image
            else ""
        )

        html_content = template.render(
            aoix={
                "defaults": self.getDefaultAspectsForAoix(),
                "periods": self.getPeriodsForAoix(),
                "schema_ref": self.getSchemaRefForAoix(),
                "namespaces": self.getNamespacesForAoix(),
            },
            reportInfo={
                "entityName": self._entityName,
                "defaultPeriod": self.defaultPeriod,
                "factCount": self.factCount,
                "title": self._reportTitle,
                "subtitle": self._reportSubtitle,
                "optionalLogoImage": self.theme.logo_image,
                "optionalCoverImage": self.theme.cover_image,
                "language": self.language,
            },
            software={
                "version": mireport.__version__,
                "name": "EFRAG Digital Template to XBRL Converter",
            },
            documentInfo=self.getDocumentInformation(),
            facts=self.facts,
            sections=sections,
            toc=toc,
            uniqueFactCount=len(frozenset(self._facts)),
            theme=self.theme.displayMode,
            colour=self.theme.colour,
            backgroundImageDataUrl=background_image_data_url,
            logoImageDataUrl=logo_image_data_url,
            introduction=self._introduction,
            backCoverMatter=self._backCoverMatter,
            footnotes_by_group=self._footnotesByGroup,
            footnote_manager=fn_manager,
        )

        try:
            start_time = time.perf_counter_ns()
            parser = ixbrltemplates.Parser(
                "http://www.xbrl.org/inlineXBRL/transformation/2022-02-16",
                self.taxonomy.dimensionContainer.value,
            )
            ixbrl_content = parser.parse(html_content).strip()
            self._generatedReport = ixbrl_content
            elapsed = time.perf_counter_ns() - start_time
            L.info(
                f"aoix parsing and transformation took {elapsed / 1_000_000:.2f} milliseconds"
            )
            return ixbrl_content
        except ixbrltemplates.ParseError as e:
            errors = []
            errors.append("aoix parse error:")
            errors.append(e.message)
            (line, offset) = ixbrltemplates.lineAndOffset(html_content, e._location)
            errors.append(line)
            errors.append(" " * offset + "^")
            message = "\n".join(errors)
            raise InlineReportException(message) from e

    def _getSafeEntityName(self) -> str:
        safeName = zipSafeString(self._entityName, fallback="Sample")
        return safeName

    def getInlineReportPackage(
        self,
        *,
        docsetMembers: Sequence[FilelikeAndFileName] = (),
        attachments: Sequence[FilelikeAndFileName] = (),
    ) -> FilelikeAndFileName:
        """Package this report as an unconstrained report package.

        @docsetMembers and @attachments are passed straight through to
        :func:`buildReportPackage`, which documents them and owns the layout.
        They are arguments rather than report state so that there is no
        register-before-build ordering for a caller to get wrong.
        """
        return buildReportPackage(
            self.getInlineReport(),
            topLevel=f"{self._getSafeEntityName()}_{self.defaultPeriod.end.year}",
            docsetMembers=docsetMembers,
            attachments=attachments,
        )

    def getInlineReport(self) -> FilelikeAndFileName:
        yearEnd = self.defaultPeriod.end.year
        filename = f"{self._getSafeEntityName()}_{yearEnd}_XBRL_Report.html"
        return FilelikeAndFileName(
            fileContent=self._constructInlineReport().encode("UTF-8"), filename=filename
        )
