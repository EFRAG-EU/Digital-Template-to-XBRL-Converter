"""POST /upload route tests: the workbook, the form options and the
supplementary PDFs. The limits come from ``digital_converter_webapp.forms``."""

import io
import json

import pytest

from digital_converter_webapp.forms import MAX_PDF_COUNT


class TestUploadValidation:
    def test_no_file_part_returns_400(self, client):
        resp = client.post("/upload", data={}, content_type="multipart/form-data")
        assert resp.status_code == 400

    def test_empty_filename_returns_400(self, client):
        resp = client.post(
            "/upload",
            data={"file": (io.BytesIO(b""), "")},
            content_type="multipart/form-data",
        )
        assert resp.status_code == 400

    def test_wrong_extension_returns_400(self, client, upload):
        resp = upload(client, filename="report.csv", data=b"col1,col2\n1,2")
        assert resp.status_code == 400

    def test_wrong_extension_error_mentions_xlsx(self, client, upload):
        resp = upload(client, filename="report.txt", data=b"hello")
        body = json.loads(resp.data)
        assert "xlsx" in body.get("error", "").lower()

    def test_too_many_files_returns_400(self, client, sample_xlsx):
        resp = client.post(
            "/upload",
            data={
                "file": [
                    (io.BytesIO(sample_xlsx), "a.xlsx"),
                    (io.BytesIO(sample_xlsx), "b.xlsx"),
                ]
            },
            content_type="multipart/form-data",
        )
        assert resp.status_code == 400


class TestUploadHappyPath:
    def test_valid_xlsx_redirects_to_conversion(self, client, upload):
        resp = upload(client)
        assert resp.status_code == 303
        assert "/conversions/" in resp.headers["Location"]

    def test_valid_xlsx_with_locale_option_redirects(self, client, upload):
        resp = upload(client, localeOption="manual", locale="en-GB")
        assert resp.status_code == 303

    def test_valid_xlsx_with_palette_redirects(self, client, upload):
        resp = upload(client, style_palette="EFRAG Blue")
        assert resp.status_code == 303

    def test_valid_xlsx_with_mode_redirects(self, client, upload):
        resp = upload(client, style_mode="full")
        assert resp.status_code == 303


@pytest.fixture()
def tiny_limit_client(app):
    """Client for an app whose MAX_CONTENT_LENGTH is only 10 bytes."""
    original = app.config["MAX_CONTENT_LENGTH"]
    app.config["MAX_CONTENT_LENGTH"] = 10
    yield app.test_client()
    app.config["MAX_CONTENT_LENGTH"] = original


class TestUploadSizeLimits:
    def test_oversized_upload_returns_413(self, tiny_limit_client):
        resp = tiny_limit_client.post(
            "/upload",
            data={"file": (io.BytesIO(b"x" * 100), "big.xlsx")},
            content_type="multipart/form-data",
        )
        assert resp.status_code == 413


class TestPdfUpload:
    def test_upload_without_pdfs_still_works(self, client, upload):
        assert upload(client).status_code == 303

    def test_no_pdfs_key_when_none_supplied(self, client, upload, conversion_of):
        upload(client)
        assert not conversion_of(client).pdfs

    def test_single_pdf_is_stored(self, client, upload, make_pdf, conversion_of):
        assert upload(client, pdfs=[make_pdf()]).status_code == 303
        stored = conversion_of(client).pdfs
        assert len(stored) == 1
        assert stored[0].filename == "annex1.pdf"

    def test_multiple_pdfs_are_stored_in_order(
        self, client, upload, make_pdf, conversion_of
    ):
        upload(client, pdfs=[make_pdf("a.pdf"), make_pdf("b.pdf"), make_pdf("c.pdf")])
        assert [p.filename for p in conversion_of(client).pdfs] == [
            "a.pdf",
            "b.pdf",
            "c.pdf",
        ]

    def test_pdf_content_is_stored(
        self, client, upload, make_pdf, conversion_of, pdf_bytes
    ):
        upload(client, pdfs=[make_pdf()])
        assert conversion_of(client).pdfs[0].fileContent == pdf_bytes

    def test_empty_filename_is_ignored(self, client, upload, conversion_of):
        """Browsers submit an empty part for an untouched file input."""
        assert upload(client, pdfs=[(io.BytesIO(b""), "")]).status_code == 303
        assert not conversion_of(client).pdfs


class TestPdfUploadValidation:
    @pytest.mark.parametrize(
        "filename", ["annex.txt", "annex.docx", "annex", "annex.pdf.exe"]
    )
    def test_non_pdf_is_rejected(self, client, upload, make_pdf, filename):
        assert upload(client, pdfs=[make_pdf(filename)]).status_code == 400

    def test_rejection_mentions_pdf(self, client, upload, make_pdf):
        resp = upload(client, pdfs=[make_pdf("annex.txt")])
        assert "pdf" in json.loads(resp.data).get("error", "").lower()

    def test_uppercase_extension_is_accepted(self, client, upload, make_pdf):
        assert upload(client, pdfs=[make_pdf("ANNEX.PDF")]).status_code == 303

    def test_too_many_pdfs_rejected(self, client, upload, make_pdf):
        pdfs = [make_pdf(f"a{i}.pdf") for i in range(MAX_PDF_COUNT + 1)]
        assert upload(client, pdfs=pdfs).status_code == 400

    def test_exactly_the_limit_is_accepted(self, client, upload, make_pdf):
        pdfs = [make_pdf(f"a{i}.pdf") for i in range(MAX_PDF_COUNT)]
        assert upload(client, pdfs=pdfs).status_code == 303

    def test_one_bad_pdf_rejects_the_whole_upload(self, client, upload, make_pdf):
        resp = upload(client, pdfs=[make_pdf("good.pdf"), make_pdf("bad.txt")])
        assert resp.status_code == 400


class TestPdfFeatureDisabled:
    """ENABLE_SUPPLEMENTARY_PDFS=False, for a deployment that does not want to
    accept anything but the workbook."""

    def test_the_form_does_not_offer_it(self, no_pdfs_client):
        assert b'name="pdfs"' not in no_pdfs_client.get("/").data

    def test_the_form_offers_it_when_enabled(self, client):
        assert b'name="pdfs"' in client.get("/").data

    def test_a_workbook_on_its_own_is_unaffected(self, no_pdfs_client, upload):
        assert upload(no_pdfs_client).status_code == 303

    def test_a_submitted_pdf_is_refused_rather_than_dropped(
        self, no_pdfs_client, upload, make_pdf
    ):
        """Only a stale page can send one, and silently discarding an upload is
        worse than saying no."""
        resp = upload(no_pdfs_client, pdfs=[make_pdf()])
        assert resp.status_code == 400
        assert "pdf" in json.loads(resp.data).get("error", "").lower()

    def test_an_empty_file_input_is_not_a_submitted_pdf(self, no_pdfs_client, upload):
        assert upload(no_pdfs_client, pdfs=[(io.BytesIO(b""), "")]).status_code == 303
