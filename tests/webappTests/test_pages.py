"""GET-only route smoke tests and branding — no conversion required."""

import json
import re

from cachelib.file import FileSystemCache

import mireport
from digital_converter_webapp import create_app
from digital_converter_webapp.conversion import ConversionStore
from mireport.filesupport import FilelikeAndFileName


class TestHomePage:
    def test_home_loads(self, client):
        resp = client.get("/")
        assert resp.status_code == 200

    def test_home_contains_upload_form(self, client):
        resp = client.get("/")
        assert b"<form" in resp.data

    def test_home_contains_file_input(self, client):
        resp = client.get("/")
        assert b'type="file"' in resp.data


class TestLocales:
    def test_locales_json_loads(self, client):
        url = f"/locales/available_{mireport.__version__}.json"
        resp = client.get(url)
        assert resp.status_code == 200

    def test_locales_json_is_list(self, client):
        url = f"/locales/available_{mireport.__version__}.json"
        resp = client.get(url)
        body = json.loads(resp.data)
        assert isinstance(body, list)
        assert len(body) > 0

    def test_locales_json_entries_have_label(self, client):
        url = f"/locales/available_{mireport.__version__}.json"
        resp = client.get(url)
        body = json.loads(resp.data)
        for entry in body:
            assert "label" in entry


class TestDebugSession:
    def test_not_found_when_debug_off(self, client):
        resp = client.get("/debug_session")
        assert resp.status_code == 404

    def test_available_when_debug_on(self, app, client, monkeypatch):
        monkeypatch.setitem(app.config, "DEBUG", True)
        resp = client.get("/debug_session")
        assert resp.status_code == 200
        assert resp.is_json


PRODUCT_NAME = "VS and VSME Digital Template to XBRL Converter"


def _brandedApp(tmp_path, **config):
    return create_app(
        {
            "TESTING": True,
            "SESSION_CACHELIB": FileSystemCache(str(tmp_path / "sessions")),
            "SECRET_KEY": "test-secret",
            **config,
        }
    )


def _title(body: str) -> str:
    match = re.search(r"<title>(.*?)</title>", body, re.DOTALL)
    assert match is not None
    return match.group(1)


class TestBranding:
    def test_home_shows_efrag_branding_by_default(self, client):
        body = client.get("/").get_data(as_text=True)
        assert (
            '<link rel="icon" href="https://www.efrag.org/themes/custom/dms_theme/'
            'favicon.ico" type="image/x-icon" />' in body
        )
        assert (
            '<img src="https://www.efrag.org/themes/custom/dms_theme/logo.svg"'
            ' alt="EFRAG Logo" class="h-12">' in body
        )
        assert f">{PRODUCT_NAME}</h1>" in body
        assert (
            'href="https://xbrl.efrag.org/downloads/vsme/'
            'VSME-Digital-Template-latest.xlsx"' in body
        )
        assert (
            'href="https://xbrl.efrag.org/downloads/vsme/'
            'VSME-Digital-Template-sample-latest.xlsx"' in body
        )
        assert "provided by EFRAG (or test the" in body
        assert "convert your Digital Template" in body

    def test_configured_product_name_and_no_logo(self, tmp_path):
        app = _brandedApp(
            tmp_path, BRAND_PRODUCT_NAME="Acme Converter", BRAND_LOGO_URL=""
        )
        body = app.test_client().get("/").get_data(as_text=True)
        assert ">Acme Converter</h1>" in body
        assert _title(body) == "Acme Converter"
        assert PRODUCT_NAME not in body
        assert 'class="h-12"' not in body
        assert "logo.svg" not in body

    def test_empty_favicon_has_no_icon_link(self, tmp_path):
        app = _brandedApp(tmp_path, BRAND_FAVICON_URL="")
        body = app.test_client().get("/").get_data(as_text=True)
        assert 'rel="icon"' not in body

    def test_default_footer_and_terms_modal(self, client):
        body = client.get("/").get_data(as_text=True)
        footer = body.split("<footer", 1)[1].split("</footer>", 1)[0]
        for text in (
            "are provided by EFRAG as guidance material",
            "implementation of the VSME and VS sustainability reporting standards",
            "More information about digital VS and VSME reporting",
            (
                '<button type="button" onclick="openModal()" class="text-blue-600'
                ' underline">terms and conditions</button>'
            ),
            'href="https://github.com/EFRAG-EU/Digital-Template-to-XBRL-Converter"',
            (
                'href="https://github.com/EFRAG-EU/Digital-Template-to-XBRL-Converter/'
                'issues"'
            ),
            "Become a Friend of EFRAG!",
            "EFRAG is co-funded by the European Union",
            "Copyright © EFRAG 2025",
            'href="mailto:digital-reporting@efrag.org"',
            "VSME Digital Template & Taxonomy",
            "Deployed since",
            f"<dd>{mireport.__version__}</dd>",
        ):
            assert text in footer
        modal = body.split('<div id="termsModal"', 1)[1].split('id="errorModal"')[0]
        for text in (
            '<h2 class="text-lg font-bold text-gray-800 mb-4">Terms and Conditions',
            "implementation of the VSME and VS sustainability reporting standards",
            "EFRAG is providing the web-converter for personal and non-commercial use",
            (
                'href="https://www.efrag.org/en/about-us/legal-policies">EFRAG\'s privacy'
                " policy</a>"
            ),
            "Please note that EFRAG does not offer direct support to end users.",
            '<button onclick="closeModal()"',
        ):
            assert text in modal

    def test_brand_template_dir_replaces_efrag_content(self, tmp_path):
        brandDir = tmp_path / "brand"
        brandDir.mkdir()
        (brandDir / "_brand-content.html.jinja").write_text(
            "{% macro footer() %}<p>Acme footer</p>{% endmacro %}\n"
            '{% macro termsModal() %}<div id="termsModal">Acme terms</div>'
            "{% endmacro %}\n"
            "{% macro feedbackRequest() %}<p>Acme feedback</p>{% endmacro %}\n",
            encoding="utf-8",
        )
        app = _brandedApp(tmp_path, BRAND_TEMPLATE_DIR=str(brandDir))
        body = app.test_client().get("/").get_data(as_text=True)
        assert "<p>Acme footer</p>" in body
        assert '<div id="termsModal">Acme terms</div>' in body
        assert "Friend of EFRAG" not in body
        assert "EFRAG is providing the web-converter" not in body
        # The page's own templates are still found behind it
        assert "Deployed since" in body
        with app.app_context():
            # The results page imports the same file as base does
            content = app.jinja_env.get_template("_brand-content.html.jinja")
            assert str(content.module.feedbackRequest()) == "<p>Acme feedback</p>"


class TestTitles:
    def test_upload_page_is_the_product_name(self, client):
        assert _title(client.get("/").get_data(as_text=True)) == PRODUCT_NAME

    def test_conversions_list(self, client):
        body = client.get("/conversions/").get_data(as_text=True)
        assert _title(body) == f"Conversions — {PRODUCT_NAME}"

    def test_results_page(self, client):
        body = client.get("/conversions/no-such-id").get_data(as_text=True)
        assert _title(body) == f"Conversion Results — {PRODUCT_NAME}"

    def test_partial_facts_page(self, client):
        with client.session_transaction() as sess:
            ConversionStore(sess).create("titles").partial_fact_concepts = [
                {"qname": "vsme:SomeConcept", "label": "Some disclosure"}
            ]
        body = client.get("/conversions/titles/partial-facts").get_data(as_text=True)
        assert _title(body) == f"Additional Documents Required — {PRODUCT_NAME}"

    def test_migration_page(self, client):
        with client.session_transaction() as sess:
            ConversionStore(sess).create("titles").excel = FilelikeAndFileName(
                fileContent=b"old", filename="report.xlsx"
            )
        body = client.get("/migrationPage/titles").get_data(as_text=True)
        assert _title(body) == f"Template Migration — {PRODUCT_NAME}"

    def test_configured_product_name_in_page_titles(self, tmp_path):
        app = _brandedApp(tmp_path, BRAND_PRODUCT_NAME="Acme Converter")
        body = app.test_client().get("/conversions/").get_data(as_text=True)
        assert _title(body) == "Conversions — Acme Converter"
