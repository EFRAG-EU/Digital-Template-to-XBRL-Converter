"""A real VSME report with a prior period for every fact, in every layout mode it has.

The VSME template reports one period, so there is no Excel data for a prior one. This builds the
report from the sample workbook, then adds a prior-period copy of every fact (numbers a tenth
smaller, so they differ), and renders it strictly: it raises if any fact appears nowhere. The VSME
report has lists, explicit-dimension tables (members as columns and as rows) and typed-dimension
tables, so this takes a prior period through all of them.
"""

from __future__ import annotations

import re

import pytest
from dateutil.relativedelta import relativedelta

from mireport.conversionresults import ConversionResultsBuilder
from mireport.data.disclosures import VSME_DEFAULTS
from mireport.report import InlineReport
from mireport.report.fact import Fact
from mireport.report.model import FactPeriod
from mireport.report.periods import DurationPeriodHolder
from mireport.xlsx_template_reader.processor import XlsxProcessor

pytestmark = pytest.mark.slow

SAMPLES = [
    "tests/data/VSME-Digital-Template-Sample-1.2.0.xlsx",
    "digital-templates/VSME-Digital-Template-Sample-1.3.0.xlsx",
]


def _with_a_prior_period(sample: str) -> tuple[InlineReport, int, int]:
    """The sample's report with every default-period fact copied into a prior period. Returns it,
    how many facts it started with, and how many it has now."""
    results = ConversionResultsBuilder(consoleOutput=False)
    report = XlsxProcessor.from_file(sample, results, VSME_DEFAULTS).createReport()
    current = report.defaultPeriod
    report.addPeriod(
        "prior",
        DurationPeriodHolder(
            current.start - relativedelta(years=1),
            current.end - relativedelta(years=1),
        ),
    )
    report.setPriorPeriodName("prior")
    prior = report.getReportPeriod("prior")

    before = len(report.facts)
    for fact in list(report.facts):
        if fact.period.reportPeriod != report.defaultReportPeriod:
            continue  # a baseline or target year is not a current-period fact to compare
        value = fact.value
        if (
            fact.concept.isNumeric
            and isinstance(value, (int, float))
            and not isinstance(value, bool)
        ):
            value = value * 0.9
        report.addFact(
            Fact(
                fact.concept,
                value,
                report,
                period=FactPeriod(prior, fact.period.part),
                unit=fact.unit,
                decimals=fact.decimals,
                scale=fact.scale,
                explicit_dimensions=fact.explicit_values,
                typed_dimensions=fact.typed_values,
                enumeration=fact.enumeration,
            )
        )
    return report, before, len(report.facts)


@pytest.fixture(
    scope="module", params=SAMPLES, ids=[s.rsplit("/", 1)[-1] for s in SAMPLES]
)
def rendered(request: pytest.FixtureRequest) -> tuple[str, int, int]:
    report, before, after = _with_a_prior_period(request.param)
    report.requireAllFactsRendered = (
        True  # raises if any fact, prior or current, appears nowhere
    )
    return report.getInlineReport().fileContent.decode("utf-8"), before, after


def test_every_fact_current_and_prior_is_in_the_document(
    rendered: tuple[str, int, int],
) -> None:
    html, before, after = rendered
    assert after > before + 100, (
        "the sample should have well over a hundred current-period facts"
    )
    tags = len(re.findall(r"<ix:non(?:Fraction|Numeric)\b", html))
    assert (
        tags >= after
    )  # one tag per fact at least (an enumeration also tags its hidden value)


def test_the_document_says_there_is_a_prior_period(
    rendered: tuple[str, int, int],
) -> None:
    html = rendered[0]
    assert "<dt>Prior period</dt>" in html
    assert "Comparative figures are for the prior period" in html


def test_lists_show_the_prior_value_beside_the_current(
    rendered: tuple[str, int, int],
) -> None:
    html = rendered[0]
    assert html.count('class="fact-comparative"') > 5


def test_tables_say_each_period_once_over_its_columns(
    rendered: tuple[str, int, int],
) -> None:
    html = rendered[0]
    # a header cell spanning more than one column, holding a period: the period axis
    spanning = re.findall(
        r'<th colspan="([2-9]\d*)"[^>]*>\s*(\d{4}-\d{2}-\d{2}) &#8211;', html
    )
    assert spanning, "no table has a period spanning its columns"
    years = {start[:4] for _, start in spanning}
    assert len(years) >= 2  # the current period's and the prior's
