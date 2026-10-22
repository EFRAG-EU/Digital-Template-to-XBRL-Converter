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
    inProcess = convertDocxToHtml(DOCUMENT)
    inChild = convertDocxToHtml(DOCUMENT, timeoutSeconds=30)
    assert inChild == inProcess
    assert "Hello" in inChild.html
    assert any("Fancy" in message for message in inChild.messages)


def test_a_child_process_that_takes_too_long_is_stopped():
    with pytest.raises(DocxConversionError, match="took longer than 0 seconds"):
        convertDocxToHtml(DOCUMENT, timeoutSeconds=0)


@pytest.mark.parametrize("timeoutSeconds", [None, 30])
def test_a_file_that_is_not_a_word_document_is_an_error(timeoutSeconds):
    with pytest.raises(DocxConversionError, match="policy.docx could not be read"):
        convertDocxToHtml(NOT_A_DOCUMENT, timeoutSeconds=timeoutSeconds)


@pytest.mark.parametrize("timeoutSeconds", [None, 30])
@pytest.mark.parametrize(
    ("altText", "expected"), [("", 'alt=""'), ("A chart", 'alt="A chart"')]
)
def test_every_image_has_an_alt_as_xhtml_requires(timeoutSeconds, altText, expected):
    document = FilelikeAndFileName(
        wordDocument("Hello", image=True, imageAltText=altText), "policy.docx"
    )
    html = convertDocxToHtml(document, timeoutSeconds=timeoutSeconds).html
    assert "<img " in html
    assert expected in html
