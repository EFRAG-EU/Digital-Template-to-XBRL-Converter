"""create_app and the app_setup helpers it uses."""

import logging

from digital_converter_webapp import create_app, routes
from mireport.conversionresults import ConversionResultsBuilder

from .conftest import _test_config


class TestBrokenConfig:
    def test_unusable_session_config_yields_broken_app(self):
        # Session backend selection sees test_config: a deployment with no
        # usable SESSION_TYPE must degrade to the 503 brokenApp, not limp on
        app = create_app({"TESTING": True, "DEPLOYMENT": "production"})
        resp = app.test_client().get("/")
        assert resp.status_code == 503

    def test_dropped_session_file_dir_warns(self, caplog, tmp_path):
        with caplog.at_level(logging.WARNING, logger="digital_converter_webapp"):
            create_app({"TESTING": True, "SESSION_FILE_DIR": str(tmp_path)})
        assert any("SESSION_FILE_DIR" in r.message for r in caplog.records)


class TestDeploymentHeader:
    def test_response_includes_deployment_header(self, client):
        resp = client.get("/")
        assert "X-Deployment-Datetime" in resp.headers


class TestExcelParsingWarningsAsDevInfo:
    def test_off_by_default(self, app):
        assert app.config["EXCEL_PARSING_WARNINGS_AS_DEVINFO"] is False

    def test_string_from_environment_is_truthy(self, tmp_path):
        app = create_app(
            _test_config(tmp_path, EXCEL_PARSING_WARNINGS_AS_DEVINFO="true")
        )
        assert app.config["EXCEL_PARSING_WARNINGS_AS_DEVINFO"] is True

    def test_passed_to_the_conversion(self, tmp_path, monkeypatch, upload_id):
        seen = {}

        def fakeDoConversion(conv, arelle, pdfMode, **kwargs):
            seen.update(kwargs)
            return ConversionResultsBuilder(conversionId=conv.id).build()

        monkeypatch.setattr(routes, "doConversion", fakeDoConversion)
        monkeypatch.setattr(routes, "getArelle", lambda: None)
        app = create_app(_test_config(tmp_path, EXCEL_PARSING_WARNINGS_AS_DEVINFO=True))
        client = app.test_client()
        client.get(f"/conversions/{upload_id(client)}")
        assert seen == {"excelParsingWarningsAsDevInfo": True}
