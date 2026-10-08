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

pytestmark = pytest.mark.slow


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
    from mireport.pdf_converter._xhtml import normaliseToXhtml

    spec = importlib.util.spec_from_file_location("stub_pdf_converter", STUB_CONVERTER)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    # pdf2htmlEX's shape: it is the preferred backend, and unlike pdftohtml's it
    # references no sibling image files, so it stands alone without a working
    # directory to inline from.
    return normaliseToXhtml(module.PDF2HTMLEX_OUTPUT.encode("utf-8"))


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


# ── validation ────────────────────────────────────────────────────────────────


class TestValidation:
    def test_plain_package_validates(
        self, arelle: ArelleReportProcessor, plainPackage: FilelikeAndFileName
    ) -> None:
        """The baseline. If this fails the comparison below means nothing."""
        result = arelle.validateReportPackage(plainPackage)
        assert not result.has_exceptions, result.log_lines

    def test_docset_package_raises_no_exception(
        self, arelle: ArelleReportProcessor, docsetPackage: FilelikeAndFileName
    ) -> None:
        result = arelle.validateReportPackage(docsetPackage)
        assert not result.has_exceptions, result.log_lines

    def test_docset_introduces_no_new_messages(
        self,
        arelle: ArelleReportProcessor,
        plainPackage: FilelikeAndFileName,
        docsetPackage: FilelikeAndFileName,
    ) -> None:
        before = errorsAndWarnings(arelle.validateReportPackage(plainPackage).messages)
        after = errorsAndWarnings(arelle.validateReportPackage(docsetPackage).messages)
        assert after - before == set()

    def test_no_entry_point_or_document_set_complaint(
        self, arelle: ArelleReportProcessor, docsetPackage: FilelikeAndFileName
    ) -> None:
        """The specific failure modes this design risks, named explicitly."""
        text = "\n".join(arelle.validateReportPackage(docsetPackage).log_lines)
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
        self,
        arelle: ArelleReportProcessor,
        plainPackage: FilelikeAndFileName,
        docsetPackage: FilelikeAndFileName,
    ) -> None:
        import json

        def factCount(package: FilelikeAndFileName) -> int:
            result = arelle.generateXBRLJson(package)
            assert result.has_json, result.log_lines
            return len(json.loads(result.xbrl_json.fileContent)["facts"])

        assert factCount(docsetPackage) == factCount(plainPackage)


# ── viewer and JSON outputs ───────────────────────────────────────────────────


class TestGeneratedOutputs:
    def test_json_is_generated_for_a_docset(
        self, arelle: ArelleReportProcessor, docsetPackage: FilelikeAndFileName
    ) -> None:
        result = arelle.generateXBRLJson(docsetPackage)
        assert result.has_json, result.log_lines

    def test_viewer_is_generated_for_a_docset(
        self, arelle: ArelleReportProcessor, docsetPackage: FilelikeAndFileName
    ) -> None:
        """The viewer writes one file per set member, so the single-file
        response-zip reader has to cope with more than one."""
        result = arelle.generateInlineViewer(docsetPackage)
        assert result.has_viewer, result.log_lines

    def test_viewer_carries_the_member_alongside_the_primary(
        self,
        arelle: ArelleReportProcessor,
        docsetPackage: FilelikeAndFileName,
        marker: bytes,
    ) -> None:
        """Proves the annex actually reached the viewer rather than merely
        failing to break it."""
        members = arelle.generateInlineViewer(docsetPackage).viewer_document_set_members
        assert [m.filename for m in members] == ["annex1.xhtml"]
        assert marker in members[0].fileContent

    def test_viewer_primary_references_the_member_by_name(
        self, arelle: ArelleReportProcessor, docsetPackage: FilelikeAndFileName
    ) -> None:
        """Which is why the member keeps its filename when served."""
        result = arelle.generateInlineViewer(docsetPackage)
        assert b"annex1.xhtml" in result.viewer.fileContent

    def test_viewer_is_still_generated_for_a_plain_report(
        self, arelle: ArelleReportProcessor, plainPackage: FilelikeAndFileName
    ) -> None:
        """Enabling the document set plug-in must not disturb the common case."""
        result = arelle.generateInlineViewer(plainPackage)
        assert result.has_viewer, result.log_lines

    def test_plain_report_has_no_document_set_members(
        self, arelle: ArelleReportProcessor, plainPackage: FilelikeAndFileName
    ) -> None:
        result = arelle.generateInlineViewer(plainPackage)
        assert result.viewer_document_set_members == []
