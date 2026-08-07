"""Tests for the partial-facts upload page.

When a report contains disclosure fields whose content must be supplied as Word
documents, the conversion flow diverts to ``/conversions/<id>/partial-facts``.
This page renders one file input per outstanding concept; submitting it stores
the uploaded ``.docx`` files as the conversion's ``external_values`` and returns
to the convert step. These tests cover that GET/POST behaviour.
"""

import io

CONVERSION_ID = "test-conversion"

CONCEPTS = [
    {"qname": "vsme:SomeConcept", "label": "Some disclosure"},
    {"qname": "vsme:OtherConcept", "label": "Other disclosure"},
]


def _seed(client, concepts):
    with client.session_transaction() as sess:
        sess[CONVERSION_ID] = {"partial_fact_concepts": concepts}


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
    with client.session_transaction() as sess:
        external = sess[CONVERSION_ID]["external_values"]
    assert set(external) == {c["qname"] for c in CONCEPTS}


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
    with client.session_transaction() as sess:
        assert "external_values" not in sess[CONVERSION_ID]
