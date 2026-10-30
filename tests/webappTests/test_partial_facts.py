"""Tests for the partial-facts upload page.

When a report contains disclosure fields whose content must be supplied as Word
documents, the conversion flow diverts to ``/conversions/<id>/partial-facts``.
This page renders one file input per outstanding concept; submitting it stores
the uploaded ``.docx`` files as the conversion's ``external_values`` and returns
to the convert step. These tests cover that GET/POST behaviour, and the slow
one the whole flow, from upload to report.
"""

import io
import zipfile

import pytest
from tests.unitTests.word_documents import (
    EXTERNAL_CONCEPT,
    externalValuesWorkbook,
    wordDocument,
)

from digital_converter_webapp.conversion import ConversionStore
from mireport.conversionresults import MessageType, Severity, Stage

CONVERSION_ID = "test-conversion"

CONCEPTS = [
    {"qname": "vsme:SomeConcept", "label": "Some disclosure"},
    {"qname": "vsme:OtherConcept", "label": "Other disclosure"},
]


def _seed(client, concepts):
    with client.session_transaction() as sess:
        ConversionStore(sess).create(CONVERSION_ID).partial_fact_concepts = concepts


def _stored(client):
    with client.session_transaction() as sess:
        return ConversionStore(sess).get(CONVERSION_ID)


def _docx(content=b"docx-bytes", filename="doc.docx"):
    return (io.BytesIO(content), filename)


def test_get_renders_a_file_input_per_concept(client):
    _seed(client, CONCEPTS)
    resp = client.get(f"/conversions/{CONVERSION_ID}/partial-facts")
    assert resp.status_code == 200
    body = resp.data.decode()
    assert "<form" in body
    for concept in CONCEPTS:
        assert concept["label"] in body
        assert f'name="docx_{concept["qname"]}"' in body
    assert body.count('type="file"') == len(CONCEPTS)
    assert "required" in body


def test_get_uses_shared_spinner(client):
    _seed(client, CONCEPTS)
    body = client.get(f"/conversions/{CONVERSION_ID}/partial-facts").data.decode()
    # The page reuses base's single spinner via the shared helper rather than
    # shipping its own markup/script.
    assert body.count('id="loadingSpinner"') == 1
    assert "showSpinner(" in body


def test_get_with_no_concepts_redirects_to_convert(client):
    _seed(client, [])
    resp = client.get(f"/conversions/{CONVERSION_ID}/partial-facts")
    assert resp.status_code == 303
    assert resp.location.endswith(f"/conversions/{CONVERSION_ID}")


def test_get_unknown_conversion_redirects_to_index(client):
    resp = client.get("/conversions/does-not-exist/partial-facts")
    assert resp.status_code == 302
    assert resp.location.endswith("/")


def test_post_stores_uploads_and_redirects_to_convert(client):
    _seed(client, CONCEPTS)
    data = {f"docx_{c['qname']}": _docx() for c in CONCEPTS}
    resp = client.post(
        f"/conversions/{CONVERSION_ID}/partial-facts",
        data=data,
        content_type="multipart/form-data",
    )
    assert resp.status_code == 303
    assert resp.location.endswith(f"/conversions/{CONVERSION_ID}")
    assert set(_stored(client).external_values) == {c["qname"] for c in CONCEPTS}


def test_post_missing_file_flashes_error_and_redirects_back(client):
    _seed(client, CONCEPTS)
    data = {f"docx_{CONCEPTS[0]['qname']}": _docx()}  # second concept omitted
    resp = client.post(
        f"/conversions/{CONVERSION_ID}/partial-facts",
        data=data,
        content_type="multipart/form-data",
    )
    assert resp.status_code == 303
    assert resp.location.endswith(f"/conversions/{CONVERSION_ID}/partial-facts")
    assert not _stored(client).has_external_values


@pytest.mark.slow
@pytest.mark.usefixtures("skipArelle")
def test_word_document_fills_the_text_block_and_its_warnings_are_shown(
    client, upload, conversion_of
):
    upload(client, data=externalValuesWorkbook())
    url = f"/conversions/{conversion_of(client).id}"
    assert client.get(url).location.endswith(f"{url}/partial-facts")

    document = wordDocument(
        "Supplied in Word.", paragraphStyle="Fancy", breakType="bogus"
    )
    client.post(
        f"{url}/partial-facts",
        data={f"docx_{EXTERNAL_CONCEPT}": (io.BytesIO(document), "policy.docx")},
        content_type="multipart/form-data",
    )
    client.get(url)

    conv = conversion_of(client)
    results = conv.results
    assert results.conversionSuccessful
    [word] = [s for s in results.sections if s.stage is Stage.WORD]
    assert {m.source for m in word.messages} == {"policy.docx"}
    (warning,) = [
        m.messageText for m in word.messages if m.severity is Severity.WARNING
    ]
    assert "Unsupported break type: bogus" in warning
    # A style mammoth doesn't know loses only formatting: developers only.
    assert any(
        m.messageType is MessageType.DevInfo and "Fancy" in m.messageText
        for m in word.messages
    )
    assert "Fancy" not in warning
    with zipfile.ZipFile(io.BytesIO(conv.zip.fileContent)) as package:
        assert any(
            b"Supplied in Word." in package.read(name)
            for name in package.namelist()
            if name.endswith(".html")
        )


@pytest.mark.slow
@pytest.mark.usefixtures("skipArelle")
def test_a_file_that_is_not_a_word_document_fails_the_conversion(
    client, upload, conversion_of
):
    upload(client, data=externalValuesWorkbook())
    url = f"/conversions/{conversion_of(client).id}"
    client.get(url)
    client.post(
        f"{url}/partial-facts",
        data={f"docx_{EXTERNAL_CONCEPT}": (io.BytesIO(b"not a docx"), "policy.docx")},
        content_type="multipart/form-data",
    )
    client.get(url)

    results = conversion_of(client).results
    assert not results.conversionSuccessful
    [word] = [s for s in results.sections if s.stage is Stage.WORD]
    errors = [
        (m.source, m.messageText) for m in word.messages if m.severity is Severity.ERROR
    ]
    assert errors == [("policy.docx", "Could not be read.")]
    assert word.messages == results.getMessages(
        wantedMessageSeverities={Severity.ERROR}
    )


@pytest.mark.slow
def test_html_mammoth_writes_outside_xhtml_strict_still_validates(
    client, upload, conversion_of
):
    """mammoth leaves alt off an image Word has no alt text for, and writes
    struck-through text as <s>; both are XHTML schema errors in the report
    unless normalised. Validated by Arelle for real, as a filing would be."""
    upload(client, data=externalValuesWorkbook())
    url = f"/conversions/{conversion_of(client).id}"
    client.get(url)
    document = wordDocument("Withdrawn.", strikethrough=True, image=True)
    client.post(
        f"{url}/partial-facts",
        data={f"docx_{EXTERNAL_CONCEPT}": (io.BytesIO(document), "policy.docx")},
        content_type="multipart/form-data",
    )
    client.get(url)

    results = conversion_of(client).results
    assert results.getMessages(wantedMessageSeverities={Severity.ERROR}) == []
    assert results.conversionSuccessful
