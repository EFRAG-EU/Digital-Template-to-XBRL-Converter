"""End-to-end conversion against whichever real converter is installed.

Skipped when neither pdf2htmlEX nor pdftohtml is on PATH.

Everything else in the PDF suite substitutes a stub. This is the only test that
can catch a real converter's output diverging from what the stub pretends it
looks like — wrong flags, output landing somewhere unexpected, or real output
failing the XHTML schema in ways the stub's sample does not. If it fails after a
converter upgrade, the normaliser is the thing to look at first.

The fixture PDF is generated rather than committed: Pillow is already a
dependency, and a generated one cannot drift from what the test claims it is.
"""

from __future__ import annotations

import zipfile
from io import BytesIO
from pathlib import Path

import pytest
from lxml import etree
from PIL import Image, ImageDraw

from mireport.filesupport import FilelikeAndFileName
from mireport.pdf_converter import (
    PdfToolNotFoundError,
    availableConverters,
    convertPdfToHtml,
    findConverter,
)


def _noConverter() -> bool:
    return not availableConverters()


pytestmark = [
    pytest.mark.slow,
    pytest.mark.skipif(
        _noConverter(),
        reason="no PDF converter installed (need pdf2htmlEX or pdftohtml on PATH)",
    ),
]

XHTML = "http://www.w3.org/1999/xhtml"
IX = "http://www.xbrl.org/2013/inlineXBRL"
SAMPLE = Path("tests/data/VSME-Digital-Template-Sample-1.2.0.xlsx")


@pytest.fixture(scope="module")
def pdfBytes() -> bytes:
    """A two-page PDF with drawn content, via Pillow's PDF writer."""
    pages = []
    for n in (1, 2):
        page = Image.new("RGB", (612, 792), "white")
        draw = ImageDraw.Draw(page)
        draw.rectangle((50, 50, 562, 200), outline="black", width=3)
        draw.text((70, 110), f"Supplementary annex page {n}", fill="black")
        pages.append(page)
    buffer = BytesIO()
    pages[0].save(buffer, format="PDF", save_all=True, append_images=pages[1:])
    return buffer.getvalue()


@pytest.fixture(scope="module")
def linkPdfBytes() -> bytes:
    """A one-page PDF carrying a URI link annotation.

    Hand-built because Pillow cannot write an annotation, and a link is what
    makes pdf2htmlEX emit a div inside an anchor — invalid XHTML Strict, and
    unreachable from a drawn page.
    """
    stream = b"BT /F1 12 Tf 20 100 Td (Contact us) Tj ET\n"
    objects = [
        b"<</Type/Catalog/Pages 2 0 R>>",
        b"<</Type/Pages/Kids[3 0 R]/Count 1>>",
        (
            b"<</Type/Page/Parent 2 0 R/MediaBox[0 0 200 200]/Contents 4 0 R"
            b"/Resources<</Font<</F1 5 0 R>>>>/Annots[6 0 R]>>"
        ),
        b"<</Length %d>>\nstream\n%sendstream" % (len(stream), stream),
        b"<</Type/Font/Subtype/Type1/BaseFont/Helvetica>>",
        (
            b"<</Type/Annot/Subtype/Link/Rect[20 95 120 115]/Border[0 0 0]"
            b"/A<</S/URI/URI(https://example.org/)>>>>"
        ),
    ]
    pdf = bytearray(b"%PDF-1.4\n")
    offsets = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(pdf))
        pdf += b"%d 0 obj\n%s\nendobj\n" % (number, body)
    startxref = len(pdf)
    pdf += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1)
    for offset in offsets:
        pdf += b"%010d 00000 n \n" % offset
    pdf += b"trailer\n<</Size %d/Root 1 0 R>>\nstartxref\n%d\n%%%%EOF\n" % (
        len(objects) + 1,
        startxref,
    )
    return bytes(pdf)


@pytest.fixture(scope="module")
def converted(pdfBytes: bytes) -> FilelikeAndFileName:
    return convertPdfToHtml(pdfBytes, filename="annex1.pdf")


@pytest.fixture(scope="module")
def convertedLink(linkPdfBytes: bytes) -> FilelikeAndFileName:
    return convertPdfToHtml(linkPdfBytes, filename="annex2.pdf")


@pytest.fixture(scope="module")
def root(converted: FilelikeAndFileName) -> etree._Element:
    return etree.fromstring(converted.fileContent)


class TestWhichConverter:
    def test_one_was_found(self) -> None:
        assert findConverter().backend.name in {"pdf2htmlEX", "pdftohtml"}

    def test_explicitly_requesting_a_missing_one_fails_clearly(self) -> None:
        with pytest.raises(PdfToolNotFoundError):
            findConverter(backendName="definitelyNotInstalled")


class TestRealConversion:
    def test_produces_output(self, converted: FilelikeAndFileName) -> None:
        assert converted.fileContent

    def test_is_named_for_the_pdf(self, converted: FilelikeAndFileName) -> None:
        assert converted.filename == "annex1.xhtml"

    def test_is_well_formed_xml(self, converted: FilelikeAndFileName) -> None:
        etree.fromstring(converted.fileContent, etree.XMLParser())

    def test_is_xhtml(self, root: etree._Element) -> None:
        assert root.tag == f"{{{XHTML}}}html"

    def test_carries_an_ix_header(self, root: etree._Element) -> None:
        assert root.findall(f".//{{{IX}}}header")

    def test_declares_no_facts(self, root: etree._Element) -> None:
        for tag in ("nonNumeric", "nonFraction", "fraction", "tuple"):
            assert not root.findall(f".//{{{IX}}}{tag}")

    def test_has_no_script(self, converted: FilelikeAndFileName) -> None:
        """Real pdf2htmlEX embeds a page-navigation script; a report package
        member must not carry it."""
        assert b"<script" not in converted.fileContent
        assert not etree.fromstring(converted.fileContent).findall(
            f".//{{{XHTML}}}script"
        )

    def test_has_no_data_attributes_left(self, root: etree._Element) -> None:
        """Real pdf2htmlEX emits data-page-no; the schema forbids it."""
        assert not [
            name
            for element in root.iter()
            if isinstance(element.tag, str)
            for name in element.attrib
            if name.startswith("data-")
        ]

    def test_is_self_contained(self, root: etree._Element) -> None:
        """It travels in the zip alone, so nothing may be fetched from disk.
        pdftohtml in particular writes one PNG per page beside the HTML."""
        external = [
            reference
            for element in root.iter()
            if isinstance(element.tag, str)
            for attribute in ("src", "href")
            if (reference := element.get(attribute))
            and not reference.startswith(("data:", "#"))
        ]
        assert external == [], external

    def test_both_pages_survive(self, converted: FilelikeAndFileName) -> None:
        assert b"data:image" in converted.fileContent or b"page2" in (
            converted.fileContent
        )

    def test_a_link_annotation_leaves_no_block_inside_an_anchor(
        self, convertedLink: FilelikeAndFileName
    ) -> None:
        """pdf2htmlEX gives every PDF link an absolutely positioned div hit
        area; XHTML Strict's <a> takes inline content only."""
        root = etree.fromstring(convertedLink.fileContent)
        assert not root.findall(f".//{{{XHTML}}}a//{{{XHTML}}}div")


class TestRealConversionInAPackage:
    """The payoff: a really converted PDF, in a real report package, validated
    by real Arelle."""

    @pytest.fixture(scope="class")
    def package(
        self, converted: FilelikeAndFileName, convertedLink: FilelikeAndFileName
    ) -> FilelikeAndFileName:
        from mireport.conversionresults import ConversionResultsBuilder
        from mireport.data.disclosures import VSME_DEFAULTS
        from mireport.taxonomy import loadBuiltInTaxonomyJSON
        from mireport.xlsx_template_reader.processor import XlsxProcessor

        loadBuiltInTaxonomyJSON()
        report = XlsxProcessor.from_file(
            SAMPLE.open("rb"),
            ConversionResultsBuilder(),
            VSME_DEFAULTS,
            outputLocale=None,
        ).createReport()
        return report.getInlineReportPackage(
            docsetMembers=[converted, convertedLink],
            attachments=[
                FilelikeAndFileName(fileContent=b"%PDF-1.7\n", filename="annex1.pdf")
            ],
        )

    def test_member_is_in_the_docset_directory(
        self, package: FilelikeAndFileName
    ) -> None:
        with zipfile.ZipFile(BytesIO(package.fileContent)) as zf:
            names = zf.namelist()
        assert any(n.endswith("/annex1.xhtml") for n in names), names

    def test_arelle_reports_nothing_about_the_annex(
        self, package: FilelikeAndFileName
    ) -> None:
        """The whole design rests on a converted PDF being a clean document set
        member. This is the only test that proves it for real output."""
        from mireport.arelle.report_info import ArelleReportProcessor
        from mireport.conversionresults import Severity

        result = ArelleReportProcessor(workOffline=False).validateReportPackage(package)
        assert not result.has_exceptions, result.log_lines
        problems = [
            m.messageText
            for m in result.messages
            if m.severity in (Severity.ERROR, Severity.WARNING)
            and ("annex" in m.messageText.lower() or "xhtml" in m.messageText.lower())
        ]
        assert problems == [], problems

    def test_no_schema_validation_errors_at_all(
        self, package: FilelikeAndFileName
    ) -> None:
        from mireport.arelle.report_info import ArelleReportProcessor

        result = ArelleReportProcessor(workOffline=False).validateReportPackage(package)
        text = "\n".join(result.log_lines)
        for marker in ("SCHEMAV", "nonIxdsDocument", "XML file syntax error"):
            assert marker not in text, text

    def test_viewer_includes_the_annex(self, package: FilelikeAndFileName) -> None:
        from mireport.arelle.report_info import ArelleReportProcessor

        result = ArelleReportProcessor(workOffline=False).generateInlineViewer(package)
        assert result.has_viewer, result.log_lines
        assert [m.filename for m in result.viewer_document_set_members] == [
            "annex1.xhtml",
            "annex2.xhtml",
        ]
