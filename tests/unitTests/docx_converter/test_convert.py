"""convertDocxToHtml, in-process (the CLI) and in a child process with a time
limit (the web app)."""

import pytest
from tests.unitTests.word_documents import wordDocument

from mireport.docx_converter import DocxConversionError, convertDocxToHtml
from mireport.filesupport import FilelikeAndFileName

DOCUMENT = FilelikeAndFileName(
    wordDocument("Hello", paragraphStyle="Fancy"), "policy.docx"
)
NOT_A_DOCUMENT = FilelikeAndFileName(b"not a docx", "policy.docx")


def test_a_child_process_converts_as_in_process_does():
    inProcess = convertDocxToHtml(DOCUMENT, idPrefix="t-")
    inChild = convertDocxToHtml(DOCUMENT, idPrefix="t-", timeoutSeconds=30)
    assert inChild == inProcess
    assert "Hello" in inChild.html
    assert any("Fancy" in message for message in inChild.messages)


def test_a_child_process_that_takes_too_long_is_stopped():
    with pytest.raises(DocxConversionError, match="took longer than 0 seconds"):
        convertDocxToHtml(DOCUMENT, idPrefix="t-", timeoutSeconds=0)


@pytest.mark.parametrize("timeoutSeconds", [None, 30])
def test_a_file_that_is_not_a_word_document_is_an_error(timeoutSeconds):
    with pytest.raises(DocxConversionError, match="policy.docx could not be read"):
        convertDocxToHtml(NOT_A_DOCUMENT, idPrefix="t-", timeoutSeconds=timeoutSeconds)


@pytest.mark.parametrize("timeoutSeconds", [None, 30])
@pytest.mark.parametrize(
    ("altText", "expected"), [("", 'alt=""'), ("A chart", 'alt="A chart"')]
)
def test_every_image_has_an_alt_as_xhtml_requires(timeoutSeconds, altText, expected):
    document = FilelikeAndFileName(
        wordDocument("Hello", image=True, imageAltText=altText), "policy.docx"
    )
    html = convertDocxToHtml(
        document, idPrefix="t-", timeoutSeconds=timeoutSeconds
    ).html
    assert "<img " in html
    assert expected in html


@pytest.mark.parametrize("timeoutSeconds", [None, 30])
def test_struck_through_text_is_xhtml_strict(timeoutSeconds):
    document = FilelikeAndFileName(
        wordDocument("Withdrawn", strikethrough=True), "policy.docx"
    )
    html = convertDocxToHtml(
        document, idPrefix="t-", timeoutSeconds=timeoutSeconds
    ).html
    assert html == (
        '<p><span style="text-decoration:line-through">Withdrawn</span></p>'
    )


@pytest.mark.parametrize("timeoutSeconds", [None, 30])
def test_ids_start_with_the_prefix_so_two_documents_cannot_collide(timeoutSeconds):
    document = FilelikeAndFileName(
        wordDocument("Hello", bookmark="Target"), "policy.docx"
    )
    first, second = (
        convertDocxToHtml(document, idPrefix=prefix, timeoutSeconds=timeoutSeconds).html
        for prefix in ("Policy-", "Target-")
    )
    assert 'id="Policy-Target"' in first
    assert 'id="Target-Target"' in second


@pytest.mark.parametrize(("ticked", "box"), [(True, "☒"), (False, "☐")])
def test_a_word_checkbox_becomes_its_box_character(ticked, box):
    document = FilelikeAndFileName(
        wordDocument(" agreed", checkbox=ticked), "policy.docx"
    )
    assert convertDocxToHtml(document, idPrefix="t-").html == f"<p>{box} agreed</p>"
