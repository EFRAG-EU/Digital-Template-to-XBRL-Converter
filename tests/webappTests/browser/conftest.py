"""Browser tests: the web app served for real, driven through Chromium by
Playwright, so the pages' JavaScript (download polling, spinners, toggles) runs.

The server runs on a thread in the test process rather than a forked one, so a
``monkeypatch`` on Arelle reaches the conversion the page triggers. The whole
directory is skipped when Playwright or its Chromium build isn't installed
(``python -m playwright install chromium``).
"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from werkzeug.serving import make_server

from mireport import template_versions

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator

    from flask import Flask
    from playwright.sync_api import Browser, FilePayload, Page

try:
    from playwright.sync_api import sync_playwright
except ImportError:
    collect_ignore_glob = ["test_*.py"]
else:
    with sync_playwright() as p:
        if not Path(p.chromium.executable_path).exists():
            collect_ignore_glob = ["test_*.py"]

SAMPLE_XLSX = (
    Path(__file__).parents[3]
    / "digital-templates"
    / "VSME-Digital-Template-Sample-1.3.0.xlsx"
)

# Conversion runs inside the results page request, Arelle and all.
CONVERSION_TIMEOUT_MS = 60_000


@pytest.hookimpl(tryfirst=True)
def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    """All the browser tests on one xdist worker: one Chromium, one server and
    Arelle loaded once. Spread across workers, each pays for all three and the
    conversions fight for the CPU, which more than doubles them. First, so the
    marks are there when xdist reads them."""
    browserDir = Path(__file__).parent
    for item in items:
        if browserDir in item.path.parents:
            item.add_marker(pytest.mark.xdist_group("browser"))


@pytest.fixture(scope="session")
def serve() -> Iterator[Callable[[Flask], str]]:
    """``serve(app)`` -> the base URL of ``app`` served on a free local port."""
    servers = []

    def _serve(app: Flask) -> str:
        server = make_server("127.0.0.1", 0, app, threaded=True)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        servers.append(server)
        return f"http://127.0.0.1:{server.server_port}"

    yield _serve
    for server in servers:
        server.shutdown()


@pytest.fixture(scope="session", autouse=True)
def converterTemplateVersion() -> Iterator[None]:
    """Templates are checked against the converter's own version, installed
    migration tool or not, so the sample converts cleanly everywhere."""
    with pytest.MonkeyPatch.context() as monkeypatch:
        monkeypatch.setattr(
            template_versions, "migrationTargetTemplateVersion", lambda: None
        )
        yield


@pytest.fixture(scope="session")
def browser_context_args(browser_context_args, serve, app) -> dict:
    """pytest-playwright's ``page``: our app, downloads accepted."""
    return {**browser_context_args, "base_url": serve(app), "accept_downloads": True}


def _uploadAndConvert(page: Page, workbook: Path | FilePayload = SAMPLE_XLSX) -> None:
    """Upload ``workbook`` on the home page and wait for its results page."""
    page.goto("/")
    page.set_input_files("#file", workbook)
    page.check("#terms")
    # The click waits for the navigation, which is the whole conversion.
    page.get_by_role("button", name="Upload & Convert Digital Template").click(
        timeout=CONVERSION_TIMEOUT_MS
    )
    page.get_by_role("heading", name="Conversion Results").wait_for()


@pytest.fixture(scope="session")
def uploadAndConvert() -> Callable[..., None]:
    """``uploadAndConvert(page, workbook=sample)``: upload on the home page and
    wait for the results page. ``workbook`` is a path or a Playwright file
    payload."""
    return _uploadAndConvert


@pytest.fixture(scope="module")
def convertedPage(browser: Browser, browser_context_args) -> Iterator[Page]:
    """The results page of one real conversion of the sample workbook, Arelle
    validation included, shared by the tests of a module."""
    context = browser.new_context(**browser_context_args)
    page = context.new_page()
    _uploadAndConvert(page)
    yield page
    context.close()
