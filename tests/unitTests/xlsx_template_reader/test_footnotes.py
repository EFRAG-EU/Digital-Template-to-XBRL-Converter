"""Footnotes were a main-only feature living inside the (now-split) processor.

These tests pin that the footnote subsystem still runs after fact creation and
attaches footnotes to facts, using the official 1.3.0 sample which ships with
working footnote_table / footnote_text / footnote_ref_concept ranges.
"""

from pathlib import Path

import pytest

from mireport.conversionresults import ConversionResultsBuilder
from mireport.data.disclosures import VSME_DEFAULTS
from mireport.xlsx_template_reader.processor import XlsxProcessor

FOOTNOTE_SAMPLE = (
    Path(__file__).parents[3]
    / "digital-templates"
    / "VSME-Digital-Template-Sample-1.3.0.xlsx"
)


def _results() -> ConversionResultsBuilder:
    return ConversionResultsBuilder(consoleOutput=False)


@pytest.fixture(scope="module")
def footnote_report():
    assert FOOTNOTE_SAMPLE.is_file(), f"Missing fixture {FOOTNOTE_SAMPLE}"
    ep = XlsxProcessor.from_file(FOOTNOTE_SAMPLE, _results(), VSME_DEFAULTS)
    return ep.createReport()


@pytest.mark.slow
def test_footnotes_are_created(footnote_report):
    assert footnote_report._footnotes, (
        "Expected footnotes to be extracted from the 1.3.0 sample's footnote ranges, "
        "but none were created — the footnote subsystem was likely lost in the merge."
    )


@pytest.mark.slow
def test_footnotes_are_attached_to_facts(footnote_report):
    facts_with_footnotes = [f for f in footnote_report.facts if f.footnotes]
    assert facts_with_footnotes, "Footnotes exist but none are attached to any fact."
