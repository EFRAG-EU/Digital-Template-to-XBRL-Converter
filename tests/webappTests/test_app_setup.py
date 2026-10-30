"""create_app and the app_setup helpers it uses."""

import logging

from flask import Flask

from digital_converter_webapp import create_app, routes
from digital_converter_webapp.app_setup import (
    BRANDING_DEFAULTS,
    ResultsLayout,
    applyBrandingDefaults,
)
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


class TestResultsLayout:
    def test_flat_by_default(self, tmp_path):
        config = _test_config(tmp_path)
        del config["RESULTS_LAYOUT"]
        assert create_app(config).config["RESULTS_LAYOUT"] is ResultsLayout.FLAT

    def test_string_from_environment(self, tmp_path):
        app = create_app(_test_config(tmp_path, RESULTS_LAYOUT="sections"))
        assert app.config["RESULTS_LAYOUT"] is ResultsLayout.SECTIONS

    def test_unknown_value_warns_and_is_flat(self, caplog, tmp_path):
        with caplog.at_level(logging.WARNING, logger="digital_converter_webapp"):
            app = create_app(_test_config(tmp_path, RESULTS_LAYOUT="accordion"))
        assert app.config["RESULTS_LAYOUT"] is ResultsLayout.FLAT
        assert any("RESULTS_LAYOUT" in r.message for r in caplog.records)


class TestBrandingDefaults:
    def test_fills_in_unset_keys(self):
        app = Flask(__name__)
        applyBrandingDefaults(app)
        for key, value in BRANDING_DEFAULTS.items():
            assert app.config[key] == value
        assert "BRAND_TEMPLATE_DIR" not in app.config

    def test_keeps_values_already_set(self):
        app = Flask(__name__)
        app.config["BRAND_PRODUCT_NAME"] = "Acme Converter"
        app.config["BRAND_LOGO_URL"] = ""
        applyBrandingDefaults(app)
        assert app.config["BRAND_PRODUCT_NAME"] == "Acme Converter"
        assert app.config["BRAND_LOGO_URL"] == ""
        assert app.config["BRAND_ORGANISATION_NAME"] == "EFRAG"

    def test_create_app_applies_them(self, app):
        assert app.config["BRAND_CONTACT_EMAIL"] == "vsme@efrag.org"
