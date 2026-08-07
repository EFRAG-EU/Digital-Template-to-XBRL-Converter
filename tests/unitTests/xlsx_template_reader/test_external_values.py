"""External values + partial facts (branch features).

No shipped template uses the ``template_external_values`` named range, so we
synthesize one in-test: take the official 1.3.0 sample, pick a text-block
concept that normally becomes a fact, and add a ``template_external_values``
range naming that concept. The concept should then be reported as a *partial*
fact (its value supplied externally) rather than a normal fact, and
``completePartialFact`` should finalize it.
"""

from io import BytesIO
from pathlib import Path

import pytest
from markupsafe import Markup
from openpyxl.workbook.defined_name import DefinedName

from mireport.conversionresults import ConversionResultsBuilder
from mireport.xlsx_template_reader.processor import VSME_DEFAULTS, XlsxProcessor
from mireport.xlsx_template_reader.util import loadExcelFromPathOrFileLike

SAMPLE_1_3_0 = (
    Path(__file__).parents[3]
    / "digital-templates"
    / "VSME-Digital-Template-Sample-1.3.0.xlsx"
)


def _results() -> ConversionResultsBuilder:
    return ConversionResultsBuilder(consoleOutput=False)


@pytest.fixture(scope="module")
def external_values_case():
    """Return (workbook_bytes, concept) for a synthesized external-values template."""
    assert SAMPLE_1_3_0.is_file(), f"Missing fixture {SAMPLE_1_3_0}"

    # 1. Normal report: find a text-block concept that actually became a fact.
    report = XlsxProcessor.from_file(
        SAMPLE_1_3_0, _results(), VSME_DEFAULTS
    ).createReport()
    chosen = next(
        (f.concept for f in report.facts if f.concept.isTextblock),
        None,
    )
    assert chosen is not None, "1.3.0 sample has no text-block fact to use"

    # 2. Synthesize a workbook with a template_external_values range naming it.
    wb = loadExcelFromPathOrFileLike(SAMPLE_1_3_0)
    ws = wb.worksheets[0]
    scratch = ws.cell(row=1, column=250)  # far-out, otherwise-unused cell
    scratch.value = chosen.qname.localName
    wb.defined_names.add(
        DefinedName(
            "template_external_values",
            attr_text=f"'{ws.title}'!{scratch.coordinate}",
        )
    )
    buf = BytesIO()
    wb.save(buf)
    wb.close()
    return buf.getvalue(), chosen


@pytest.mark.slow
def test_external_value_becomes_partial_fact(external_values_case):
    blob, concept = external_values_case
    report = XlsxProcessor.from_bytes(blob, _results(), VSME_DEFAULTS).createReport()

    assert report.hasPartialFacts, (
        "Expected a pending partial fact for the external value."
    )
    assert concept in report.partialFactsByConcept
    assert concept not in {f.concept for f in report.facts}, (
        "External-value concept should not also be reported as a normal fact."
    )


@pytest.mark.slow
def test_complete_partial_fact_finalizes_it(external_values_case):
    blob, concept = external_values_case
    report = XlsxProcessor.from_bytes(blob, _results(), VSME_DEFAULTS).createReport()

    report.completePartialFact(concept, Markup("<p>Externally supplied.</p>"))

    assert not report.hasPartialFacts
    assert concept in {f.concept for f in report.facts}
