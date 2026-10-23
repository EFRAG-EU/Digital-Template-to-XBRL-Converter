"""Last year's report, uploaded beside this year's workbook, gives the comparatives.

This year's workbook is the 1.3.0 sample, and last year's report is either its
own xBRL-JSON a year earlier or the 1.2.0 sample, which is for the year before.
"""

import io
import json
import re
from html import unescape
from pathlib import Path

import pytest
from openpyxl.workbook.defined_name import DefinedName

from digital_converter_webapp.conversion import ConversionStore
from mireport.conversionresults import ConversionResultsBuilder
from mireport.data.disclosures import VSME_DEFAULTS
from mireport.xbrljson_writer import toXbrlJson
from mireport.xlsx_template_reader.processor import XlsxProcessor
from mireport.xlsx_template_reader.util import loadExcelFromPathOrFileLike

SAMPLES = Path(__file__).parents[2] / "digital-templates"
SAMPLE_1_2_0 = SAMPLES / "VSME-Digital-Template-Sample-1.2.0.xlsx"
SAMPLE_1_3_0 = SAMPLES / "VSME-Digital-Template-Sample-1.3.0.xlsx"
PRIOR_PERIOD = "2025-01-01T00:00:00/2026-01-01T00:00:00"
COMPARATIVES = "Comparative figures for 2025-01-01 – 2025-12-31"
# A text block the 1.3.0 sample reports, made one whose value is a Word document.
TEXT_BLOCK_CONCEPT = (
    "DescriptionOfClimateRelatedHazardsAndClimateRelatedTransitionEvents"
)


def _withNamedCell(workbook: bytes, name: str, value: str | None) -> bytes:
    """The workbook with ``name`` naming an otherwise unused cell holding ``value``."""
    wb = loadExcelFromPathOrFileLike(io.BytesIO(workbook))
    ws = wb.worksheets[0]
    scratch = ws.cell(row=1, column=250)
    scratch.value = value
    wb.defined_names.add(
        DefinedName(name, attr_text=f"'{ws.title}'!{scratch.coordinate}")
    )
    out = io.BytesIO()
    wb.save(out)
    return out.getvalue()


def _xbrlJson(workbook: bytes, *, yearsEarlier: int = 0) -> bytes:
    """The workbook's report as xBRL-JSON, every period ``yearsEarlier``."""
    report = XlsxProcessor.from_file(
        io.BytesIO(workbook),
        ConversionResultsBuilder(consoleOutput=False),
        VSME_DEFAULTS,
    ).createReport()
    document = toXbrlJson(report).toDict()
    for fact in document["facts"].values():
        if period := fact["dimensions"].get("period"):
            fact["dimensions"]["period"] = re.sub(
                r"\d{4}", lambda year: str(int(year[0]) - yearsEarlier), period
            )
    return json.dumps(document).encode("utf-8")


@pytest.fixture(scope="module")
def thisYear() -> bytes:
    return SAMPLE_1_3_0.read_bytes()


@pytest.fixture(scope="module")
def lastYearJson(app, thisYear: bytes) -> bytes:
    return _xbrlJson(thisYear, yearsEarlier=1)


def _upload(client, thisYear: bytes, prior: bytes, priorName: str):
    return client.post(
        "/upload",
        data={
            "file": (io.BytesIO(thisYear), "this-year.xlsx"),
            "prior": (io.BytesIO(prior), priorName),
        },
        content_type="multipart/form-data",
    )


def _convert(client, upload) -> str:
    """The results page for the upload, unescaped."""
    assert upload.status_code == 303
    page = client.get(upload.headers["Location"])
    assert page.status_code == 200
    return unescape(page.get_data(as_text=True))


class TestUpload:
    def test_last_years_report_is_kept_with_the_conversion(
        self, client, upload, conversion_of
    ):
        resp = upload(client, prior=(io.BytesIO(b"{}"), "last-year.JSON"))
        assert resp.status_code == 303
        prior = conversion_of(client).priorReport
        assert prior is not None
        assert (prior.filename, prior.fileContent) == ("last-year.JSON", b"{}")

    def test_an_empty_prior_input_is_ignored(self, client, upload, conversion_of):
        resp = upload(client, prior=(io.BytesIO(b""), ""))
        assert resp.status_code == 303
        assert conversion_of(client).priorReport is None

    def test_a_bad_extension_is_refused(self, client, upload):
        resp = upload(client, prior=(io.BytesIO(b"%PDF"), "last-year.pdf"))
        assert resp.status_code == 400
        body = json.loads(resp.data)
        assert body["file"] == "last-year.pdf"
        assert ".json, .xlsx, .xhtml, .html, .xbri, .xbr, .zip" in body["error"]
        with client.session_transaction() as sess:
            assert not ConversionStore(sess).active()


@pytest.mark.slow
class TestComparatives:
    """Each runs a full conversion, Arelle validation included."""

    def test_last_years_xbrl_json_gives_the_comparatives(
        self, client, conversion_of, thisYear, lastYearJson
    ):
        body = _convert(client, _upload(client, thisYear, lastYearJson, "prior.json"))
        assert f"{COMPARATIVES}: " in body
        assert "taken from prior.json." in body
        assert "Last year's report:</span> prior.json" in body
        conv = conversion_of(client)
        assert conv.successful
        assert conv.json is not None
        assert PRIOR_PERIOD in conv.json.fileContent.decode("utf-8")

    def test_last_years_workbook_on_an_older_template_is_used_as_it_is(
        self, client, conversion_of, thisYear
    ):
        """No migration on this app: its figures are matched to ours by name."""
        prior = SAMPLE_1_2_0.read_bytes()
        body = _convert(client, _upload(client, thisYear, prior, SAMPLE_1_2_0.name))
        assert f"taken from {SAMPLE_1_2_0.name}." in body
        assert conversion_of(client).successful


@pytest.mark.slow
class TestFailures:
    def test_this_years_report_given_as_last_years_too_stops_the_conversion(
        self, client, conversion_of, thisYear
    ):
        """This year's own report, as last year's, is for the same period, and is named so."""
        body = _convert(
            client, _upload(client, thisYear, _xbrlJson(thisYear), "prior.json")
        )
        assert "is it this year's report given twice?" in body
        conv = conversion_of(client)
        assert not conv.successful
        assert not conv.has_zip

    def test_a_migrated_workbook_not_yet_saved_in_excel_is_refused(
        self, client, conversion_of, thisYear
    ):
        """Its migration status is a formula, with no value until Excel saves it."""
        unsaved = _withNamedCell(thisYear, "template_migration_status", None)
        body = _convert(client, _upload(client, thisYear, unsaved, "migrated.xlsx"))
        assert (
            "migrated.xlsx has been migrated but not yet saved in "
            "Excel. Open it in Excel, save it, and give it again."
        ) in body
        assert not conversion_of(client).has_zip

    def test_the_word_documents_are_still_asked_for(
        self, client, conversion_of, thisYear, lastYearJson
    ):
        """This year's workbook pauses for its Word text blocks, last year's report
        or not, and keeps it for when the conversion goes on."""
        withTextBlock = _withNamedCell(
            thisYear, "template_external_values", TEXT_BLOCK_CONCEPT
        )
        upload = _upload(client, withTextBlock, lastYearJson, "prior.json")
        resp = client.get(upload.headers["Location"])
        assert resp.status_code == 303
        assert resp.location.endswith("/partial-facts")
        conv = conversion_of(client)
        assert conv.has_partial_fact_concepts
        assert conv.priorReport is not None
        assert not conv.has_results
