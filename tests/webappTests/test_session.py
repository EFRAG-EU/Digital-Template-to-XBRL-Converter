"""What the server-side session backend does to a conversion between requests."""

from digital_converter_webapp.conversion import ConversionStore
from mireport.filesupport import FilelikeAndFileName


class TestSessionRoundTrip:
    def test_pdfs_survive_the_session_backend(
        self, client, upload, make_pdf, conversion_of, pdf_bytes
    ):
        """The server-side session serialises NamedTuples as lists, so a list of
        FilelikeAndFileName comes back as a list of lists. Reading it without
        from_tuple would work under a dict session and fail in production."""
        upload(client, pdfs=[make_pdf("a.pdf"), make_pdf("b.pdf")])
        conv = conversion_of(client)

        # What the backend actually holds is plain lists...
        assert all(isinstance(p, list | tuple) for p in conv.raw["pdfs"])
        # ...and the model hands back real FilelikeAndFileNames.
        recovered = conv.pdfs
        assert all(isinstance(p, FilelikeAndFileName) for p in recovered)
        assert [p.filename for p in recovered] == ["a.pdf", "b.pdf"]
        assert all(p.fileContent == pdf_bytes for p in recovered)

    def test_internal_session_keys_are_not_conversions(self, client, upload_id):
        conv_id = upload_id(client)
        with client.session_transaction() as sess:
            store = ConversionStore(sess)
            assert "csrf_token" in sess
            assert "csrf_token" not in store
            assert list(store) == [conv_id]
