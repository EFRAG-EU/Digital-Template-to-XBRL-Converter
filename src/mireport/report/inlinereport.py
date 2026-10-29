from __future__ import annotations

import logging
import os
import time
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import TYPE_CHECKING
from unicodedata import name as unicode_name

if TYPE_CHECKING:
    from collections.abc import Sequence

    from mireport.conversionresults import ConversionResultsBuilder

import ixbrltemplates
from babel import Locale
from babel.support import NullTranslations
from jinja2 import Environment, PackageLoader, StrictUndefined, Undefined
from markupsafe import Markup
from rcssmin import cssmin

import mireport
from mireport.data.disclosures import getReportTranslations
from mireport.exceptions import InlineReportException
from mireport.filesupport import FilelikeAndFileName, zipSafeString
from mireport.localise import (
    as_xmllang,
    decimal_symbol,
    get_locale_from_str,
    group_symbol,
)
from mireport.report.aoix import aoix_quote
from mireport.report.disclosure_layout import (
    DisclosureLayoutStrategy,
    withoutContinuations,
)
from mireport.report.fact import Fact, Symbol
from mireport.report.footnote import Footnote, FootnoteManager
from mireport.report.layout import ReportLayoutOrganiser, TableStyle
from mireport.report.periods import (
    DurationPeriodHolder,
    PeriodHolder,
)
from mireport.report.report import Report
from mireport.report.reportpackage import buildReportPackage
from mireport.report.theme import ReportTheme
from mireport.stringutil import NumberGroupingApostrophes
from mireport.taxonomy import PresentationStyle, Taxonomy

L = logging.getLogger(__name__)


def _now_utc() -> datetime:
    """The report's generation time. SOURCE_DATE_EPOCH (https://reproducible-builds.org/specs/
    source-date-epoch/) pins it, so a build can be made byte-for-byte reproducible."""
    if epoch := os.environ.get("SOURCE_DATE_EPOCH", "").strip():
        return datetime.fromtimestamp(int(epoch), UTC)
    return datetime.now(UTC)


def msgid(text: str) -> str:
    """Marks ``text`` as a msgid where it is defined; the template translates it."""
    return text


class _MissRecorder(NullTranslations):
    """The last fallback of a report's translations: notes each msgid that reached it and
    gives it back untranslated."""

    def __init__(self, misses: list[str]):
        super().__init__()
        self._misses = misses

    def gettext(self, message: str) -> str:
        if message not in self._misses:
            self._misses.append(message)
        return message


class InlineReport(Report):
    """A Report as an Inline XBRL document: what it says in which language and locale, how it
    looks, and how it is packaged."""

    def __init__(self, taxonomy: Taxonomy, outputLocale: Locale | None = None):
        super().__init__(taxonomy)
        self._entityName: str = "Sample"
        # When set, rendering fails if any fact would appear nowhere in the report, instead of
        # leaving it out. A report built from data we did not author wants this; the Excel
        # route has facts (e.g. inconsistent duplicates) it knowingly leaves out.
        self.requireAllFactsRendered: bool = False
        self._generatedReport: str | None = None
        self._reportTitle: str = ""
        self._reportSubtitle: str = ""
        self._introduction: str | None = None
        self._backCoverMatter: str | None = None
        self._theme: ReportTheme = ReportTheme.default()
        self._footnotesByGroup: dict[str, Footnote] = {}
        self._labelOverrides: dict[str, str] = {}
        # The report's fixed strings that had no translation into a non-English output
        # language, in the order the report first used them; filled in when it is rendered.
        self.untranslatedStrings: list[str] = []
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

        self._numericTransform: str = numeric_transform

    def addPeriod(self, name: str, duration: DurationPeriodHolder) -> bool:
        """As Report's, but only for a period of whole days: the report shows dates, never a
        time of day, so a period with one is refused here, before any fact refers to it,
        rather than when the report is rendered. A fact's period is a declared one or an
        instant at either end of one, so this covers every period the report shows."""
        if not duration.isWholeDays:
            raise InlineReportException(
                f"Period {name!r} ({duration}) has a time of day; an inline report shows "
                "only whole days."
            )
        return super().addPeriod(name, duration)

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

    def getDefaultAspectsForAoix(self) -> str:
        defaults = [
            f"{{{{ default numeric-transform = {self._numericTransform} }}}}",
            '{{ default decimals = "INF" }}',
        ]
        if (currency := self._defaultCurrency) is not None:
            defaults.append(f"{{{{ default monetary-units = {currency.localName} }}}}")
        if (identifier := self._entityIdentifier) is not None:
            defaults.append(
                f"{{{{ default entity-identifier = {aoix_quote(identifier)} }}}}"
            )
        if (scheme := self._entityScheme) is not None:
            defaults.append(f"{{{{ default entity-scheme = {aoix_quote(scheme)} }}}}")
        return "\n".join(defaults)

    @property
    def theme(self) -> ReportTheme:
        return self._theme

    @theme.setter
    def theme(self, value: ReportTheme) -> None:
        self._theme = value

    def setEntityName(self, name: str) -> None:
        self._entityName = name

    @property
    def language(self) -> str:
        """Returns the language of the report (as a BCP 47 string like `xml:lang` uses): its
        output locale's.

        Useful for requesting the right taxonomy labels and other language-sensitive output."""
        return as_xmllang(self._outputLocale)

    @property
    def outputLocale(self) -> Locale:
        """The locale numbers and dates are written in."""
        return self._outputLocale

    def getPeriodsForAoix(self) -> str:
        p = []
        for name, reportPeriod in self._periods.items():
            duration = reportPeriod.duration
            p.append(
                f'{{{{ period {name} "{duration.firstDay}" "{duration.lastDay}" }}}}'
            )
        p.append(f"{{{{ default period = {self._defaultPeriodName} }}}}")
        return "\n".join(p)

    def addFootnoteToFacts(
        self,
        content: str | Markup,
        facts: list[Fact],
        *,
        group: str | None = None,
    ) -> Footnote:
        """Create a footnote and attach it to the given facts directly; a ``group``'s footnote
        is shown under that section's heading."""
        footnote = super().addFootnoteToFacts(content, facts)
        if group is not None:
            self._footnotesByGroup[group] = footnote
        return footnote

    def getNamespacesForAoix(self) -> str:
        """Declarations for the prefixes the facts use (see Report.usedNamespaces); aoix adds
        those of the elements it writes itself."""
        # {{ namespace utr = "http://www.xbrl.org/2009/utr" }}
        return "\n".join(
            f'{{{{ namespace {p} = "{n}" }}}}' for p, n in self.usedNamespaces().items()
        )

    def getSchemaRefForAoix(self) -> str:
        # {{ schema-ref "https://xbrl.efrag.org/taxonomy/vsme/2024-12-17/vsme-all.xsd" }}
        lines = []
        for url in sorted(self.schemaRefs):
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
            msgid("Entity Name"): self._entityName,
            msgid("Entity Identifier"): self._entityIdentifier,
            msgid("Entity Identifier Scheme"): self._entityScheme,
            # Not every taxonomy has monetary facts, so there may be no report currency.
            msgid("Report currency"): (
                self._defaultCurrency.localName if self._defaultCurrency else None
            ),
        }
        for k, v in meta.items():
            if v is not None:
                addDict(k, v)
        addDict(msgid("Report period"), self.defaultPeriod, "render_duration_period")
        if (prior := self.priorPeriod) is not None:
            addDict(msgid("Prior period"), prior, "render_duration_period")

        separator = decimal_symbol(self._outputLocale)
        bits.append(
            {
                "key": msgid("Decimal separator"),
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
        if nil := [f for f in self._facts if f.isNil]:
            concepts = ", ".join(sorted({str(f.concept.qname) for f in nil}))
            raise InlineReportException(
                f"Cannot render nil facts yet; the following concepts have them: {concepts}."
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

        layout = DisclosureLayoutStrategy.for_entry_point(self._taxonomy.entryPointSet)
        rl = ReportLayoutOrganiser(self._taxonomy, self)
        sections = rl.organise(layout)
        toc = withoutContinuations(layout.build_toc(sections, lang), sections)

        translations = getReportTranslations(
            self._taxonomy.entryPointSet, self._outputLocale
        )
        if self._outputLocale.language != "en":
            translations.add_fallback(_MissRecorder(self.untranslatedStrings))

        env = Environment(
            loader=PackageLoader("mireport.report", "inline_report_templates"),
            extensions=["jinja2.ext.i18n"],
            keep_trailing_newline=True,
            trim_blocks=True,
            lstrip_blocks=True,
            undefined=StrictUndefined if L.isEnabledFor(logging.DEBUG) else Undefined,
        )
        env.install_gettext_translations(translations, newstyle=True)  # type: ignore[attr-defined]
        env.globals.update(
            {
                PresentationStyle.__name__: PresentationStyle,
                TableStyle.__name__: TableStyle,
                "now_utc": _now_utc,
                "labelLanguage": label_language,
                "labelQNameFallback": label_language is None,
                "label_overrides_by_concept": self._labelOverrides,
                "section_label": lambda s: layout.section_label(s, lang),
                "page_group_key": lambda s: layout.page_group_key(s, lang),
            }
        )
        env.filters.update(
            {
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
                "priorPeriod": self.priorPeriod,
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
        if self.untranslatedStrings:
            L.info(
                "Report strings with no %s translation: %s",
                self.language,
                self.untranslatedStrings,
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

    @property
    def packageTopLevelName(self) -> str:
        """The top-level directory/entry name :func:`buildReportPackage` uses
        for this report, e.g. for a caller assembling a package itself
        (merging supplementary content into the report bytes first) rather
        than going through :meth:`getInlineReportPackage`."""
        return f"{self._getSafeEntityName()}_{self.defaultPeriod.lastDay.year}"

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
            topLevel=self.packageTopLevelName,
            docsetMembers=docsetMembers,
            attachments=attachments,
        )

    def reportUntranslatedStrings(self, results: ConversionResultsBuilder) -> None:
        """Say, as dev info, which of the rendered report's fixed strings had no translation."""
        if self.untranslatedStrings:
            results.addDevInfoMessage(
                f"Report strings with no {self.language} translation: "
                f"{'; '.join(self.untranslatedStrings)}"
            )

    def getInlineReport(self) -> FilelikeAndFileName:
        yearEnd = self.defaultPeriod.lastDay.year
        filename = f"{self._getSafeEntityName()}_{yearEnd}_XBRL_Report.html"
        return FilelikeAndFileName(
            fileContent=self._constructInlineReport().encode("UTF-8"), filename=filename
        )
