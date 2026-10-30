"""Last year's report as Inline XBRL or a report package, read through Arelle by PriorReport for
its comparatives: the 1.2.0 sample is for the year before the 1.3.0 one's."""

from __future__ import annotations

import zipfile
from io import BytesIO
from pathlib import Path

import pytest
from tests.offline_arelle import offlineArelle

from mireport.arelle.report_info import ArelleReportProcessor
from mireport.comparatives import PriorReport
from mireport.conversionresults import ConversionResultsBuilder, Severity
from mireport.data.disclosures import VSME_DEFAULTS
from mireport.exceptions import EarlyAbortException
from mireport.filesupport import FilelikeAndFileName
from mireport.report.report import Report
from mireport.taxonomy import listTaxonomies, loadBuiltInTaxonomyJSON
from mireport.xbrljson_writer import toXbrlJson
from mireport.xlsx_template_reader.processor import XlsxProcessor

SAMPLES = Path("digital-templates")

pytestmark = [pytest.mark.slow, pytest.mark.xdist_group("prior-inline-xbrl")]


def _sample(version: str) -> Report:
    if not listTaxonomies():
        loadBuiltInTaxonomyJSON()
    return XlsxProcessor.from_file(
        SAMPLES / f"VSME-Digital-Template-Sample-{version}.xlsx",
        ConversionResultsBuilder(consoleOutput=False),
        VSME_DEFAULTS,
    ).createReport()


@pytest.fixture(scope="module")
def arelle() -> ArelleReportProcessor:
    return offlineArelle()


def _package(document: bytes, name: str, kind: str) -> FilelikeAndFileName:
    """A report package of the 2023 spec's ``kind`` (xbri or xbr) holding one document."""
    content = BytesIO()
    with zipfile.ZipFile(content, "w") as zf:
        zf.writestr(
            "last-year/META-INF/reportPackage.json",
            '{"documentInfo": {"documentType": '
            f'"https://xbrl.org/report-package/2023/{kind}"}}}}',
        )
        zf.writestr(f"last-year/reports/{name}", document)
    return FilelikeAndFileName(content.getvalue(), f"last-year.{kind}")


def _lastYear(form: str) -> FilelikeAndFileName:
    report = _sample("1.2.0")
    if form == "xbr":
        return _package(toXbrlJson(report).toJson().encode(), "report.json", "xbr")
    inline = report.getInlineReport()
    if form == "xbri":
        return _package(inline.fileContent, inline.filename, "xbri")
    return inline


@pytest.mark.parametrize("form", ["html", "xbri", "xbr"])
def test_last_years_report_through_arelle_gives_the_comparatives(
    arelle: ArelleReportProcessor, form: str
) -> None:
    lastYear = _lastYear(form)
    assert lastYear.filename.endswith(f".{form}")
    results = ConversionResultsBuilder(consoleOutput=False)
    prior = PriorReport.fromFile(lastYear, results, arelle=arelle)

    current = _sample("1.3.0")
    merged = prior.mergeInto(current, results)
    assert merged.taken > 50
    assert str(current.priorPeriod) == "2025-01-01T00:00:00/2026-01-01T00:00:00"
    assert not [m for m in results.messages if m.severity is Severity.ERROR]
    current.requireAllFactsRendered = True
    assert current.getInlineReport().fileContent


def test_a_bad_inline_xbrl_report_stops_the_conversion(
    arelle: ArelleReportProcessor,
) -> None:
    results = ConversionResultsBuilder(consoleOutput=False)
    with pytest.raises(
        EarlyAbortException,
        match=r"^Prior-year report: Arelle could not read last-year\.xhtml as an XBRL report\.$",
    ) as raised:
        PriorReport.fromInlineXbrl(
            FilelikeAndFileName(b"<html>not XBRL</html>", "last-year.xhtml"),
            results,
            arelle=arelle,
        )
    *read, error = results.messages
    assert read
    assert all(
        str(m.messageText).startswith("Prior-year report: ")
        and m.severity is not Severity.ERROR
        for m in read
    )
    assert (error.severity, error.messageText) == (Severity.ERROR, str(raised.value))
