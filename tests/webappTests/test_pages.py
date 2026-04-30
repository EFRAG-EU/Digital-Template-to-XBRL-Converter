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


class TestConversionsList:
    def test_conversions_list_loads(self, client):
        resp = client.get("/conversions/")
        assert resp.status_code == 200

    def test_unknown_conversion_is_404(self, client):
        resp = client.get("/conversions/does-not-exist")
        assert resp.status_code == 404


class TestCaptcha:
    def test_captcha_returns_200(self, client):
        resp = client.get("/generate_captcha")
        assert resp.status_code == 200

    def test_captcha_returns_json_with_question(self, client):
        resp = client.get("/generate_captcha")
        body = json.loads(resp.data)
        assert "question" in body

    def test_captcha_question_contains_plus(self, client):
        resp = client.get("/generate_captcha")
        body = json.loads(resp.data)
        assert "+" in body["question"]


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


class TestDeploymentHeader:
    def test_response_includes_deployment_header(self, client):
        resp = client.get("/")
        assert "X-Deployment-Datetime" in resp.headers
