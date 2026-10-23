"""GET /conversions/<id> and the conversion pipeline behind it
(``digital_converter_webapp.conversion_service``).

Fast tests cover the expired-session rendering. Slow tests run the full
pipeline: that the results page shows the right messages, and how supplementary
PDFs end up in the report package.

None of the PDF tests need pdf2htmlEX. The conversion call is monkeypatched,
which is also the point of several of them: the whole feature is designed to
degrade to "the PDF still ships, unconverted" on a deployment with no converter
installed, and that path has to be exercised on machines that are exactly that.
"""

import io
import zipfile

import pytest

from mireport.conversionresults import Severity, Stage
from mireport.pdf_converter import PdfConversionError, PdfToolNotFoundError


class TestConversionResultsPageExpired:
    """Fast: checks the expired-session rendering (no real conversion needed)."""

    def test_expired_conversion_is_404(self, client):
        resp = client.get("/conversions/no-such-id")
        assert resp.status_code == 404

    def test_expired_conversion_shows_expired_banner(self, client):
        resp = client.get("/conversions/no-such-id")
        assert b"Conversion expired" in resp.data

    def test_expired_conversion_shows_no_results_message(self, client):
        resp = client.get("/conversions/no-such-id")
        assert b"No conversion results available" in resp.data


@pytest.mark.slow
@pytest.mark.xdist_group("webapp-converted")
class TestConversionResultsPage:
    """Checks that /conversions/<id> renders the right messages after a real conversion."""

    def test_results_page_is_200(self, converted_id):
        conv_id, c = converted_id
        resp = c.get(f"/conversions/{conv_id}")
        assert resp.status_code == 200

    def test_results_page_has_conversion_results_heading(self, converted_id):
        conv_id, c = converted_id
        resp = c.get(f"/conversions/{conv_id}")
        assert b"Conversion Results" in resp.data

    def test_results_page_names_vs_and_vsme(self, converted_id):
        conv_id, c = converted_id
        resp = c.get(f"/conversions/{conv_id}")
        assert b"VS and VSME Digital Template to XBRL Converter" in resp.data

    def test_results_page_asks_for_feedback(self, converted_id):
        conv_id, c = converted_id
        body = c.get(f"/conversions/{conv_id}").get_data(as_text=True)
        assert "The EFRAG Secretariat is interested in your experience" in body
        assert (
            '<a href="mailto:vsme@efrag.org" class="underline font-medium'
            ' hover:text-blue-600">vsme@efrag.org</a> – Thank you!' in body
        )

    def test_results_page_feedback_goes_to_the_contact_email(
        self, app, converted_id, monkeypatch
    ):
        monkeypatch.setitem(app.config, "BRAND_CONTACT_EMAIL", "feedback@example.com")
        conv_id, c = converted_id
        body = c.get(f"/conversions/{conv_id}").get_data(as_text=True)
        assert ">feedback@example.com</a>" in body
        assert 'href="mailto:feedback@example.com"' in body
        assert "vsme@efrag.org" not in body

    def test_results_page_has_no_feedback_request_without_a_contact_email(
        self, app, converted_id, monkeypatch
    ):
        monkeypatch.setitem(app.config, "BRAND_CONTACT_EMAIL", "")
        conv_id, c = converted_id
        body = c.get(f"/conversions/{conv_id}").get_data(as_text=True)
        assert "Technical conversion" in body
        assert "interested in your experience" not in body
        assert "Feedback / Request Message Card" not in body

    def test_results_page_shows_success_banner(self, converted_id):
        conv_id, c = converted_id
        resp = c.get(f"/conversions/{conv_id}")
        assert b"Technical conversion" in resp.data
        assert b"successful" in resp.data

    def test_results_page_shows_messages_table(self, converted_id):
        conv_id, c = converted_id
        resp = c.get(f"/conversions/{conv_id}")
        assert b"Messages" in resp.data
        assert b"<table" in resp.data

    def test_results_page_shows_inline_xbrl_report_message(self, converted_id):
        # doConversion() always adds this message on success
        conv_id, c = converted_id
        resp = c.get(f"/conversions/{conv_id}")
        assert b"Inline XBRL report" in resp.data

    def test_results_page_shows_message_type_column(self, converted_id):
        conv_id, c = converted_id
        resp = c.get(f"/conversions/{conv_id}")
        assert b"Message Type" in resp.data

    def test_results_page_shows_severity_column(self, converted_id):
        conv_id, c = converted_id
        resp = c.get(f"/conversions/{conv_id}")
        assert b"Severity" in resp.data

    def test_results_page_shows_info_severity_badge(self, converted_id):
        conv_id, c = converted_id
        resp = c.get(f"/conversions/{conv_id}")
        assert Severity.INFO.value.encode() in resp.data

    def test_results_page_shows_files_to_download_section(self, converted_id):
        conv_id, c = converted_id
        resp = c.get(f"/conversions/{conv_id}")
        assert b"Files to download" in resp.data

    def test_results_page_shows_original_filename(self, converted_id):
        conv_id, c = converted_id
        resp = c.get(f"/conversions/{conv_id}")
        assert b"test.xlsx" in resp.data

    def test_results_page_dev_mode_shows_developer_messages(self, converted_id):
        conv_id, c = converted_id
        resp = c.get(f"/conversions/{conv_id}?show_developer_messages=true")
        assert resp.status_code == 200
        # Developer messages include Progress-type entries
        assert (
            b"DevInfo" in resp.data
            or b"Progress" in resp.data
            or b"Extracting" in resp.data
        )


def _convert(client, conversion_of):
    """Convert the client's one uploaded conversion and return it as the session
    holds it afterwards."""
    client.get(f"/conversions/{conversion_of(client).id}")
    return conversion_of(client)


def _packageNames(conv):
    assert conv.zip is not None
    with zipfile.ZipFile(io.BytesIO(conv.zip.fileContent)) as zf:
        return zf.namelist()


@pytest.mark.slow
class TestPdfConversion:
    """Marked slow: each runs a full Excel -> Inline XBRL conversion, Arelle
    stubbed out as only the package and messages are looked at."""

    @pytest.mark.xdist_group("webapp-converted-pdf")
    def test_pdf_becomes_a_docset_member_and_an_attachment(
        self, convertedPdfPackage
    ) -> None:
        names = convertedPdfPackage
        assert any(n.endswith("/annex1.xhtml") for n in names), names
        assert any(n.endswith("/attachments/annex1.pdf") for n in names), names

    @pytest.mark.xdist_group("webapp-converted-pdf")
    def test_report_moves_into_the_docset_directory(self, convertedPdfPackage) -> None:
        names = convertedPdfPackage
        directlyInReports = [n for n in names if "/reports/" in n and n.count("/") == 2]
        assert directlyInReports == [], names

    @pytest.mark.usefixtures("skipArelle")
    def test_no_pdfs_leaves_the_package_layout_alone(
        self, client, upload, conversion_of
    ) -> None:
        upload(client)
        names = _packageNames(_convert(client, conversion_of))
        assert not any("/attachments/" in n for n in names)
        assert any(n.count("/") == 2 and "/reports/" in n for n in names), names

    @pytest.mark.parametrize(
        "error",
        [PdfToolNotFoundError("no binary"), PdfConversionError("bad pdf")],
        ids=["no-binary", "bad-pdf"],
    )
    @pytest.mark.usefixtures("skipArelle")
    def test_conversion_failure_is_a_warning_not_an_error(
        self, client, upload, make_pdf, conversion_of, monkeypatch, error
    ) -> None:
        """A PDF that will not convert must not sink an otherwise-good report."""
        from mireport.pdf_converter import (
            BACKENDS_IN_PREFERENCE_ORDER,
            ResolvedConverter,
            _batch,
        )

        def boom(*args, **kwargs):
            raise error

        monkeypatch.setattr(_batch, "convertPdfToHtml", boom)
        monkeypatch.setattr(
            _batch,
            "findConverter",
            lambda: ResolvedConverter(BACKENDS_IN_PREFERENCE_ORDER[0], "/usr/bin/x"),
        )
        upload(client, pdfs=[make_pdf("annex1.pdf")])
        results = _convert(client, conversion_of).results
        assert results.conversionSuccessful
        # Scoped to the PDFs' section: the sample template raises its own
        # unrelated warnings, so a bare count would pass for the wrong reason.
        [pdfs] = [s for s in results.sections if s.stage is Stage.PDF]
        pdfWarnings = [m for m in pdfs.messages if m.severity is Severity.WARNING]
        assert [m.source for m in pdfWarnings] == ["annex1.pdf"]

    @pytest.mark.xdist_group("webapp-unconverted-pdf")
    def test_the_pdf_still_ships_when_conversion_fails(
        self, unconvertedPdfPackage
    ) -> None:
        """The whole point of attaching the original as well."""
        names = unconvertedPdfPackage
        assert any(n.endswith("/attachments/annex1.pdf") for n in names), names
        assert not any(n.endswith(".xhtml") for n in names), names

    @pytest.mark.xdist_group("webapp-unconverted-pdf")
    def test_report_layout_unchanged_when_conversion_fails(
        self, unconvertedPdfPackage
    ) -> None:
        """No document set members means the report stays where it always was."""
        names = unconvertedPdfPackage
        assert any(n.count("/") == 2 and "/reports/" in n for n in names), names

    @pytest.mark.usefixtures("skipArelle")
    def test_all_pdfs_still_attached_without_a_binary(
        self, client, upload, make_pdf, conversion_of, noBinary
    ) -> None:
        upload(client, pdfs=[make_pdf("a.pdf"), make_pdf("b.pdf"), make_pdf("c.pdf")])
        names = _packageNames(_convert(client, conversion_of))
        assert sum("/attachments/" in n for n in names) == 3, names
