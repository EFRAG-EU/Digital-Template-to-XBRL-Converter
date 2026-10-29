"""The report's fixed strings in the output language: the checked-in catalogs, how a locale finds
one, and what a rendered report says."""

from __future__ import annotations

from io import BytesIO
from pathlib import Path
from typing import Any

import pytest
from babel import Locale
from babel.messages.catalog import Catalog
from babel.messages.extract import extract
from babel.messages.mofile import write_mo
from babel.messages.pofile import read_po
from babel.support import NullTranslations, Translations
from jinja2.ext import babel_extract

import mireport
from mireport.conversionresults import ConversionResultsBuilder
from mireport.data.disclosures import VSME_DEFAULTS, getReportTranslations
from mireport.entrypoints import entryPointSetOf
from mireport.report import InlineReport
from mireport.report.factbuilder import FactBuilder
from mireport.xbrljson_reader import XbrlJsonProcessor
from mireport.xlsx_template_reader.processor import XlsxProcessor

SRC = Path(mireport.__file__).parent
CATALOGS = SRC / "data" / "disclosures" / "translations" / "vsme"
TEMPLATES = SRC / "report" / "inline_report_templates"
SAMPLE = "digital-templates/VSME-Digital-Template-Sample-1.3.0.xlsx"

VSME = "https://xbrl.efrag.org/taxonomy/vsme/2026-05-01"
VSME_ENTRY_POINTS = entryPointSetOf(f"{VSME}/vsme-all.xsd")
ENTITY = "lei:529900T8BM49AURSDO55"
DURATION = "2026-01-01T00:00:00/2027-01-01T00:00:00"
ONE_COUNT = "This report contains %(factCount)s XBRL facts."


@pytest.fixture(scope="module", autouse=True)
def _taxonomies() -> None:
    mireport.loadBuiltInTaxonomyJSON()


def usedMsgids() -> set[str]:
    """Every msgid the report templates translate, and those inlinereport.py marks."""
    used = set()
    for template in TEMPLATES.glob("*.jinja"):
        with template.open("rb") as f:
            used |= {message for _, message, *_ in extract(babel_extract, f)}
    with (SRC / "report" / "inlinereport.py").open("rb") as f:
        used |= {m for _, m, *_ in extract("python", f, keywords={"msgid": None})}
    return used


def test_every_catalog_msgid_is_in_the_report() -> None:
    used = usedMsgids()
    assert len(list(CATALOGS.glob("*.po"))) == 11
    for po in CATALOGS.glob("*.po"):
        with po.open("rb") as f:
            msgids = {m.id for m in read_po(f) if m.id}
        assert msgids <= used, po.name


def test_a_language_has_its_catalog() -> None:
    translations = getReportTranslations(VSME_ENTRY_POINTS, Locale.parse("de"))
    assert translations.gettext("Report generated") == "Bericht erstellt"


def test_a_territory_falls_back_to_its_language() -> None:
    translations = getReportTranslations(VSME_ENTRY_POINTS, Locale.parse("de_AT"))
    assert translations.gettext("Report generated") == "Bericht erstellt"


def test_a_language_with_no_catalog_is_untranslated() -> None:
    translations = getReportTranslations(VSME_ENTRY_POINTS, Locale.parse("sv"))
    assert type(translations) is NullTranslations


def germanJson() -> dict[str, Any]:
    """A small VSME report whose one text fact is in German."""
    return {
        "documentInfo": {
            "documentType": "https://xbrl.org/2021/xbrl-json",
            "namespaces": {
                "lei": "http://standards.iso.org/iso/17442",
                "utr": "http://www.xbrl.org/2009/utr",
                "vsme": f"{VSME}/vsme",
            },
            "taxonomy": [f"{VSME}/vsme-all.xsd"],
        },
        "facts": {
            "f1": {
                "value": "1250.5",
                "decimals": 1,
                "dimensions": {
                    "concept": "vsme:TotalEnergyConsumption",
                    "entity": ENTITY,
                    "period": DURATION,
                    "unit": "utr:MWh",
                },
            },
            "f2": {
                "value": "Ein Verhaltenskodex für Lieferanten.",
                "dimensions": {
                    "concept": "vsme:SpecificationOfOtherTypesOfContentCoveredByTheCodeOfConductOrHumanRightsPolicy",
                    "entity": ENTITY,
                    "period": DURATION,
                    "language": "de",
                },
            },
        },
    }


def jsonReport() -> InlineReport:
    return XbrlJsonProcessor(germanJson(), ConversionResultsBuilder()).createReport()


def html(report: InlineReport) -> str:
    return report.getInlineReport().fileContent.decode("utf-8")


def test_a_german_xbrl_json_report_is_translated() -> None:
    report = jsonReport()
    page = html(report)
    assert report.language == "de"
    assert "<dt>Bericht erstellt</dt>" in page
    assert "<h2>Inhaltsverzeichnis</h2>" in page
    assert "<h2>Dokumentinformationen</h2>" in page
    assert not report.untranslatedStrings


def test_translations_are_escaped(monkeypatch: pytest.MonkeyPatch) -> None:
    catalog = Catalog(locale="de")
    catalog.add("Report generated", "Bericht <erstellt> & gedruckt")
    mo = BytesIO()
    write_mo(mo, catalog)
    mo.seek(0)
    monkeypatch.setattr(
        "mireport.report.inlinereport.getReportTranslations",
        lambda *_: Translations(mo),
    )
    assert "<dt>Bericht &lt;erstellt&gt; &amp; gedruckt</dt>" in html(jsonReport())


def excelReport(language: str, *, duplicates: int = 0) -> InlineReport:
    """The 1.3.0 sample in this language, with the first few facts given an identical copy so
    that more facts are shown than there are distinct ones."""
    report = XlsxProcessor.from_file(
        SAMPLE,
        ConversionResultsBuilder(consoleOutput=False),
        VSME_DEFAULTS,
        outputLocale=Locale.parse(language),
    ).createReport()
    for fact in list(report.facts)[:duplicates]:
        report.addFact(FactBuilder.fromFact(report, fact).buildFact())
    return report


@pytest.mark.slow
@pytest.mark.xdist_group("report-translations-excel")
class TestExcelReport:
    def test_german(self) -> None:
        report = excelReport("de")
        page = html(report)
        assert "<dt>Unternehmensname</dt>" in page
        assert (
            "<p>Alle Angaben beziehen sich auf den oben genannten Berichtszeitraum"
            in page
        )
        assert "<p>Dieser Bericht enthält 144 XBRL-Fakten.</p>" in page
        assert not report.untranslatedStrings

    def test_german_with_more_facts_shown_than_distinct(self) -> None:
        page = html(excelReport("de", duplicates=3))
        assert (
            "<p>Dieser Bericht enthält 147 XBRL-Fakten (144 eindeutige Fakten).</p>"
            in page
        )

    def test_english(self) -> None:
        report = excelReport("en")
        page = html(report)
        assert "<dt>Entity Name</dt>" in page
        assert "<dt>Report generated</dt>" in page
        assert "<h2>Table of contents</h2>" in page
        assert "<p>This report contains 144 XBRL facts.</p>" in page
        assert report.untranslatedStrings == []

    def test_english_with_more_facts_shown_than_distinct(self) -> None:
        page = html(excelReport("en", duplicates=3))
        assert "<p>This report contains 147 XBRL facts (144 unique facts).</p>" in page
