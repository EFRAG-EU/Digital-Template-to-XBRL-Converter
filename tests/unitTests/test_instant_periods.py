"""An instant fact is reported at the start or at the end of a report period, and says which.

The start of 2025 and the end of 2024 are one XBRL instant (``2024-12-31``), but an opening
balance of 2025 and a closing balance of 2024 are not the same statement, so a fact keeps which it
meant: aoix is told, and the page shows the first day of 2025 for one and the last of 2024 for the
other.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from datetime import date
from typing import Any

import pytest
from jinja2 import Environment, PackageLoader

import mireport.taxonomy as _taxonomy_module
from mireport.exceptions import InlineReportException
from mireport.report import InlineReport
from mireport.report.factbuilder import FactBuilder
from mireport.report.model import FactPeriod, PeriodPart
from mireport.report.periods import DurationPeriodHolder, InstantPeriodHolder
from mireport.taxonomy import Taxonomy, loadTaxonomyJSON
from mireport.xml import getBootstrapQNameMaker

_NS = "https://example.com/tp"
_ENTRY = "test://periods/instants"
_ROLE = "https://example.com/tp/role/Group"
_LABEL = "http://www.xbrl.org/2003/role/label"
_Q = getBootstrapQNameMaker()
_Q.addNamespacePrefix("utr", "http://www.xbrl.org/2009/utr")


@pytest.fixture(autouse=True)
def _isolated_taxonomy_registry() -> Iterator[None]:
    before = set(_taxonomy_module._TAXONOMIES)
    yield
    for entryPoint in set(_taxonomy_module._TAXONOMIES) - before:
        del _taxonomy_module._TAXONOMIES[entryPoint]


def _concept(label: str, *, period: str, abstract: bool = False) -> dict[str, Any]:
    kind = "xbrli:stringItemType" if abstract else "xbrli:decimalItemType"
    concept: dict[str, Any] = {
        "labels": {"en": {_LABEL: label}},
        "dataType": kind,
        "baseDataType": kind,
        "periodType": period,
    }
    if abstract:
        concept["abstract"] = True
    else:
        concept["numeric"] = True
    return concept


def _taxonomy() -> Taxonomy:
    return loadTaxonomyJSON(
        {
            "entryPoint": _ENTRY,
            "namespaces": {"tp": _NS, "xs": "http://www.w3.org/2001/XMLSchema"},
            "concepts": {
                "tp:Group": _concept("Group", period="duration", abstract=True),
                "tp:Stock": _concept("Stock", period="instant"),
                "tp:Emissions": _concept("Emissions", period="duration"),
            },
            "presentation": {
                _ROLE: {
                    "definition": "Group",
                    "rows": [[0, "tp:Group"], [1, "tp:Stock"], [1, "tp:Emissions"]],
                }
            },
            "dimensions": {"_defaults": {}},
        }
    )


def _report() -> InlineReport:
    report = InlineReport(_taxonomy())
    report.setEntity("http://standards.iso.org/iso/17442", "529900T8BM49AURSDO55")
    report.addDurationPeriod("prior", date(2024, 1, 1), date(2024, 12, 31))
    report.addDurationPeriod("cur", date(2025, 1, 1), date(2025, 12, 31))
    report.setDefaultPeriodName("cur")
    return report


def _builder(report: InlineReport, name: str, value: Any) -> FactBuilder:
    return (
        FactBuilder(report)
        .setConcept(report.taxonomy.getConcept(name))
        .setValue(value)
        .setSimpleUnit(_Q.fromString("utr:kg"))
        .setDecimals(0)
    )


def _contexts(html: str) -> list[str]:
    return re.findall(r"<xbrli:instant>([^<]+)</xbrli:instant>", html)


def _html(report: InlineReport) -> str:
    return report.getInlineReport().fileContent.decode("utf-8")


class TestFactPeriodsFromTheBuilder:
    def test_an_instant_concept_is_reported_at_the_end_by_default(self) -> None:
        report = _report()
        fact = _builder(report, "tp:Stock", 5).setNamedPeriod("cur").buildFact()
        assert fact.period == FactPeriod(report.getReportPeriod("cur"), PeriodPart.End)

    def test_a_duration_concept_is_reported_for_the_whole_period(self) -> None:
        report = _report()
        fact = _builder(report, "tp:Emissions", 5).setNamedPeriod("cur").buildFact()
        assert fact.period == FactPeriod(
            report.getReportPeriod("cur"), PeriodPart.Whole
        )

    def test_the_start_of_a_period_can_be_asked_for(self) -> None:
        report = _report()
        fact = (
            _builder(report, "tp:Stock", 5)
            .setNamedPeriod("cur", at=PeriodPart.Start)
            .buildFact()
        )
        assert fact.period.isStart

    def test_a_duration_concept_has_no_start(self) -> None:
        report = _report()
        builder = _builder(report, "tp:Emissions", 5).setNamedPeriod(
            "cur", at=PeriodPart.Start
        )
        with pytest.raises(InlineReportException, match="cannot be reported"):
            builder.buildFact()

    def test_the_whole_of_a_period_is_not_a_choice_of_start_or_end(self) -> None:
        with pytest.raises(InlineReportException, match="'at'"):
            _builder(_report(), "tp:Stock", 5).setNamedPeriod(
                "cur", at=PeriodPart.Whole
            )

    def test_a_duration_period_is_no_use_to_an_instant_concept(self) -> None:
        report = _report()
        builder = _builder(report, "tp:Stock", 5).setPeriod(
            report.getReportPeriod("cur").duration
        )
        with pytest.raises(InlineReportException, match="cannot be reported"):
            builder.buildFact()


class TestLocatePeriod:
    def test_a_declared_duration_is_the_whole_of_its_period(self) -> None:
        report = _report()
        cur = report.getReportPeriod("cur")
        assert report.locatePeriod(cur.duration) == FactPeriod(cur, PeriodPart.Whole)

    def test_an_instant_is_the_end_of_the_period_it_ends(self) -> None:
        report = _report()
        cur = report.getReportPeriod("cur")
        assert report.locatePeriod(cur.duration.endInstant) == FactPeriod(
            cur, PeriodPart.End
        )

    def test_one_periods_end_is_the_next_ones_start_and_is_taken_as_the_end(
        self,
    ) -> None:
        report = _report()
        cur, prior = report.getReportPeriod("cur"), report.getReportPeriod("prior")
        assert cur.duration.startInstant == prior.duration.endInstant
        assert report.locatePeriod(cur.duration.startInstant) == FactPeriod(
            prior, PeriodPart.End
        )

    def test_the_start_of_the_earliest_period_is_its_start(self) -> None:
        report = _report()
        prior = report.getReportPeriod("prior")
        assert report.locatePeriod(prior.duration.startInstant) == FactPeriod(
            prior, PeriodPart.Start
        )

    def test_a_period_the_report_does_not_have_is_an_error(self) -> None:
        report = _report()
        stranger = DurationPeriodHolder.fromDays(date(2023, 1, 1), date(2023, 12, 31))
        with pytest.raises(InlineReportException, match="not one of the report's"):
            report.locatePeriod(stranger)
        with pytest.raises(InlineReportException, match="not one of the report's"):
            report.locatePeriod(InstantPeriodHolder(stranger.end.replace(hour=5)))


class TestFactPeriod:
    def test_what_a_period_means_and_what_xbrl_calls_it_can_differ(self) -> None:
        report = _report()
        cur, prior = report.getReportPeriod("cur"), report.getReportPeriod("prior")
        opening = FactPeriod(cur, PeriodPart.Start)
        closing = FactPeriod(prior, PeriodPart.End)
        assert opening.oim == closing.oim
        assert opening != closing

    def test_the_parts_have_their_aoix_period_types(self) -> None:
        cur = _report().getReportPeriod("cur")
        assert [
            FactPeriod(cur, part).aoixPeriodType
            for part in (PeriodPart.Whole, PeriodPart.Start, PeriodPart.End)
        ] == ["duration", "instant-start", "instant"]


class TestInTheReport:
    @staticmethod
    def _facts(report: InlineReport) -> None:
        for at, value in ((PeriodPart.End, 11), (PeriodPart.Start, 7)):
            report.addFact(
                _builder(report, "tp:Stock", value)
                .setNamedPeriod("cur", at=at)
                .buildFact()
            )
        report.addFact(
            _builder(report, "tp:Emissions", 3).setNamedPeriod("cur").buildFact()
        )

    def test_both_instants_of_a_period_are_facts_in_their_own_right(self) -> None:
        report = _report()
        self._facts(report)
        assert len(report.getFacts(report.taxonomy.getConcept("tp:Stock"))) == 2

    def test_each_goes_into_the_context_xbrl_would_give_it(self) -> None:
        report = _report()
        self._facts(report)
        # closing 2025, and opening 2025 (the instant at the end of the day before the first)
        assert sorted(_contexts(_html(report))) == ["2024-12-31", "2025-12-31"]

    def test_the_page_says_the_first_day_of_an_opening_balance_and_the_last_of_a_closing_one(
        self,
    ) -> None:
        report = _report()
        self._facts(report)
        html = _html(report)
        # the closing balance leads its line, the opening balance is its comparative
        assert 'fact-comparative-period">At 2025-01-01<' in html
        # the closing balance of the report's own period is what a fact with no period means
        assert "At 2025-12-31" not in html


class TestRenderMacros:
    @staticmethod
    def _macros() -> Any:
        env = Environment(
            loader=PackageLoader("mireport.report", "inline_report_templates"),
            trim_blocks=True,
            lstrip_blocks=True,
        )
        return env.get_template("_macros.html.jinja").module

    @pytest.mark.parametrize(
        ("part", "short", "shown"),
        [
            (PeriodPart.Whole, False, "2025-01-01 &#8211; 2025-12-31"),
            (PeriodPart.Whole, True, "2025"),
            (PeriodPart.Start, False, "2025-01-01"),
            (PeriodPart.Start, True, "2025-01-01"),
            (PeriodPart.End, False, "2025-12-31"),
            (PeriodPart.End, True, "2025-12-31"),
        ],
    )
    def test_each_part_of_a_period_is_shown_as_the_day_it_is(
        self, part: PeriodPart, short: bool, shown: str
    ) -> None:
        factPeriod = FactPeriod(_report().getReportPeriod("cur"), part)
        assert str(self._macros().render_period(factPeriod, short)).strip() == shown

    def test_a_fact_that_is_an_instant_says_so(self) -> None:
        report = _report()
        macros = self._macros()
        opening = (
            _builder(report, "tp:Stock", 1)
            .setNamedPeriod("cur", at=PeriodPart.Start)
            .buildFact()
        )
        whole = _builder(report, "tp:Emissions", 1).buildFact()
        assert str(macros.render_fact_period(opening)).strip() == "At 2025-01-01"
        assert str(macros.render_fact_period(whole)).strip() == (
            "2025-01-01 &#8211; 2025-12-31"
        )
