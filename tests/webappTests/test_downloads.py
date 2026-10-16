"""downloadFile route tests.

Fast tests cover all the 404 / error paths without needing a conversion.
Slow tests run the full pipeline to verify the happy path downloads.
"""

import pytest


class TestDownloadErrors:
    def test_unknown_id_returns_404(self, client):
        resp = client.get("/downloadFile/no-such-id/zip/")
        assert resp.status_code == 404

    def test_invalid_file_type_returns_404(self, client, upload_id):
        conv_id = upload_id(client)
        resp = client.get(f"/downloadFile/{conv_id}/notaformat/")
        assert resp.status_code == 404

    def test_download_before_conversion_returns_404(self, client, upload_id):
        # File uploaded but /conversions/<id> never called → no "zip" in session
        conv_id = upload_id(client)
        resp = client.get(f"/downloadFile/{conv_id}/zip/")
        assert resp.status_code == 404

    def test_download_before_conversion_json_returns_404(self, client, upload_id):
        conv_id = upload_id(client)
        resp = client.get(f"/downloadFile/{conv_id}/json/")
        assert resp.status_code == 404

    def test_head_unknown_id_returns_404(self, client):
        resp = client.head("/downloadFile/ghost-id/zip/")
        assert resp.status_code == 404


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
