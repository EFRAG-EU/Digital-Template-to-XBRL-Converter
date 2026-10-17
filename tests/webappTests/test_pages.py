"""GET-only route smoke tests — no conversion required."""

import json

import mireport


class TestHomePage:
    def test_home_loads(self, client):
        resp = client.get("/")
        assert resp.status_code == 200

    def test_home_contains_upload_form(self, client):
        resp = client.get("/")
        assert b"<form" in resp.data

    def test_home_contains_file_input(self, client):
        resp = client.get("/")
        assert b'type="file"' in resp.data


class TestLocales:
    def test_locales_json_loads(self, client):
        url = f"/locales/available_{mireport.__version__}.json"
        resp = client.get(url)
        assert resp.status_code == 200

    def test_locales_json_is_list(self, client):
        url = f"/locales/available_{mireport.__version__}.json"
        resp = client.get(url)
        body = json.loads(resp.data)
        assert isinstance(body, list)
        assert len(body) > 0

    def test_locales_json_entries_have_label(self, client):
        url = f"/locales/available_{mireport.__version__}.json"
        resp = client.get(url)
        body = json.loads(resp.data)
        for entry in body:
            assert "label" in entry


class TestDebugSession:
    def test_not_found_when_debug_off(self, client):
        resp = client.get("/debug_session")
        assert resp.status_code == 404

    def test_available_when_debug_on(self, app, client, monkeypatch):
        monkeypatch.setitem(app.config, "DEBUG", True)
        resp = client.get("/debug_session")
        assert resp.status_code == 200
        assert resp.is_json
