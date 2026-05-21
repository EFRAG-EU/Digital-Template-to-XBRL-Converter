import pytest

from digital_converter_webapp import create_app


@pytest.fixture(scope="session")
def app(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("sessions")
    return create_app(
        {
            "TESTING": True,
            "SESSION_FILE_DIR": str(tmp),
            "SECRET_KEY": "test-secret",
        }
    )


@pytest.fixture()
def client(app):
    return app.test_client()
