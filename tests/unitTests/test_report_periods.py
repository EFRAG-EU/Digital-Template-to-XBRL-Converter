"""A report's periods: the current one, an optional prior one, and any others."""

from __future__ import annotations

from datetime import date

import pytest

from mireport.exceptions import InlineReportException
from mireport.report import InlineReport
from mireport.report.model import PeriodRole
from mireport.taxonomy import getTaxonomy, listTaxonomies, loadBuiltInTaxonomyJSON


@pytest.fixture
def report() -> InlineReport:
    if not listTaxonomies():
        loadBuiltInTaxonomyJSON()
    taxonomy = getTaxonomy(
        next(ep for ep in listTaxonomies() if any("vsme" in uri.lower() for uri in ep))
    )
    report = InlineReport(taxonomy)
    report.addDurationPeriod("cur", date(2025, 1, 1), date(2025, 12, 31))
    report.addDurationPeriod("prior", date(2024, 1, 1), date(2024, 12, 31))
    report.addDurationPeriod("baseline", date(2020, 1, 1), date(2020, 12, 31))
    return report


def test_a_report_has_no_prior_period_until_it_is_given_one(
    report: InlineReport,
) -> None:
    report.setDefaultPeriodName("cur")
    assert report.priorReportPeriod is None
    assert report.priorPeriod is None


def test_roles(report: InlineReport) -> None:
    report.setDefaultPeriodName("cur")
    report.setPriorPeriodName("prior")
    role = {p.name: report.periodRole(p) for p in report.reportingPeriods}
    assert role == {
        "cur": PeriodRole.CURRENT,
        "prior": PeriodRole.PRIOR,
        "baseline": PeriodRole.OTHER,
    }
    assert (
        report.priorReportPeriod is not None
        and report.priorReportPeriod.name == "prior"
    )
    assert report.priorPeriod == report.getReportPeriod("prior").duration


def test_periods_are_met_current_then_prior_then_the_rest_in_declared_order(
    report: InlineReport,
) -> None:
    report.addDurationPeriod("target", date(2030, 1, 1), date(2030, 12, 31))
    report.setDefaultPeriodName("cur")
    report.setPriorPeriodName("prior")
    assert [p.name for p in report.reportingPeriods] == [
        "cur",
        "prior",
        "baseline",
        "target",
    ]


def test_the_order_does_not_depend_on_the_order_they_were_declared_in(
    report: InlineReport,
) -> None:
    report.setDefaultPeriodName("baseline")  # declared last-but-one, current anyway
    report.setPriorPeriodName("cur")
    assert [p.name for p in report.reportingPeriods][:2] == ["baseline", "cur"]


def test_a_period_cannot_be_both_current_and_prior(report: InlineReport) -> None:
    report.setDefaultPeriodName("cur")
    with pytest.raises(InlineReportException, match="current period"):
        report.setPriorPeriodName("cur")
    report.setPriorPeriodName("prior")
    with pytest.raises(InlineReportException, match="prior period"):
        report.setDefaultPeriodName("prior")


def test_only_a_declared_period_can_be_prior(report: InlineReport) -> None:
    with pytest.raises(InlineReportException, match="no such period"):
        report.setPriorPeriodName("nope")


def test_a_fact_with_no_period_of_its_own_is_in_the_current_one(
    report: InlineReport,
) -> None:
    report.setDefaultPeriodName("cur")
    assert report.defaultReportPeriod.name == "cur"
    assert report.defaultPeriod == report.getReportPeriod("cur").duration
