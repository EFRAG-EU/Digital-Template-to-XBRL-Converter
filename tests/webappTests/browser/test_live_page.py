"""The results page while the steps run on a converted report: shown at once,
then brought up to date in place as Arelle validates and builds the viewer.

The app runs the steps in the background, as deployed. Arelle is faked, so a
test decides when validation finishes and what it finds.
"""

import re
import threading

import pytest
from cachelib.file import FileSystemCache
from playwright.sync_api import Page, expect

from digital_converter_webapp import create_app
from mireport.arelle.report_info import ArelleReportProcessor
from mireport.arelle.support import ArelleProcessingResult
from mireport.conversionresults import Message, MessageType, Severity
from mireport.filesupport import FilelikeAndFileName

DOWNLOAD_BUTTONS = ("Download viewer", "Download XBRL Report", "Download xBRL-JSON")
VALIDATING = "Validating the generated XBRL report"
VALID = "The generated XBRL report is valid according to the technical XBRL"
INVALID = "The generated XBRL report has validation ERRORS"
FAKE_ERROR = "[test:fake] A validation error made up by the test"
# Long enough to see a step through, short enough to fail quickly.
SETTLE_TIMEOUT_MS = 30_000


class FakeArelle:
    """Arelle as the steps call it. Package validation waits for ``gate``,
    open unless a test closes it, and finds an ERROR if ``packageError``."""

    def __init__(self) -> None:
        self.gate = threading.Event()
        self.gate.set()
        self.packageError = False
        self.viewersBuilt = 0

    def validateReportPackage(self, processor, source, **kwargs):
        assert self.gate.wait(timeout=60), "The test never let validation go ahead"
        result = ArelleProcessingResult()
        if self.packageError:
            result._validationMessages.append(
                Message(FAKE_ERROR, Severity.ERROR, MessageType.XbrlValidation)
            )
        return result

    def validateXbrlJson(self, processor, document):
        return ArelleProcessingResult()

    def generateInlineViewer(self, processor, source):
        self.viewersBuilt += 1
        result = ArelleProcessingResult()
        result._viewer = FilelikeAndFileName(
            fileContent=b"<html><body>viewer</body></html>",
            filename="report_viewer.html",
        )
        return result


@pytest.fixture
def arelle(monkeypatch):
    fake = FakeArelle()
    for name in ("validateReportPackage", "validateXbrlJson", "generateInlineViewer"):
        method = getattr(fake, name)
        monkeypatch.setattr(
            ArelleReportProcessor,
            name,
            lambda processor, *args, _method=method, **kwargs: _method(
                processor, *args, **kwargs
            ),
        )
    yield fake
    # No step is left waiting on a test that has finished.
    fake.gate.set()


@pytest.fixture(scope="module")
def backgroundUrl(serve, tmp_path_factory) -> str:
    """An app that runs the steps in the background, as deployed."""
    return serve(
        create_app(
            {
                "TESTING": True,
                "SESSION_CACHELIB": FileSystemCache(
                    str(tmp_path_factory.mktemp("live-sessions"))
                ),
                "SECRET_KEY": "test-secret",
            }
        )
    )


@pytest.fixture
def context(browser, browser_context_args, backgroundUrl):
    context = browser.new_context(**{**browser_context_args, "base_url": backgroundUrl})
    yield context
    context.close()


@pytest.fixture
def page(context) -> Page:
    return context.new_page()


def expectSettled(page: Page) -> None:
    expect(page.locator("#conversion-results")).to_have_attribute(
        "data-state", "complete", timeout=SETTLE_TIMEOUT_MS
    )


def test_page_is_shown_while_validating_then_updates_itself(
    page: Page, uploadAndConvert, arelle
):
    arelle.gate.clear()
    uploadAndConvert(page)

    expect(page.get_by_text(VALIDATING)).to_be_visible()
    expect(page.locator("#conversion-results")).to_have_attribute(
        "data-state", "pending"
    )

    arelle.gate.set()

    expectSettled(page)
    expect(page.get_by_text(VALID)).to_be_visible()
    expect(page.get_by_text(VALIDATING)).to_be_hidden()
    for name in DOWNLOAD_BUTTONS:
        expect(page.get_by_role("link", name=name)).not_to_have_attribute(
            "aria-disabled", "true"
        )


def test_download_waits_for_validation(page: Page, uploadAndConvert, arelle):
    arelle.gate.clear()
    uploadAndConvert(page)

    with page.expect_download(timeout=SETTLE_TIMEOUT_MS) as info:
        page.get_by_role("link", name="Download xBRL-JSON").click()
        expect(page.locator("#loadingSpinner")).to_be_visible()
        arelle.gate.set()

    assert info.value.suggested_filename.endswith(".json")
    expect(page.locator("#loadingSpinner")).to_be_hidden()


def test_validation_errors_cancel_a_waiting_download(
    page: Page, uploadAndConvert, arelle
):
    arelle.gate.clear()
    arelle.packageError = True
    downloads = []
    page.on("download", lambda download: downloads.append(download))
    uploadAndConvert(page)

    page.get_by_role("link", name="Download xBRL-JSON").click()
    expect(page.locator("#loadingSpinner")).to_be_visible()
    arelle.gate.set()

    expect(page.locator("#errorModal")).to_be_visible(timeout=SETTLE_TIMEOUT_MS)
    expect(page.locator("#errorMessage")).to_contain_text("Validation found errors")
    expect(page.locator("#loadingSpinner")).to_be_hidden()
    expectSettled(page)
    expect(page.get_by_text(INVALID)).to_be_visible()
    expect(page.get_by_text(FAKE_ERROR)).to_be_visible()
    for name in DOWNLOAD_BUTTONS:
        expect(page.get_by_role("link", name=name)).to_have_attribute(
            "aria-disabled", "true"
        )
    assert downloads == []


def test_viewer_built_in_the_background_downloads_at_once(
    page: Page, uploadAndConvert, arelle
):
    uploadAndConvert(page)
    expectSettled(page)
    assert arelle.viewersBuilt == 1

    with page.expect_download(timeout=5_000) as info:
        page.get_by_role("link", name="Download viewer").click()

    assert info.value.suggested_filename == "report_viewer.html"
    assert arelle.viewersBuilt == 1


def test_a_section_the_user_opened_stays_open(page: Page, uploadAndConvert, arelle):
    arelle.gate.clear()
    arelle.packageError = True
    uploadAndConvert(page)
    report, package = page.locator("#section-report"), page.locator("#section-package")

    report.locator("summary").click()
    arelle.gate.set()

    expectSettled(page)
    expect(report).to_have_attribute("open", "")
    # Untouched, it opens itself for the error it now has.
    expect(package).to_have_attribute("open", "")


def test_a_section_the_user_closed_stays_closed(page: Page, uploadAndConvert, arelle):
    arelle.gate.clear()
    arelle.packageError = True
    uploadAndConvert(page)
    package = page.locator("#section-package")

    package.locator("summary").click()
    expect(package).to_have_attribute("open", "")
    package.locator("summary").click()
    arelle.gate.set()

    expectSettled(page)
    expect(package.get_by_text(FAKE_ERROR)).to_be_attached()
    expect(package).not_to_have_attribute("open", "")


def test_developer_messages_follow_the_updates(page: Page, uploadAndConvert, arelle):
    arelle.gate.clear()
    uploadAndConvert(page)
    toggle = page.get_by_label("Display software developer messages")

    toggle.check()
    page.wait_for_url(re.compile(r"show_developer_messages=true"))
    expect(page.get_by_text(VALIDATING)).to_be_visible()
    arelle.gate.set()

    expectSettled(page)
    expect(toggle).to_be_checked()
    expect(page.locator("#section-package").get_by_title("Duration")).to_be_visible()


def test_two_tabs_settle_alike(context, uploadAndConvert, arelle):
    arelle.gate.clear()
    first = context.new_page()
    uploadAndConvert(first)
    second = context.new_page()
    second.goto(first.url)
    for tab in (first, second):
        expect(tab.get_by_text(VALIDATING)).to_be_visible()

    arelle.gate.set()

    for tab in (first, second):
        expectSettled(tab)
        expect(tab.get_by_text(VALID)).to_be_visible()
    assert arelle.viewersBuilt == 1
