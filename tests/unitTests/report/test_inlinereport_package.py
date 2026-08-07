"""InlineReport.getInlineReportPackage() is a pass-through to buildReportPackage().

Attachments are deliberately an argument rather than state on InlineReport: there
is no "register these first, then build" ordering to get wrong, because the only
thing that knows about attachments is the caller that already has them in hand.
"""

from __future__ import annotations

import zipfile
from datetime import date
from io import BytesIO

import pytest

from mireport.filesupport import FilelikeAndFileName
from mireport.report.inlinereport import InlineReport
from mireport.taxonomy import (
    Taxonomy,
    getTaxonomy,
    listTaxonomies,
    loadBuiltInTaxonomyJSON,
)


@pytest.fixture(scope="module")
def taxonomy() -> Taxonomy:
    if not listTaxonomies():
        loadBuiltInTaxonomyJSON()
    entry_point = next(ep for ep in listTaxonomies() if "vsme" in ep.lower())
    return getTaxonomy(entry_point)


REPORT_HTML = b"<html>tagged</html>"


@pytest.fixture
def report(taxonomy: Taxonomy, monkeypatch: pytest.MonkeyPatch) -> InlineReport:
    """A minimal InlineReport whose rendering is stubbed out.

    Only the packaging path is under test here; constructing real facts and
    running them through aoix is the integration tests' job.
    """
    r = InlineReport(taxonomy)
    r.setEntityName("Acme Ltd")
    r.addDurationPeriod("y", date(2024, 1, 1), date(2024, 12, 31))
    r.setDefaultPeriodName("y")
    monkeypatch.setattr(
        InlineReport,
        "_constructInlineReport",
        lambda self: REPORT_HTML.decode(),
    )
    return r


class TestEndToEnd:
    """No stubbing of buildReportPackage: the real zip, from a real InlineReport."""

    def test_plain_package(self, report: InlineReport) -> None:
        with zipfile.ZipFile(
            BytesIO(report.getInlineReportPackage().fileContent)
        ) as zf:
            assert zf.namelist() == [
                "Acme_Ltd_2024/META-INF/reportPackage.json",
                "Acme_Ltd_2024/reports/Acme_Ltd_2024_XBRL_Report.html",
            ]

    def test_docset_package(self, report: InlineReport) -> None:
        package = report.getInlineReportPackage(
            docsetMembers=[
                FilelikeAndFileName(fileContent=b"<html/>", filename="annex1.html")
            ],
            attachments=[
                FilelikeAndFileName(fileContent=b"%PDF-1.7", filename="annex1.pdf")
            ],
        )
        with zipfile.ZipFile(BytesIO(package.fileContent)) as zf:
            assert zf.namelist() == [
                "Acme_Ltd_2024/META-INF/reportPackage.json",
                "Acme_Ltd_2024/reports/Acme_Ltd_2024_XBRL_Report/Acme_Ltd_2024_XBRL_Report.html",
                "Acme_Ltd_2024/reports/Acme_Ltd_2024_XBRL_Report/annex1.html",
                "Acme_Ltd_2024/attachments/annex1.pdf",
            ]

    def test_entity_name_is_made_zip_safe(self, report: InlineReport) -> None:
        report.setEntityName("Acme / Ltd\\Co")
        package = report.getInlineReportPackage()
        with zipfile.ZipFile(BytesIO(package.fileContent)) as zf:
            assert all("\\" not in i.orig_filename for i in zf.infolist())
            assert {n.split("/")[0] for n in zf.namelist()} == {"Acme___Ltd_Co_2024"}
