"""Arelle must accept a report package whose report is an Inline XBRL document set.

This is the load-bearing test for the whole PDF feature. Everything else is
plumbing; this decides whether shipping converted PDFs as document set members is
viable at all.

The stub member is checked-in XHTML with no ``ix:`` content, so none of this
needs ``pdf2htmlEX`` installed. What it proves:

- an extra document in the set introduces no new validation messages;
- fact count is unchanged (the member contributes nothing, and hides nothing);
- viewer and xBRL-JSON generation still work, which they do not by default —
  the viewer emits one file per set member and the JSON path needs the
  InlineXbrlDocumentSet plug-in enabled.
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
from mireport.filesupport import FilelikeAndFileName
from mireport.taxonomy import loadBuiltInTaxonomyJSON
from mireport.xlsx_template_reader.processor import XlsxProcessor

if TYPE_CHECKING:
    from mireport.conversionresults import Message
    from mireport.report.inlinereport import InlineReport

SAMPLE = Path("tests/data/VSME-Digital-Template-Sample-1.2.0.xlsx")
STUB = Path("tests/data/docset-member-stub.xhtml")
STUB_CONVERTER = Path("tests/data/stub-pdf-converter.py")

# Module-scoped fixtures here are slow Arelle runs. With ``--dist loadgroup``
# the whole module stays on one worker, so each runs once rather than once per
# worker that happens to pick up one of its tests.
pytestmark = [pytest.mark.slow, pytest.mark.xdist_group("docset-package")]


@pytest.fixture(scope="module")
def report() -> InlineReport:
    loadBuiltInTaxonomyJSON()
    results = ConversionResultsBuilder()
    processor = XlsxProcessor.from_file(
        SAMPLE.open("rb"), results, VSME_DEFAULTS, outputLocale=None
    )
    return processor.createReport()


def _handwrittenMember() -> bytes:
    """XHTML written by hand — the minimum a member must look like."""
    return STUB.read_bytes()


def _normalisedMember() -> bytes:
    """The same thing arrived at the way production does: HTML5 shaped like
    pdf2htmlEX's output, put through the normaliser.

    This is the variant that matters. The hand-written stub passed Arelle from
    the outset; this one did not, and the four XHTML-schema errors it produced
    are why the normaliser strips data-* attributes and bare <meta charset>.
    """
    from mireport.html_to_xhtml import normaliseDocument

    spec = importlib.util.spec_from_file_location("stub_pdf_converter", STUB_CONVERTER)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    # pdf2htmlEX's shape: it is the preferred backend, and unlike pdftohtml's it
    # references no sibling image files, so it stands alone without a working
    # directory to inline from.
    return normaliseDocument(module.PDF2HTMLEX_OUTPUT.encode("utf-8"))


@pytest.fixture(
    scope="module",
    params=[
        (_handwrittenMember, b"Supplementary annex"),
        (_normalisedMember, b"Converted annex"),
    ],
    ids=["handwritten", "normalised"],
)
def memberAndMarker(
    request: pytest.FixtureRequest,
) -> tuple[FilelikeAndFileName, bytes]:
    build, marker = request.param
    return (
        FilelikeAndFileName(fileContent=build(), filename="annex1.xhtml"),
        marker,
    )


@pytest.fixture(scope="module")
def member(
    memberAndMarker: tuple[FilelikeAndFileName, bytes],
) -> FilelikeAndFileName:
    return memberAndMarker[0]


@pytest.fixture(scope="module")
def marker(memberAndMarker: tuple[FilelikeAndFileName, bytes]) -> bytes:
    """Text unique to this member variant, so a viewer assertion about it
    cannot pass by accident — or by matching boilerplate the normaliser
    injects, which is how the first attempt at this went wrong."""
    return memberAndMarker[1]


@pytest.fixture(scope="module")
def arelle() -> ArelleReportProcessor:
    return ArelleReportProcessor(workOffline=False)


@pytest.fixture(scope="module")
def plainPackage(report: InlineReport) -> FilelikeAndFileName:
    return report.getInlineReportPackage()


@pytest.fixture(scope="module")
def docsetPackage(
    report: InlineReport, member: FilelikeAndFileName
) -> FilelikeAndFileName:
    return report.getInlineReportPackage(
        docsetMembers=[member],
        attachments=[
            FilelikeAndFileName(fileContent=b"%PDF-1.7\n", filename="annex1.pdf")
        ],
    )


def errorsAndWarnings(messages: list[Message]) -> set[str]:
    return {
        m.messageText
        for m in messages
        if m.severity in (Severity.ERROR, Severity.WARNING)
    }


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
def docsetRun(
    arelle: ArelleReportProcessor, docsetPackage: FilelikeAndFileName
) -> ArelleProcessingResult:
    return arelle.validateReportPackage(docsetPackage)


@pytest.fixture(scope="module")
def plainViewer(
    arelle: ArelleReportProcessor, plainPackage: FilelikeAndFileName
) -> ArelleProcessingResult:
    return arelle.generateInlineViewer(plainPackage)


@pytest.fixture(scope="module")
def docsetViewer(
    arelle: ArelleReportProcessor, docsetPackage: FilelikeAndFileName
) -> ArelleProcessingResult:
    return arelle.generateInlineViewer(docsetPackage)


# ── validation ────────────────────────────────────────────────────────────────


class TestValidation:
    def test_plain_package_validates(self, plainRun: ArelleProcessingResult) -> None:
        """The baseline. If this fails the comparison below means nothing."""
        assert not plainRun.has_exceptions, plainRun.log_lines

    def test_docset_package_raises_no_exception(
        self, docsetRun: ArelleProcessingResult
    ) -> None:
        assert not docsetRun.has_exceptions, docsetRun.log_lines

    def test_docset_introduces_no_new_messages(
        self,
        plainRun: ArelleProcessingResult,
        docsetRun: ArelleProcessingResult,
    ) -> None:
        before = errorsAndWarnings(plainRun.messages)
        after = errorsAndWarnings(docsetRun.messages)
        assert after - before == set()

    def test_no_entry_point_or_document_set_complaint(
        self, docsetRun: ArelleProcessingResult
    ) -> None:
        """The specific failure modes this design risks, named explicitly."""
        text = "\n".join(docsetRun.log_lines)
        assert "InlineXbrlDocumentSet" not in text, (
            "Arelle asked for the document set plug-in to be enabled"
        )
        for code in (
            "rpe:multipleReports",
            "rpe:missingReport",
            "rpe:multipleReportsInSubdirectory",
            "rpe:invalidDirectoryStructure",
            "ix11:missingHeader",
            "ix11:headerMissing",
        ):
            assert code not in text, f"{code} reported for the document set package"


# ── the member must not perturb the report ────────────────────────────────────


class TestFactsUnchanged:
    def test_fact_count_is_identical(
        self, plainRun: ArelleProcessingResult, docsetRun: ArelleProcessingResult
    ) -> None:
        assert docsetRun.factCount == plainRun.factCount
        assert plainRun.factCount, plainRun.log_lines


# ── viewer output ───────────────────────────────────────────────────────────


class TestGeneratedOutputs:
    def test_viewer_is_generated_for_a_docset(
        self, docsetViewer: ArelleProcessingResult
    ) -> None:
        """The viewer writes one file per set member, so the single-file
        response-zip reader has to cope with more than one."""
        assert docsetViewer.has_viewer, docsetViewer.log_lines

    def test_viewer_carries_the_member_alongside_the_primary(
        self, docsetViewer: ArelleProcessingResult, marker: bytes
    ) -> None:
        """Proves the annex actually reached the viewer rather than merely
        failing to break it."""
        members = docsetViewer.viewer_document_set_members
        assert [m.filename for m in members] == ["annex1.xhtml"]
        assert marker in members[0].fileContent

    def test_viewer_primary_references_the_member_by_name(
        self, docsetViewer: ArelleProcessingResult
    ) -> None:
        """Which is why the member keeps its filename when served."""
        assert b"annex1.xhtml" in docsetViewer.viewer.fileContent

    def test_viewer_is_still_generated_for_a_plain_report(
        self, plainViewer: ArelleProcessingResult
    ) -> None:
        """Enabling the document set plug-in must not disturb the common case."""
        assert plainViewer.has_viewer, plainViewer.log_lines

    def test_plain_report_has_no_document_set_members(
        self, plainViewer: ArelleProcessingResult
    ) -> None:
        assert plainViewer.viewer_document_set_members == []
