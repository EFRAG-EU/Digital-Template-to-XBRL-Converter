import pytest
from cachelib.file import FileSystemCache

from digital_converter_webapp import create_app


def _test_config(session_dir, **overrides):
    return {
        "TESTING": True,
        "SESSION_CACHELIB": FileSystemCache(str(session_dir)),
        "SECRET_KEY": "test-secret",
        **overrides,
    }


@pytest.fixture(scope="session")
def app(tmp_path_factory):
    return create_app(_test_config(tmp_path_factory.mktemp("sessions")))


@pytest.fixture()
def client(app):
    return app.test_client()


@pytest.fixture(scope="session")
def captcha_app(tmp_path_factory):
    return create_app(
        _test_config(tmp_path_factory.mktemp("captcha-sessions"), ENABLE_CAPTCHA=True)
    )


@pytest.fixture()
def captcha_client(captcha_app):
    return captcha_app.test_client()


@pytest.fixture(scope="session")
def no_pdfs_app(tmp_path_factory):
    return create_app(
        _test_config(
            tmp_path_factory.mktemp("no-pdf-sessions"), ENABLE_SUPPLEMENTARY_PDFS=False
        )
    )


@pytest.fixture()
def no_pdfs_client(no_pdfs_app):
    return no_pdfs_app.test_client()
