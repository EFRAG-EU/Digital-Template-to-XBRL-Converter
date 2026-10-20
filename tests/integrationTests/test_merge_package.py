"""Arelle must accept a report whose own document carries a merged PDF annex.

The document-set counterpart to this is test_docset_package.py; this is the
same load-bearing question for the alternative "merge" mode: splicing a
converted, normalised PDF into the back of the report's own document instead of
alongside it. No real converter needed — the stub PDF2HTMLEX_OUTPUT shape run
through the normaliser is exactly what a real one calling
mireport.pdf_converter._merge.buildMergedAnnex is given.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from mireport.arelle.report_info import ArelleReportProcessor
from mireport.arelle.support import ArelleProcessingResult
from mireport.conversionresults import ConversionResultsBuilder, Severity
from mireport.data.disclosures import VSME_DEFAULTS
from mireport.pdf_converter import MergedAnnex, buildMergedAnnex, mergeAnnexesIntoReport
from mireport.pdf_converter._xhtml import normaliseToXhtml
from mireport.report.reportpackage import buildReportPackage
from mireport.taxonomy import loadBuiltInTaxonomyJSON
from mireport.xlsx_template_reader.processor import XlsxProcessor

if TYPE_CHECKING:
    from mireport.conversionresults import Message
    from mireport.filesupport import FilelikeAndFileName
    from mireport.report.inlinereport import InlineReport

SAMPLE = Path("tests/data/VSME-Digital-Template-Sample-1.2.0.xlsx")
STUB_CONVERTER = Path("tests/data/stub-pdf-converter.py")

# Module-scoped fixtures here are slow Arelle runs. With ``--dist loadgroup``
# the whole module stays on one worker, so each runs once rather than once per
# worker that happens to pick up one of its tests.
pytestmark = [pytest.mark.slow, pytest.mark.xdist_group("merge-package")]


def _loadStubModule():
    spec = importlib.util.spec_from_file_location("stub_pdf_converter", STUB_CONVERTER)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def report() -> InlineReport:
    loadBuiltInTaxonomyJSON()
    results = ConversionResultsBuilder()
    processor = XlsxProcessor.from_file(
        SAMPLE.open("rb"), results, VSME_DEFAULTS, outputLocale=None
    )
    return processor.createReport()


@pytest.fixture(scope="module")
def annex() -> MergedAnnex:
    module = _loadStubModule()
    normalised = normaliseToXhtml(
        module.PDF2HTMLEX_OUTPUT.encode("utf-8"), injectIxHeader=False
    )
    return buildMergedAnnex(normalised, index=1, label="annex1.pdf")


@pytest.fixture(scope="module")
def arelle() -> ArelleReportProcessor:
    return ArelleReportProcessor(workOffline=False)


@pytest.fixture(scope="module")
def plainPackage(report: InlineReport) -> FilelikeAndFileName:
    return report.getInlineReportPackage()


@pytest.fixture(scope="module")
def mergedPackage(report: InlineReport, annex: MergedAnnex) -> FilelikeAndFileName:
    reportFile = mergeAnnexesIntoReport(report.getInlineReport(), [annex])
    return buildReportPackage(reportFile, topLevel=report.packageTopLevelName)


# Each Arelle session costs several seconds, so every distinct operation on a
# package runs once per module and the tests below assert on the result.
# One validating session per package also counts the facts Arelle loaded
# (factCount), so validation and fact tests share a single run.


@pytest.fixture(scope="module")
def plainRun(
    arelle: ArelleReportProcessor, plainPackage: FilelikeAndFileName
) -> ArelleProcessingResult:
    return arelle.validateReportPackage(plainPackage)


@pytest.fixture(scope="module")
def mergedRun(
    arelle: ArelleReportProcessor, mergedPackage: FilelikeAndFileName
) -> ArelleProcessingResult:
    return arelle.validateReportPackage(mergedPackage)


@pytest.fixture(scope="module")
def mergedViewer(
    arelle: ArelleReportProcessor, mergedPackage: FilelikeAndFileName
) -> ArelleProcessingResult:
    return arelle.generateInlineViewer(mergedPackage)


def errorsAndWarnings(messages: list[Message]) -> set[str]:
    return {
        m.messageText
        for m in messages
        if m.severity in (Severity.ERROR, Severity.WARNING)
    }


def _reportHtml(package: FilelikeAndFileName) -> bytes:
    import zipfile
    from io import BytesIO

    with zipfile.ZipFile(BytesIO(package.fileContent)) as zf:
        [reportName] = [n for n in zf.namelist() if n.endswith(".html")]
        return zf.read(reportName)


# ── validation ────────────────────────────────────────────────────────────────


class TestValidation:
    def test_plain_package_validates(self, plainRun: ArelleProcessingResult) -> None:
        """The baseline. If this fails the comparison below means nothing."""
        assert not plainRun.has_exceptions, plainRun.log_lines

    def test_merged_package_raises_no_exception(
        self, mergedRun: ArelleProcessingResult
    ) -> None:
        assert not mergedRun.has_exceptions, mergedRun.log_lines

    def test_merge_introduces_no_new_messages(
        self,
        plainRun: ArelleProcessingResult,
        mergedRun: ArelleProcessingResult,
    ) -> None:
        before = errorsAndWarnings(plainRun.messages)
        after = errorsAndWarnings(mergedRun.messages)
        assert after - before == set()

    def test_no_document_set_complaint(self, mergedRun: ArelleProcessingResult) -> None:
        """A merged report is still a single-document entry point — it must
        never need the document-set plug-in the way docset mode does."""
        text = "\n".join(mergedRun.log_lines)
        assert "InlineXbrlDocumentSet" not in text


# ── the annex must not perturb the report's own facts ─────────────────────────


class TestFactsUnchanged:
    def test_fact_count_is_identical(
        self, plainRun: ArelleProcessingResult, mergedRun: ArelleProcessingResult
    ) -> None:
        assert mergedRun.factCount == plainRun.factCount
        assert plainRun.factCount, plainRun.log_lines


# ── layout and content ────────────────────────────────────────────────────────


class TestMergedContent:
    def test_stays_a_single_file_report(
        self, mergedPackage: FilelikeAndFileName
    ) -> None:
        import zipfile
        from io import BytesIO

        with zipfile.ZipFile(BytesIO(mergedPackage.fileContent)) as zf:
            names = zf.namelist()
        assert sum("/reports/" in n for n in names) == 1
        assert any(n.count("/") == 2 and "/reports/" in n for n in names), names

    def test_annex_marker_text_is_present(
        self, mergedPackage: FilelikeAndFileName
    ) -> None:
        assert b"Converted annex" in _reportHtml(mergedPackage)

    def test_no_placeholder_left_unspliced(
        self, mergedPackage: FilelikeAndFileName
    ) -> None:
        assert b"pdf-annex-placeholder" not in _reportHtml(mergedPackage)

    def test_annexes_entry_is_in_the_table_of_contents(
        self, mergedPackage: FilelikeAndFileName
    ) -> None:
        assert b'href="#pdf-annex-1"' in _reportHtml(mergedPackage)

    def test_only_one_ix_header(self, mergedPackage: FilelikeAndFileName) -> None:
        from lxml import etree

        IX = "http://www.xbrl.org/2013/inlineXBRL"
        root = etree.fromstring(_reportHtml(mergedPackage))
        assert len(root.findall(f".//{{{IX}}}header")) == 1


# ── viewer output ───────────────────────────────────────────────────────────


class TestGeneratedOutputs:
    def test_viewer_is_generated(self, mergedViewer: ArelleProcessingResult) -> None:
        assert mergedViewer.has_viewer, mergedViewer.log_lines

    def test_viewer_has_no_extra_document_set_members(
        self, mergedViewer: ArelleProcessingResult
    ) -> None:
        """A merged annex is not a document set member: it travels inside the
        one file the viewer already serves."""
        assert mergedViewer.viewer_document_set_members == []
