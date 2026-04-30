"""POST /delete routes — session management without conversion."""

import io
from pathlib import Path

SAMPLE_XLSX = (
    Path(__file__).parent.parent / "data" / "VSME-Digital-Template-Sample-1.2.0.xlsx"
)


def _upload_id(client):
    """Upload a file and return the conversion id from the redirect Location."""
    resp = client.post(
        "/upload",
        data={"file": (io.BytesIO(SAMPLE_XLSX.read_bytes()), "test.xlsx")},
        content_type="multipart/form-data",
    )
    assert resp.status_code == 303
    # Location is /conversions/<id>
    location = resp.headers["Location"]
    return location.rstrip("/").split("/")[-1]


class TestDeleteSingle:
    def test_delete_redirects_to_conversions_list(self, client):
        conv_id = _upload_id(client)
        resp = client.post(f"/delete/{conv_id}")
        assert resp.status_code == 303
        assert "/conversions/" in resp.headers["Location"]

    def test_deleted_conversion_no_longer_in_list(self, client):
        conv_id = _upload_id(client)
        client.post(f"/delete/{conv_id}")
        resp = client.get(f"/conversions/{conv_id}")
        assert resp.status_code == 404

    def test_delete_nonexistent_id_is_graceful(self, client):
        # Should redirect without crashing, not 500
        resp = client.post("/delete/no-such-id")
        assert resp.status_code == 303


class TestDeleteAll:
    def test_delete_all_redirects_to_conversions_list(self, client):
        _upload_id(client)
        resp = client.post("/delete/_all")
        assert resp.status_code == 303
        assert "/conversions/" in resp.headers["Location"]

    def test_delete_all_when_empty_is_graceful(self, client):
        # Start from a clean session by deleting all first
        client.post("/delete/_all")
        resp = client.post("/delete/_all")
        assert resp.status_code == 303
