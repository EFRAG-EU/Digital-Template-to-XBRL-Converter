"""The results page of a real conversion, Arelle validation included, as a user
sees it: the status banners, the downloads and the developer messages toggle.

Assertions wait for the page's final state rather than assuming the first
response carries it, so they hold however the work behind the page is
scheduled.
"""

import re

import pytest
from playwright.sync_api import Page, expect

pytestmark = pytest.mark.slow

# The viewer is generated on its first download, another Arelle run.
DOWNLOAD_TIMEOUT_MS = 120_000


def test_banners_show_successful_conversion_and_validation(convertedPage: Page):
    expect(
        convertedPage.get_by_text(
            "Technical conversion of the Digital Template to XBRL was successful!"
        )
    ).to_be_visible()
    expect(
        convertedPage.get_by_text(
            "The generated XBRL report is valid according to the technical XBRL"
            " specifications!"
        )
    ).to_be_visible()
    expect(convertedPage.get_by_text("Files to download")).to_be_visible()


@pytest.mark.parametrize(
    ("button", "suffix"),
    [
        ("Download XBRL Report", ".zip"),
        ("Download xBRL-JSON", ".json"),
        ("Download viewer", ".html"),
    ],
)
def test_download(convertedPage: Page, button: str, suffix: str):
    with convertedPage.expect_download(timeout=DOWNLOAD_TIMEOUT_MS) as info:
        convertedPage.get_by_role("link", name=button).click()
    download = info.value
    assert download.suggested_filename.endswith(suffix)
    assert download.failure() is None
    expect(convertedPage.locator("#loadingSpinner")).to_be_hidden()


def test_developer_messages_toggle(convertedPage: Page):
    toggle = convertedPage.get_by_label("Display software developer messages")
    progress = convertedPage.get_by_role("cell", name="Progress").first

    expect(progress).to_be_hidden()
    toggle.check()
    convertedPage.wait_for_url(re.compile(r"show_developer_messages=true"))
    expect(progress).to_be_visible()
    expect(toggle).to_be_checked()

    toggle.uncheck()
    convertedPage.wait_for_url(re.compile(r"/conversions/[^?]+$"))
    expect(progress).to_be_hidden()
