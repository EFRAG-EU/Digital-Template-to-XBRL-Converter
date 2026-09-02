"""Supplementary PDF upload, conversion and packaging in the web app.

None of this needs pdf2htmlEX. The conversion call is monkeypatched, which is
also the point of several of the tests: the whole feature is designed to degrade
to "the PDF still ships, unconverted" on a deployment with no converter
installed, and that path has to be exercised on machines that are exactly that.
"""

import io
import json
import zipfile
from pathlib import Path

import pytest

from digital_converter_webapp import MAX_PDF_COUNT
from mireport.filesupport import FilelikeAndFileName
from mireport.pdf_converter import PdfConversionError, PdfToolNotFoundError
from mireport.pdf_converter._xhtml import normaliseToXhtml

SAMPLE_XLSX = (
    Path(__file__).parent.parent / "data" / "VSME-Digital-Template-Sample-1.2.0.xlsx"
)
PDF_BYTES = b"%PDF-1.7\n1 0 obj\n<< /Type /Catalog >>\nendobj\n%%EOF\n"

# Normalised the way production does it: the empty ix:header the normaliser
# injects is what makes the file an Inline XBRL Document, and so a document set
# member, rather than one dropped as "Non-inline file is not loadable".
CONVERTED = FilelikeAndFileName(
    fileContent=normaliseToXhtml(
        b"<html><head><title>a</title></head><body>annex</body></html>"
    ),
    filename="annex1.xhtml",
)


def _upload(client, pdfs=None, **extra):
    """POST /upload with the sample workbook and optional pdfs field."""
    form = {"file": (io.BytesIO(SAMPLE_XLSX.read_bytes()), "test.xlsx")}
    if pdfs is not None:
        form["pdfs"] = pdfs
    form.update(extra)
    return client.post("/upload", data=form, content_type="multipart/form-data")


def _pdf(name="annex1.pdf", content=PDF_BYTES):
    return (io.BytesIO(content), name)


def _conversionId(client):
    with client.session_transaction() as sess:
        return next(k for k in sess if k not in {"_permanent", "csrf_token"})


# ── upload ────────────────────────────────────────────────────────────────────


class TestUpload:
    def test_upload_without_pdfs_still_works(self, client):
        assert _upload(client).status_code == 303

    def test_no_pdfs_key_when_none_supplied(self, client):
        _upload(client)
        with client.session_transaction() as sess:
            assert not sess[_conversionId(client)].get("pdfs")

    def test_single_pdf_is_stored(self, client):
        assert _upload(client, pdfs=[_pdf()]).status_code == 303
        with client.session_transaction() as sess:
            stored = sess[_conversionId(client)]["pdfs"]
        assert len(stored) == 1
        assert FilelikeAndFileName.from_tuple(stored[0]).filename == "annex1.pdf"

    def test_multiple_pdfs_are_stored_in_order(self, client):
        _upload(client, pdfs=[_pdf("a.pdf"), _pdf("b.pdf"), _pdf("c.pdf")])
        with client.session_transaction() as sess:
            stored = sess[_conversionId(client)]["pdfs"]
        assert [FilelikeAndFileName.from_tuple(p).filename for p in stored] == [
            "a.pdf",
            "b.pdf",
            "c.pdf",
        ]

    def test_pdf_content_is_stored(self, client):
        _upload(client, pdfs=[_pdf()])
        with client.session_transaction() as sess:
            stored = sess[_conversionId(client)]["pdfs"]
        assert FilelikeAndFileName.from_tuple(stored[0]).fileContent == PDF_BYTES

    def test_empty_filename_is_ignored(self, client):
        """Browsers submit an empty part for an untouched file input."""
        assert _upload(client, pdfs=[(io.BytesIO(b""), "")]).status_code == 303
        with client.session_transaction() as sess:
            assert not sess[_conversionId(client)].get("pdfs")


class TestUploadValidation:
    @pytest.mark.parametrize(
        "filename", ["annex.txt", "annex.docx", "annex", "annex.pdf.exe"]
    )
    def test_non_pdf_is_rejected(self, client, filename):
        assert _upload(client, pdfs=[_pdf(filename)]).status_code == 400

    def test_rejection_mentions_pdf(self, client):
        resp = _upload(client, pdfs=[_pdf("annex.txt")])
        assert "pdf" in json.loads(resp.data).get("error", "").lower()

    def test_uppercase_extension_is_accepted(self, client):
        assert _upload(client, pdfs=[_pdf("ANNEX.PDF")]).status_code == 303

    def test_too_many_pdfs_rejected(self, client):
        pdfs = [_pdf(f"a{i}.pdf") for i in range(MAX_PDF_COUNT + 1)]
        assert _upload(client, pdfs=pdfs).status_code == 400

    def test_exactly_the_limit_is_accepted(self, client):
        pdfs = [_pdf(f"a{i}.pdf") for i in range(MAX_PDF_COUNT)]
        assert _upload(client, pdfs=pdfs).status_code == 303

    def test_one_bad_pdf_rejects_the_whole_upload(self, client):
        resp = _upload(client, pdfs=[_pdf("good.pdf"), _pdf("bad.txt")])
        assert resp.status_code == 400


class TestFeatureDisabled:
    """ENABLE_SUPPLEMENTARY_PDFS=False, for a deployment that does not want to
    accept anything but the workbook."""

    def test_the_form_does_not_offer_it(self, no_pdfs_client):
        assert b'name="pdfs"' not in no_pdfs_client.get("/").data

    def test_the_form_offers_it_when_enabled(self, client):
        assert b'name="pdfs"' in client.get("/").data

    def test_a_workbook_on_its_own_is_unaffected(self, no_pdfs_client):
        assert _upload(no_pdfs_client).status_code == 303

    def test_a_submitted_pdf_is_refused_rather_than_dropped(self, no_pdfs_client):
        """Only a stale page can send one, and silently discarding an upload is
        worse than saying no."""
        resp = _upload(no_pdfs_client, pdfs=[_pdf()])
        assert resp.status_code == 400
        assert "pdf" in json.loads(resp.data).get("error", "").lower()

    def test_an_empty_file_input_is_not_a_submitted_pdf(self, no_pdfs_client):
        assert _upload(no_pdfs_client, pdfs=[(io.BytesIO(b""), "")]).status_code == 303


# ── session round trip ────────────────────────────────────────────────────────


class TestSessionRoundTrip:
    def test_pdfs_survive_the_session_backend(self, client):
        """The server-side session serialises NamedTuples as lists, so a list of
        FilelikeAndFileName comes back as a list of lists. Reading it without
        from_tuple would work under a dict session and fail in production."""
        _upload(client, pdfs=[_pdf("a.pdf"), _pdf("b.pdf")])
        with client.session_transaction() as sess:
            stored = sess[_conversionId(client)]["pdfs"]
        recovered = [FilelikeAndFileName.from_tuple(p) for p in stored]
        assert [p.filename for p in recovered] == ["a.pdf", "b.pdf"]
        assert all(p.fileContent == PDF_BYTES for p in recovered)


# ── conversion ────────────────────────────────────────────────────────────────


@pytest.fixture
def convertedOk(monkeypatch):
    """pdf2htmlEX present and working. Patched on mireport.pdf_converter._batch,
    where the conversion policy actually lives — the web app only supplies the
    uploaded files and consumes the result."""
    from mireport.pdf_converter import (
        BACKENDS_IN_PREFERENCE_ORDER,
        ResolvedConverter,
        _batch,
    )

    monkeypatch.setattr(
        _batch,
        "findConverter",
        lambda: ResolvedConverter(
            BACKENDS_IN_PREFERENCE_ORDER[0], "/usr/bin/pdf2htmlEX"
        ),
    )
    monkeypatch.setattr(
        _batch,
        "convertPdfToHtml",
        lambda content, *, filename, converter, injectIxHeader=True: (
            FilelikeAndFileName(
                fileContent=CONVERTED.fileContent,
                filename=f"{Path(filename).stem}.xhtml",
            )
        ),
    )


@pytest.fixture
def noBinary(monkeypatch):
    from mireport.pdf_converter import _batch

    def raiser():
        raise PdfToolNotFoundError("pdf2htmlEX was not found")

    monkeypatch.setattr(_batch, "findConverter", raiser)


def _packageNames(client, conversionId):
    with client.session_transaction() as sess:
        package = FilelikeAndFileName.from_tuple(sess[conversionId]["zip"])
    with zipfile.ZipFile(io.BytesIO(package.fileContent)) as zf:
        return zf.namelist()


@pytest.mark.slow
class TestConversion:
    """Marked slow: each runs a full Excel -> Inline XBRL -> Arelle conversion."""

    def test_pdf_becomes_a_docset_member_and_an_attachment(
        self, client, convertedOk
    ) -> None:
        _upload(client, pdfs=[_pdf("annex1.pdf")])
        conversionId = _conversionId(client)
        client.get(f"/conversions/{conversionId}")
        names = _packageNames(client, conversionId)
        assert any(n.endswith("/annex1.xhtml") for n in names), names
        assert any(n.endswith("/attachments/annex1.pdf") for n in names), names

    def test_report_moves_into_the_docset_directory(self, client, convertedOk) -> None:
        _upload(client, pdfs=[_pdf("annex1.pdf")])
        conversionId = _conversionId(client)
        client.get(f"/conversions/{conversionId}")
        names = _packageNames(client, conversionId)
        directlyInReports = [n for n in names if "/reports/" in n and n.count("/") == 2]
        assert directlyInReports == [], names

    def test_no_pdfs_leaves_the_package_layout_alone(self, client) -> None:
        _upload(client)
        conversionId = _conversionId(client)
        client.get(f"/conversions/{conversionId}")
        names = _packageNames(client, conversionId)
        assert not any("/attachments/" in n for n in names)
        assert any(n.count("/") == 2 and "/reports/" in n for n in names), names

    @pytest.mark.parametrize(
        "error",
        [PdfToolNotFoundError("no binary"), PdfConversionError("bad pdf")],
        ids=["no-binary", "bad-pdf"],
    )
    def test_conversion_failure_is_a_warning_not_an_error(
        self, client, monkeypatch, error
    ) -> None:
        """A PDF that will not convert must not sink an otherwise-good report."""
        from mireport.pdf_converter import (
            BACKENDS_IN_PREFERENCE_ORDER,
            ResolvedConverter,
            _batch,
        )

        def boom(*args, **kwargs):
            raise error

        monkeypatch.setattr(_batch, "convertPdfToHtml", boom)
        monkeypatch.setattr(
            _batch,
            "findConverter",
            lambda: ResolvedConverter(BACKENDS_IN_PREFERENCE_ORDER[0], "/usr/bin/x"),
        )
        _upload(client, pdfs=[_pdf("annex1.pdf")])
        conversionId = _conversionId(client)
        client.get(f"/conversions/{conversionId}")
        with client.session_transaction() as sess:
            results = sess[conversionId]["results"]
        # "success" is ConversionResults.toDict()'s abbreviated key.
        assert results["success"] is True
        # Scoped to PDF warnings: the sample template raises its own unrelated
        # ones, so a bare count would pass for the wrong reason.
        pdfWarnings = [
            m for m in results["m"] if m["s"] == "WARNING" and "annex1.pdf" in m["m"]
        ]
        assert len(pdfWarnings) == 1, results["m"]

    def test_the_pdf_still_ships_when_conversion_fails(self, client, noBinary) -> None:
        """The whole point of attaching the original as well."""
        _upload(client, pdfs=[_pdf("annex1.pdf")])
        conversionId = _conversionId(client)
        client.get(f"/conversions/{conversionId}")
        names = _packageNames(client, conversionId)
        assert any(n.endswith("/attachments/annex1.pdf") for n in names), names
        assert not any(n.endswith(".xhtml") for n in names), names

    def test_report_layout_unchanged_when_conversion_fails(
        self, client, noBinary
    ) -> None:
        """No document set members means the report stays where it always was."""
        _upload(client, pdfs=[_pdf("annex1.pdf")])
        conversionId = _conversionId(client)
        client.get(f"/conversions/{conversionId}")
        names = _packageNames(client, conversionId)
        assert any(n.count("/") == 2 and "/reports/" in n for n in names), names

    def test_all_pdfs_still_attached_without_a_binary(self, client, noBinary) -> None:
        _upload(client, pdfs=[_pdf("a.pdf"), _pdf("b.pdf"), _pdf("c.pdf")])
        conversionId = _conversionId(client)
        client.get(f"/conversions/{conversionId}")
        names = _packageNames(client, conversionId)
        assert sum("/attachments/" in n for n in names) == 3, names


@pytest.mark.slow
class TestDocumentSetViewer:
    """The viewer of a document set is served, not downloaded.

    Its members are fetched by name relative to the viewer's own URL, so a
    single-file download of the primary alone would be useless.
    """

    @pytest.fixture
    def viewedConversion(self, client, convertedOk):
        _upload(client, pdfs=[_pdf("annex1.pdf")])
        conversionId = _conversionId(client)
        client.get(f"/conversions/{conversionId}")
        client.get(f"/downloadFile/{conversionId}/viewer/")
        return conversionId

    def test_viewer_has_document_set_members(self, client, viewedConversion) -> None:
        with client.session_transaction() as sess:
            members = sess[viewedConversion]["viewer_members"]
        assert list(members) == ["annex1.xhtml"], list(members)

    def test_download_redirects_to_the_served_viewer(
        self, client, viewedConversion
    ) -> None:
        resp = client.get(f"/downloadFile/{viewedConversion}/viewer/")
        assert resp.status_code == 303
        assert resp.location.endswith(f"/viewer/{viewedConversion}/")

    def test_members_are_served_where_the_viewer_looks_for_them(
        self, client, viewedConversion
    ) -> None:
        with client.session_transaction() as sess:
            names = list(sess[viewedConversion]["viewer_members"])
        for name in names:
            resp = client.get(f"/viewer/{viewedConversion}/{name}")
            assert resp.status_code == 200, name
            assert resp.data

    def test_primary_viewer_names_its_members(self, client, viewedConversion) -> None:
        resp = client.get(f"/viewer/{viewedConversion}/")
        assert resp.status_code == 200
        assert b"annex1.xhtml" in resp.data
