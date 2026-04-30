"""POST /upload route tests."""

import io
from pathlib import Path

import pytest

SAMPLE_XLSX = (
    Path(__file__).parent.parent / "data" / "VSME-Digital-Template-Sample-1.2.0.xlsx"
)


def _xlsx_upload(client, filename="test.xlsx", data=None, extra_form=None):
    """POST /upload with an XLSX file payload."""
    file_data = data if data is not None else SAMPLE_XLSX.read_bytes()
    form = {
        "file": (io.BytesIO(file_data), filename),
    }
    if extra_form:
        form.update(extra_form)
    return client.post(
        "/upload",
        data=form,
        content_type="multipart/form-data",
    )


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

    def test_wrong_extension_returns_400(self, client):
        resp = _xlsx_upload(client, filename="report.csv", data=b"col1,col2\n1,2")
        assert resp.status_code == 400

    def test_wrong_extension_error_mentions_xlsx(self, client):
        import json

        resp = _xlsx_upload(client, filename="report.txt", data=b"hello")
        body = json.loads(resp.data)
        assert "xlsx" in body.get("error", "").lower()

    def test_too_many_files_returns_400(self, client):
        file_bytes = SAMPLE_XLSX.read_bytes()
        resp = client.post(
            "/upload",
            data={
                "file": [
                    (io.BytesIO(file_bytes), "a.xlsx"),
                    (io.BytesIO(file_bytes), "b.xlsx"),
                ]
            },
            content_type="multipart/form-data",
        )
        assert resp.status_code == 400


class TestUploadHappyPath:
    def test_valid_xlsx_redirects_to_conversion(self, client):
        resp = _xlsx_upload(client)
        assert resp.status_code == 303
        assert "/conversions/" in resp.headers["Location"]

    def test_valid_xlsx_with_locale_option_redirects(self, client):
        resp = _xlsx_upload(
            client, extra_form={"localeOption": "manual", "locale": "en-GB"}
        )
        assert resp.status_code == 303

    def test_valid_xlsx_with_palette_redirects(self, client):
        resp = _xlsx_upload(client, extra_form={"style_palette": "EFRAG Blue"})
        assert resp.status_code == 303

    def test_valid_xlsx_with_mode_redirects(self, client):
        resp = _xlsx_upload(client, extra_form={"style_mode": "full"})
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
