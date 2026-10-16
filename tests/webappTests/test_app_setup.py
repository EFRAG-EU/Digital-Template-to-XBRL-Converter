"""create_app and the app_setup helpers it uses."""

import logging

from digital_converter_webapp import create_app


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
