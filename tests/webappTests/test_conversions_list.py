"""The conversions list and the delete routes — session management without
running a conversion."""


class TestConversionsList:
    def test_conversions_list_loads(self, client):
        resp = client.get("/conversions/")
        assert resp.status_code == 200

    def test_unknown_conversion_is_404(self, client):
        resp = client.get("/conversions/does-not-exist")
        assert resp.status_code == 404


class TestDeleteSingle:
    def test_delete_redirects_to_conversions_list(self, client, upload_id):
        conv_id = upload_id(client)
        resp = client.post(f"/delete/{conv_id}")
        assert resp.status_code == 303
        assert "/conversions/" in resp.headers["Location"]

    def test_deleted_conversion_no_longer_in_list(self, client, upload_id):
        conv_id = upload_id(client)
        client.post(f"/delete/{conv_id}")
        resp = client.get(f"/conversions/{conv_id}")
        assert resp.status_code == 404

    def test_delete_nonexistent_id_is_graceful(self, client):
        # Should redirect without crashing, not 500
        resp = client.post("/delete/no-such-id")
        assert resp.status_code == 303


class TestDeleteAll:
    def test_delete_all_redirects_to_conversions_list(self, client, upload_id):
        upload_id(client)
        resp = client.post("/delete/_all")
        assert resp.status_code == 303
        assert "/conversions/" in resp.headers["Location"]

    def test_delete_all_when_empty_is_graceful(self, client):
        # Start from a clean session by deleting all first
        client.post("/delete/_all")
        resp = client.post("/delete/_all")
        assert resp.status_code == 303
