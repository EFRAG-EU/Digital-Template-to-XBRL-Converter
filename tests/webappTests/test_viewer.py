"""The Inline XBRL viewer routes and the document set members they serve
(``digital_converter_webapp.viewer`` plus ``ensureViewer`` in the conversion
service).

A document set's viewer is served rather than downloaded: its members are
fetched by name relative to the viewer's own URL, so a single-file download of
the primary alone would be useless.
"""

import pytest

from digital_converter_webapp.conversion import ConversionStore
from mireport.filesupport import FilelikeAndFileName

DOCSET_ID = "docset-conversion"
MEMBER_NAME = "annex1.xhtml"


def _seed_viewer(client, *, members):
    """Seed a conversion whose viewer has already been generated.

    ``ensureViewer()`` only calls Arelle when there is no viewer yet, so seeding
    the report package, the viewer and its members keeps these tests fast.
    """
    with client.session_transaction() as sess:
        conv = ConversionStore(sess).create(DOCSET_ID)
        conv.zip = FilelikeAndFileName(fileContent=b"zip-bytes", filename="report.zip")
        conv.viewer = FilelikeAndFileName(
            fileContent=b"<html>viewer</html>", filename="report_viewer.html"
        )
        conv.viewer_members = {
            name: FilelikeAndFileName(fileContent=content, filename=name)
            for name, content in members.items()
        }


class TestDocumentSetViewer:
    def test_download_of_document_set_viewer_redirects_to_viewer(self, client):
        _seed_viewer(client, members={MEMBER_NAME: b"<html>annex</html>"})
        resp = client.get(f"/downloadFile/{DOCSET_ID}/viewer/")
        assert resp.status_code == 303
        assert resp.location.endswith(f"/viewer/{DOCSET_ID}/")

    def test_head_of_document_set_viewer_still_reports_ready(self, client):
        _seed_viewer(client, members={MEMBER_NAME: b"<html>annex</html>"})
        resp = client.head(f"/downloadFile/{DOCSET_ID}/viewer/")
        assert resp.status_code == 200
        assert resp.headers["X-File-Ready"] == "true"

    def test_single_document_viewer_is_still_downloaded(self, client):
        _seed_viewer(client, members={})
        resp = client.get(f"/downloadFile/{DOCSET_ID}/viewer/")
        assert resp.status_code == 200
        assert "attachment" in resp.headers["Content-Disposition"]

    def test_member_is_served_beside_the_viewer(self, client):
        _seed_viewer(client, members={MEMBER_NAME: b"<html>annex</html>"})
        resp = client.get(f"/viewer/{DOCSET_ID}/{MEMBER_NAME}")
        assert resp.status_code == 200
        assert resp.data == b"<html>annex</html>"
        assert "attachment" not in resp.headers.get("Content-Disposition", "")

    def test_unknown_member_is_404(self, client):
        _seed_viewer(client, members={MEMBER_NAME: b"<html>annex</html>"})
        resp = client.get(f"/viewer/{DOCSET_ID}/nope.xhtml")
        assert resp.status_code == 404

    def test_member_of_unknown_conversion_is_404(self, client):
        resp = client.get(f"/viewer/no-such-id/{MEMBER_NAME}")
        assert resp.status_code == 404

    def test_viewer_of_unknown_conversion_is_404(self, client):
        resp = client.get("/viewer/no-such-id/")
        assert resp.status_code == 404

    def test_head_of_member_reports_ready(self, client):
        _seed_viewer(client, members={MEMBER_NAME: b"<html>annex</html>"})
        resp = client.head(f"/viewer/{DOCSET_ID}/{MEMBER_NAME}")
        assert resp.status_code == 200
        assert resp.headers["X-File-Ready"] == "true"

    def test_member_is_served_with_script_forbidden(self, client):
        """A member is content from an uploaded PDF, served same-origin.
        mireport strips script out of it; this is the second line of that."""
        _seed_viewer(client, members={MEMBER_NAME: b"<html>annex</html>"})
        resp = client.get(f"/viewer/{DOCSET_ID}/{MEMBER_NAME}")
        policy = resp.headers["Content-Security-Policy"]
        assert "script-src 'none'" in policy
        assert "object-src 'none'" in policy
        assert resp.headers["X-Content-Type-Options"] == "nosniff"

    def test_the_viewer_itself_is_not_restricted(self, client):
        """It is script: the iXBRL viewer is the whole point of the page."""
        _seed_viewer(client, members={MEMBER_NAME: b"<html>annex</html>"})
        resp = client.get(f"/viewer/{DOCSET_ID}/")
        assert "Content-Security-Policy" not in resp.headers


@pytest.mark.slow
@pytest.mark.xdist_group("webapp-converted")
class TestViewerAfterConversion:
    def test_viewer_page_returns_html(self, converted_id):
        conv_id, c = converted_id
        resp = c.get(f"/viewer/{conv_id}/")
        assert resp.status_code == 200
        assert b"html" in resp.data.lower()


@pytest.mark.slow
@pytest.mark.xdist_group("webapp-viewed-docset")
class TestDocumentSetViewerAfterConversion:
    """Against a real conversion of a workbook with a supplementary PDF."""

    def test_viewer_has_document_set_members(
        self, viewed_docset, conversion_of
    ) -> None:
        client, _ = viewed_docset
        assert list(conversion_of(client).viewer_members) == ["annex1.xhtml"]

    def test_download_redirects_to_the_served_viewer(self, viewed_docset) -> None:
        client, conversionId = viewed_docset
        resp = client.get(f"/downloadFile/{conversionId}/viewer/")
        assert resp.status_code == 303
        assert resp.location.endswith(f"/viewer/{conversionId}/")

    def test_members_are_served_where_the_viewer_looks_for_them(
        self, viewed_docset, conversion_of
    ) -> None:
        client, conversionId = viewed_docset
        for name in conversion_of(client).viewer_members:
            resp = client.get(f"/viewer/{conversionId}/{name}")
            assert resp.status_code == 200, name
            assert resp.data

    def test_primary_viewer_names_its_members(self, viewed_docset) -> None:
        client, conversionId = viewed_docset
        resp = client.get(f"/viewer/{conversionId}/")
        assert resp.status_code == 200
        assert b"annex1.xhtml" in resp.data
