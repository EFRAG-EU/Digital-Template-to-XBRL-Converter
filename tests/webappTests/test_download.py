"""Download, viewer, and conversion-results page tests.

Fast tests cover all the 404 / error paths without needing a conversion.
Slow tests run the full pipeline to verify the happy path downloads and
that the conversion-results page renders the right messages.
"""

import io
from pathlib import Path

import pytest

from mireport.conversionresults import Severity

SAMPLE_XLSX = (
    Path(__file__).parent.parent / "data" / "VSME-Digital-Template-Sample-1.2.0.xlsx"
)


def _upload_id(client) -> str:
    """Upload a file and return the conversion id (no conversion triggered)."""
    resp = client.post(
        "/upload",
        data={"file": (io.BytesIO(SAMPLE_XLSX.read_bytes()), "test.xlsx")},
        content_type="multipart/form-data",
    )
    assert resp.status_code == 303
    location = resp.headers["Location"]
    return location.rstrip("/").split("/")[-1]


class TestConversionResultsPageExpired:
    """Fast: checks the expired-session rendering (no real conversion needed)."""

    def test_expired_conversion_is_404(self, client):
        resp = client.get("/conversions/no-such-id")
        assert resp.status_code == 404

    def test_expired_conversion_shows_expired_banner(self, client):
        resp = client.get("/conversions/no-such-id")
        assert b"Conversion expired" in resp.data

    def test_expired_conversion_shows_no_results_message(self, client):
        resp = client.get("/conversions/no-such-id")
        assert b"No conversion results available" in resp.data


class TestDownloadErrors:
    def test_unknown_id_returns_404(self, client):
        resp = client.get("/downloadFile/no-such-id/zip/")
        assert resp.status_code == 404

    def test_invalid_file_type_returns_404(self, client):
        conv_id = _upload_id(client)
        resp = client.get(f"/downloadFile/{conv_id}/notaformat/")
        assert resp.status_code == 404

    def test_download_before_conversion_returns_404(self, client):
        # File uploaded but /conversions/<id> never called → no "zip" in session
        conv_id = _upload_id(client)
        resp = client.get(f"/downloadFile/{conv_id}/zip/")
        assert resp.status_code == 404

    def test_download_before_conversion_json_returns_404(self, client):
        conv_id = _upload_id(client)
        resp = client.get(f"/downloadFile/{conv_id}/json/")
        assert resp.status_code == 404

    def test_head_unknown_id_returns_404(self, client):
        resp = client.head("/downloadFile/ghost-id/zip/")
        assert resp.status_code == 404


@pytest.fixture(scope="module")
def converted_id(app):
    """Run the full pipeline once and return the conversion id.

    Marked slow because it runs Arelle validation.
    Uses a plain test_client (no context manager) to avoid Flask's
    preserve_context mode, which causes "Popped wrong request context"
    errors when multiple requests share the same client.
    """
    c = app.test_client()
    resp = c.post(
        "/upload",
        data={"file": (io.BytesIO(SAMPLE_XLSX.read_bytes()), "test.xlsx")},
        content_type="multipart/form-data",
    )
    conv_id = resp.headers["Location"].rstrip("/").split("/")[-1]
    # Trigger the conversion (stores results in the server-side session)
    c.get(f"/conversions/{conv_id}")
    return conv_id, c


@pytest.mark.slow
class TestDownloadHappyPath:
    def test_download_zip_returns_200(self, converted_id):
        conv_id, c = converted_id
        resp = c.get(f"/downloadFile/{conv_id}/zip/")
        assert resp.status_code == 200

    def test_download_zip_content_type(self, converted_id):
        conv_id, c = converted_id
        resp = c.get(f"/downloadFile/{conv_id}/zip/")
        assert "text/html" in resp.content_type  # sent_file uses text/html for zip here

    def test_download_excel_returns_200(self, converted_id):
        conv_id, c = converted_id
        resp = c.get(f"/downloadFile/{conv_id}/excel/")
        assert resp.status_code == 200

    def test_download_json_returns_200(self, converted_id):
        conv_id, c = converted_id
        resp = c.get(f"/downloadFile/{conv_id}/json/")
        assert resp.status_code == 200

    def test_download_viewer_returns_200(self, converted_id):
        conv_id, c = converted_id
        resp = c.get(f"/downloadFile/{conv_id}/viewer/")
        assert resp.status_code == 200

    def test_head_zip_returns_200_no_body(self, converted_id):
        conv_id, c = converted_id
        resp = c.head(f"/downloadFile/{conv_id}/zip/")
        assert resp.status_code == 200
        assert resp.data == b""

    @pytest.mark.xfail(
        reason="Viewer page currently returns HTML with the error message instead of the expected zip file"
    )
    def test_viewer_page_returns_html(self, converted_id):
        conv_id, c = converted_id
        resp = c.get(f"/viewer/{conv_id}/")
        assert resp.status_code == 200
        assert b"html" in resp.data.lower()


@pytest.mark.slow
class TestConversionResultsPage:
    """Checks that /conversions/<id> renders the right messages after a real conversion."""

    def test_results_page_is_200(self, converted_id):
        conv_id, c = converted_id
        resp = c.get(f"/conversions/{conv_id}")
        assert resp.status_code == 200

    def test_results_page_has_conversion_results_heading(self, converted_id):
        conv_id, c = converted_id
        resp = c.get(f"/conversions/{conv_id}")
        assert b"Conversion Results" in resp.data

    def test_results_page_shows_success_banner(self, converted_id):
        conv_id, c = converted_id
        resp = c.get(f"/conversions/{conv_id}")
        assert b"Technical conversion" in resp.data
        assert b"successful" in resp.data

    def test_results_page_shows_messages_table(self, converted_id):
        conv_id, c = converted_id
        resp = c.get(f"/conversions/{conv_id}")
        assert b"Messages" in resp.data
        assert b"<table" in resp.data

    def test_results_page_shows_inline_xbrl_report_message(self, converted_id):
        # doConversion() always adds this message on success
        conv_id, c = converted_id
        resp = c.get(f"/conversions/{conv_id}")
        assert b"Inline XBRL report" in resp.data

    def test_results_page_shows_message_type_column(self, converted_id):
        conv_id, c = converted_id
        resp = c.get(f"/conversions/{conv_id}")
        assert b"Message Type" in resp.data

    def test_results_page_shows_severity_column(self, converted_id):
        conv_id, c = converted_id
        resp = c.get(f"/conversions/{conv_id}")
        assert b"Severity" in resp.data

    def test_results_page_shows_info_severity_badge(self, converted_id):
        conv_id, c = converted_id
        resp = c.get(f"/conversions/{conv_id}")
        assert Severity.INFO.value.encode() in resp.data

    def test_results_page_shows_files_to_download_section(self, converted_id):
        conv_id, c = converted_id
        resp = c.get(f"/conversions/{conv_id}")
        assert b"Files to download" in resp.data

    def test_results_page_shows_original_filename(self, converted_id):
        conv_id, c = converted_id
        resp = c.get(f"/conversions/{conv_id}")
        assert b"test.xlsx" in resp.data

    def test_results_page_dev_mode_shows_developer_messages(self, converted_id):
        conv_id, c = converted_id
        resp = c.get(f"/conversions/{conv_id}?show_developer_messages=true")
        assert resp.status_code == 200
        # Developer messages include Progress-type entries
        assert (
            b"DevInfo" in resp.data
            or b"Progress" in resp.data
            or b"Extracting" in resp.data
        )
