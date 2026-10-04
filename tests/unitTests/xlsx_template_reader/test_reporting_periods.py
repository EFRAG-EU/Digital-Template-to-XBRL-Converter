"""The periods a disclosure configuration declares: a current one, an optional prior one."""

from __future__ import annotations

import copy

import pytest

from mireport.conversionresults import ConversionResultsBuilder, Severity
from mireport.data.disclosures import VSME_DEFAULTS
from mireport.exceptions import InlineReportException
from mireport.report import InlineReport
from mireport.xlsx_template_reader.processor import XlsxProcessor

SAMPLE = "digital-templates/VSME-Digital-Template-Sample-1.3.0.xlsx"
START, END = "template_reporting_period_startdate", "template_reporting_period_enddate"

pytestmark = pytest.mark.slow


def _report(*periods: dict) -> tuple[InlineReport, ConversionResultsBuilder]:
    defaults = copy.deepcopy(VSME_DEFAULTS)
    defaults["periods"] = list(periods)
    results = ConversionResultsBuilder(consoleOutput=False)
    report = XlsxProcessor.from_file(SAMPLE, results, defaults).createReport()
    return report, results


def _errors(results: ConversionResultsBuilder) -> list[str]:
    return [m.messageText for m in results.messages if m.severity is Severity.ERROR]


def test_a_single_period_is_the_current_one_as_it_always_was() -> None:
    report, results = _report({"start": START, "end": END, "name": "cur"})
    assert report.defaultReportPeriod.name == "cur"
    assert report.priorReportPeriod is None
    assert not _errors(results)


def test_a_prior_period_whose_cells_are_not_in_the_workbook_is_left_out_not_an_error() -> (
    None
):
    report, results = _report(
        {"start": START, "end": END, "name": "cur"},
        {
            "start": "no_such_start",
            "end": "no_such_end",
            "name": "prior",
            "role": "prior",
        },
    )
    assert report.priorReportPeriod is None
    assert not report.hasNamedPeriod("prior")
    assert not _errors(results)


def test_a_prior_period_whose_cells_are_there_is_the_prior_period() -> None:
    # The template has one period, so name its own cells: this is about the plumbing, not the dates.
    report, results = _report(
        {"start": START, "end": END, "name": "cur"},
        {"start": START, "end": END, "name": "prior", "role": "prior"},
    )
    assert (
        report.priorReportPeriod is not None
        and report.priorReportPeriod.name == "prior"
    )
    assert report.defaultReportPeriod.name == "cur"
    assert not _errors(results)


def test_the_prior_period_may_be_listed_first() -> None:
    report, _ = _report(
        {"start": START, "end": END, "name": "prior", "role": "prior"},
        {"start": START, "end": END, "name": "cur"},
    )
    assert report.defaultReportPeriod.name == "cur"
    assert (
        report.priorReportPeriod is not None
        and report.priorReportPeriod.name == "prior"
    )


def test_the_first_current_period_is_the_default_not_the_last() -> None:
    """The last period added used to become the default, so a second entry silently displaced the first."""
    report, _ = _report(
        {"start": START, "end": END, "name": "first"},
        {"start": START, "end": END, "name": "second"},
    )
    assert report.defaultReportPeriod.name == "first"
    assert report.hasNamedPeriod("second")


def test_an_unknown_role_is_a_configuration_error() -> None:
    with pytest.raises(InlineReportException, match="unknown role 'later'"):
        _report({"start": START, "end": END, "name": "cur", "role": "later"})
