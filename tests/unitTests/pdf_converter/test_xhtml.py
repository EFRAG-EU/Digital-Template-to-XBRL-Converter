"""Tests for normalising a converter's output into a document set member.

Both supported converters emit something the iXBRL specification will not accept
as-is. pdf2htmlEX emits HTML5; pdftohtml emits XHTML Transitional. Every member
of an Inline XBRL Document Set is a well-formed XML document valid against the
XHTML + iXBRL schema (iXBRL 1.1, 3.1 and 3.3.1), so the output must become
well-formed XHTML *Strict*, and must carry at least one ``ix:`` element — the
specification defines an Inline XBRL Document as one containing Inline XBRL
Elements, so a namespace declaration alone does not make a document a member.
Arelle, the processor this pipeline runs, says so with an
``arelle:nonIxdsDocument`` warning and drops the member from the set.

Every rule asserted here came from a real validation error against real converter
output, reproduced in tests/integrationTests/test_real_pdf_converter.py. They are
asserted here as well because that run needs a converter installed, takes
minutes, and reports failures far from their cause.
"""

from __future__ import annotations

import pytest
from lxml import etree

from mireport.pdf_converter import PdfConversionError
from mireport.pdf_converter._xhtml import normaliseToXhtml

XHTML = "http://www.w3.org/1999/xhtml"
IX = "http://www.xbrl.org/2013/inlineXBRL"

MALFORMED = b"""<!DOCTYPE html>
<html><head><meta charset=utf-8>
<style type="text/css">.pf{position:relative}</style>
</head>
<body>
<div id="page-container">
<div class="pf" data-page-no=1>
<div class="t">Converted annex &amp; appendix</div>
<img alt="" src="data:image/png;base64,iVBORw0KGgo=">
<br>
<hr>
<p>Unclosed paragraph
</div>
</div>
</body>
</html>
"""


@pytest.fixture
def normalised() -> bytes:
    return normaliseToXhtml(MALFORMED)


@pytest.fixture
def root(normalised: bytes) -> etree._Element:
    return etree.fromstring(normalised)


# ── well-formedness ───────────────────────────────────────────────────────────


class TestWellFormed:
    def test_parses_with_a_strict_xml_parser(self, normalised: bytes) -> None:
        etree.fromstring(normalised, etree.XMLParser())

    def test_root_is_html_in_the_xhtml_namespace(self, root: etree._Element) -> None:
        assert root.tag == f"{{{XHTML}}}html"

    def test_every_element_is_namespaced(self, root: etree._Element) -> None:
        """A bare element would put the document outside XHTML."""
        for element in root.iter():
            if isinstance(element.tag, str):
                assert element.tag.startswith("{"), element.tag

    @pytest.mark.parametrize(
        "fragment",
        [
            b"<html><body><br></body></html>",
            b"<html><body><p>one<p>two</body></html>",
            b"<html><body><div class=unquoted>x</div></body></html>",
            b"<html><body><img src=a.png alt=b></body></html>",
            b"<html><body><table><tr><td>x</table></body></html>",
            b"<html><body><ul><li>a<li>b</ul></body></html>",
        ],
        ids=["void", "implied-close", "unquoted", "img", "table", "list"],
    )
    def test_html5_shapes_become_well_formed(self, fragment: bytes) -> None:
        etree.fromstring(normaliseToXhtml(fragment), etree.XMLParser())

    def test_normalising_its_own_output_changes_nothing(
        self, normalised: bytes
    ) -> None:
        """The only test that drives the already-XHTML parse path, and so the
        only one where the input arrives with a real ``ix:header`` rather than a
        literally named one — no second header, and nothing the strict element
        check refuses."""
        assert normaliseToXhtml(normalised) == normalised


# ── document set membership ───────────────────────────────────────────────────


class TestInlineXbrlMembership:
    def test_carries_an_ix_header(self, root: etree._Element) -> None:
        assert root.findall(f".//{{{IX}}}header")

    def test_header_is_hidden(self, root: etree._Element) -> None:
        """iXBRL 1.1 recommends the header sit in a display:none container, and
        the annex must not sprout a blank band where the header is."""
        header = root.find(f".//{{{IX}}}header")
        assert header is not None
        parent = header.getparent()
        assert parent is not None
        assert "display:none" in (parent.get("style") or "").replace(" ", "")

    def test_declares_no_facts(self, root: etree._Element) -> None:
        """The annex contributes content, never XBRL data."""
        for tag in ("nonNumeric", "nonFraction", "fraction", "tuple"):
            assert not root.findall(f".//{{{IX}}}{tag}")

    def test_declares_no_schema_references(self, root: etree._Element) -> None:
        """DTS discovery stays entirely the tagged report's business."""
        assert not root.findall(f".//{{{IX}}}references")

    def test_added_only_once(self) -> None:
        """Normalising already-normalised output must be a no-op, not a second
        header — and must not crash on the xmlns attributes the HTML parser
        would otherwise choke on."""
        once = normaliseToXhtml(MALFORMED)
        assert normaliseToXhtml(once).count(b"<ix:header") == 1

    def test_existing_ix_content_is_left_alone(self) -> None:
        """A converter that already emitted ix: content keeps its own header."""
        source = (
            b'<?xml version="1.0"?>'
            b'<html xmlns="http://www.w3.org/1999/xhtml"'
            b' xmlns:ix="http://www.xbrl.org/2013/inlineXBRL">'
            b'<body><div style="display:none"><ix:header id="theirs"/></div></body>'
            b"</html>"
        )
        root = etree.fromstring(normaliseToXhtml(source))
        headers = root.findall(f".//{{{IX}}}header")
        assert len(headers) == 1
        assert headers[0].get("id") == "theirs"


# ── XHTML schema conformance ──────────────────────────────────────────────────


class TestSchemaConformance:
    """Every inline document is schema-validated against the iXBRL 1.1 XHTML
    schema, document set members included. Well-formed is not sufficient — these
    are the HTML5-isms pdf2htmlEX emits that the schema rejects outright.
    Verified end to end in tests/integrationTests/test_docset_package.py;
    asserted here because that
    run takes minutes and reports the errors far from their cause."""

    def test_data_attributes_are_stripped(self) -> None:
        source = b'<html><body><div class="pf" data-page-no=1 data-x="y">p</div></body></html>'
        root = etree.fromstring(normaliseToXhtml(source))
        assert not [
            name
            for element in root.iter()
            if isinstance(element.tag, str)
            for name in element.attrib
            if name.startswith("data-")
        ]

    def test_only_the_data_attributes_go(self) -> None:
        source = (
            b'<html><body><div class="pf" id="p1" data-page-no=1>p</div></body></html>'
        )
        root = etree.fromstring(normaliseToXhtml(source))
        div = root.find(f".//{{{XHTML}}}div[@id='p1']")
        assert div is not None
        assert div.get("class") == "pf"

    def test_meta_charset_is_dropped_entirely(self) -> None:
        """Stripping the attribute would leave a bare <meta/>, and the schema
        requires content on every meta. The encoding lives in the XML
        declaration regardless."""
        source = b"<html><head><meta charset=utf-8></head><body>x</body></html>"
        root = etree.fromstring(normaliseToXhtml(source))
        assert root.findall(f".//{{{XHTML}}}meta") == []

    def test_meta_with_content_is_kept(self) -> None:
        source = (
            b'<html><head><meta http-equiv="Content-Type" content="text/html"></head>'
            b"<body>x</body></html>"
        )
        root = etree.fromstring(normaliseToXhtml(source))
        metas = root.findall(f".//{{{XHTML}}}meta")
        assert len(metas) == 1
        assert metas[0].get("content") == "text/html"

    def test_head_always_has_a_title(self) -> None:
        """Stripping the meta can leave a head with no permitted child."""
        source = b"<html><head><meta charset=utf-8></head><body>x</body></html>"
        root = etree.fromstring(normaliseToXhtml(source))
        head = root.find(f"{{{XHTML}}}head")
        assert head is not None
        assert (head.find(f"{{{XHTML}}}title") is not None) and len(head) >= 1

    def test_existing_title_is_kept(self) -> None:
        source = b"<html><head><title>Annex A</title></head><body>x</body></html>"
        root = etree.fromstring(normaliseToXhtml(source))
        titles = root.findall(f".//{{{XHTML}}}title")
        assert [t.text for t in titles] == ["Annex A"]

    def test_head_is_created_when_absent(self) -> None:
        root = etree.fromstring(normaliseToXhtml(b"<html><body>x</body></html>"))
        assert root.find(f"{{{XHTML}}}head") is not None

    def test_head_precedes_body(self) -> None:
        root = etree.fromstring(normaliseToXhtml(b"<html><body>x</body></html>"))
        assert [etree.QName(c).localname for c in root] == ["head", "body"]


# ── strict XHTML structure ────────────────────────────────────────────────────


class TestStrictStructure:
    """The schema is XHTML Strict, which pdftohtml's output violates in ways
    pdf2htmlEX's does not. Every rule here came from a real validation error
    against real converter output — see
    tests/integrationTests/test_real_pdf_converter.py."""

    @pytest.mark.parametrize(
        "attribute", ["bgcolor", "vlink", "link", "alink", "text", "background"]
    )
    def test_presentational_body_attributes_are_stripped(self, attribute: str) -> None:
        source = f'<html><body {attribute}="blue">x</body></html>'.encode()
        root = etree.fromstring(normaliseToXhtml(source))
        body = root.find(f"{{{XHTML}}}body")
        assert body is not None and attribute not in body.attrib

    def test_lang_is_dropped_but_xml_lang_kept(self) -> None:
        source = b'<html lang="en" xml:lang="en"><body>x</body></html>'
        root = etree.fromstring(normaliseToXhtml(source))
        assert "lang" not in root.attrib
        assert root.get("{http://www.w3.org/XML/1998/namespace}lang") == "en"

    def test_xml_lang_alone_survives(self) -> None:
        source = b'<html xml:lang="fr"><body>x</body></html>'
        root = etree.fromstring(normaliseToXhtml(source))
        assert root.get("{http://www.w3.org/XML/1998/namespace}lang") == "fr"

    def test_body_children_are_wrapped_in_a_block(self) -> None:
        """Strict requires block-level children; pdftohtml puts bare anchors
        straight in the body."""
        source = b'<html><body><a name="1"></a>loose text</body></html>'
        root = etree.fromstring(normaliseToXhtml(source))
        body = root.find(f"{{{XHTML}}}body")
        assert body is not None
        assert all(etree.QName(c).localname == "div" for c in body)

    def test_anchor_name_becomes_an_id(self) -> None:
        source = b'<html><body><a name="1">x</a></body></html>'
        root = etree.fromstring(normaliseToXhtml(source))
        anchor = root.find(f".//{{{XHTML}}}a")
        assert anchor is not None
        assert "name" not in anchor.attrib
        assert anchor.get("id") == "pdfpage-1"

    def test_anchor_id_is_a_valid_ncname(self) -> None:
        """A raw page number cannot be an id — NCNames may not start with a
        digit — so the prefix is load-bearing, not decoration."""
        root = etree.fromstring(
            normaliseToXhtml(b'<html><body><a name="1">x</a></body></html>')
        )
        anchor = root.find(f".//{{{XHTML}}}a")
        assert anchor is not None
        assert not (anchor.get("id") or "")[0].isdigit()

    def test_links_to_renamed_anchors_are_rewritten(self) -> None:
        """Otherwise the page navigation silently points at nothing."""
        source = b'<html><body><a name="1">x</a><a href="#1">go</a></body></html>'
        root = etree.fromstring(normaliseToXhtml(source))
        links = [a.get("href") for a in root.findall(f".//{{{XHTML}}}a")]
        assert "#pdfpage-1" in links

    def test_unrelated_links_are_not_rewritten(self) -> None:
        source = b'<html><body><a name="1">x</a><a href="#other">go</a></body></html>'
        root = etree.fromstring(normaliseToXhtml(source))
        assert "#other" in [a.get("href") for a in root.findall(f".//{{{XHTML}}}a")]

    def test_style_gets_a_type(self) -> None:
        source = b"<html><head><style>p{color:red}</style></head><body>y</body></html>"
        root = etree.fromstring(normaliseToXhtml(source))
        style = root.find(f".//{{{XHTML}}}style")
        assert style is not None and style.get("type") == "text/css"

    def test_existing_type_is_not_overwritten(self) -> None:
        source = (
            b'<html><head><style type="text/x-custom">p{color:red}</style></head>'
            b"<body>y</body></html>"
        )
        root = etree.fromstring(normaliseToXhtml(source))
        style = root.find(f".//{{{XHTML}}}style")
        assert style is not None and style.get("type") == "text/x-custom"

    def test_style_in_the_body_is_hoisted_to_the_head(self) -> None:
        source = b"<html><head><title>t</title></head><body><style>p{color:red}</style><div>x</div></body></html>"
        root = etree.fromstring(normaliseToXhtml(source))
        head = root.find(f"{{{XHTML}}}head")
        body = root.find(f"{{{XHTML}}}body")
        assert head is not None and body is not None
        assert head.findall(f"{{{XHTML}}}style")
        assert not body.findall(f".//{{{XHTML}}}style")

    def test_hoisted_style_keeps_its_css(self) -> None:
        source = b"<html><body><style>p{color:red}</style><div>x</div></body></html>"
        root = etree.fromstring(normaliseToXhtml(source))
        style = root.find(f".//{{{XHTML}}}style")
        assert style is not None and "color:red" in (style.text or "")

    def test_content_around_a_hoisted_style_survives(self) -> None:
        source = b"<html><body><div>before</div><style>p{color:red}</style>after</body></html>"
        text = etree.fromstring(normaliseToXhtml(source)).xpath("string()")
        assert "before" in text and "after" in text


# ── content preservation ──────────────────────────────────────────────────────


class TestContentPreserved:
    def test_text_survives(self, normalised: bytes) -> None:
        assert "Converted annex & appendix" in etree.fromstring(normalised).xpath(
            "string()"
        )

    def test_styles_survive(self, root: etree._Element) -> None:
        """The CSS is the whole reason for converting via pdf2htmlEX."""
        styles = root.findall(f".//{{{XHTML}}}style")
        assert styles and "position:relative" in (styles[0].text or "")

    def test_embedded_images_survive(self, root: etree._Element) -> None:
        images = root.findall(f".//{{{XHTML}}}img")
        assert images and (images[0].get("src") or "").startswith("data:image/png")

    def test_attributes_survive(self, root: etree._Element) -> None:
        assert root.findall(f".//{{{XHTML}}}div[@id='page-container']")

    def test_content_around_a_stripped_script_survives(self) -> None:
        source = (
            b"<html><body><div>before</div>"
            b"<script>if (1 < 2 && 3 > 2) { x(); }</script>after</body></html>"
        )
        text = etree.fromstring(normaliseToXhtml(source)).xpath("string()")
        assert "before" in text and "after" in text
        assert "x()" not in text


# ── script removal ────────────────────────────────────────────────────────────


class TestScriptsRemoved:
    """A document set member is part of a financial report other people open.
    Script the converter happened to bundle has no business travelling with it,
    and the iXBRL viewer supplies its own."""

    def test_scripts_are_removed(self) -> None:
        source = b"<html><head><script>var x = 1;</script></head><body>y</body></html>"
        root = etree.fromstring(normaliseToXhtml(source))
        assert root.findall(f".//{{{XHTML}}}script") == []

    def test_every_script_is_removed(self) -> None:
        source = (
            b"<html><head><script>a()</script></head>"
            b"<body><script>b()</script><div>x</div><script>c()</script></body></html>"
        )
        result = normaliseToXhtml(source)
        assert b"<script" not in result
        assert b"a()" not in result and b"b()" not in result and b"c()" not in result

    def test_the_real_converter_sample_has_none_left(self) -> None:
        """MALFORMED carries a pdf2htmlEX-shaped inline script."""
        assert b"<script" not in normaliseToXhtml(MALFORMED)

    def test_page_content_still_survives(self) -> None:
        """pdf2htmlEX's script only drives optional page navigation; the page
        itself is CSS-positioned content and renders without it."""
        result = normaliseToXhtml(MALFORMED)
        assert b"Converted annex" in result


class TestScriptingRemoved:
    """Removing <script> is not enough: a handler attribute and a javascript:
    URL are both valid XHTML Strict, so nothing else here would catch them, and
    the web app serves members same-origin.

    Preventive rather than observed — neither converter emits any of this."""

    @pytest.mark.parametrize(
        "source",
        [
            b'<html><body><div onclick="x()">a</div></body></html>',
            b'<html><body onload="x()">a</body></html>',
            b'<html><body><img src="a.png" onerror="x()"/></body></html>',
        ],
        ids=["onclick", "onload", "onerror"],
    )
    def test_event_handlers_are_removed(self, source: bytes) -> None:
        result = normaliseToXhtml(source)
        assert b"x()" not in result

    @pytest.mark.parametrize(
        "href",
        [b"javascript:alert(1)", b"JavaScript:alert(1)", b"java\tscript:alert(1)"],
        ids=["plain", "mixed-case", "split"],
    )
    def test_scripting_urls_are_removed(self, href: bytes) -> None:
        source = b'<html><body><p><a href="' + href + b'">link</a></p></body></html>'
        root = etree.fromstring(normaliseToXhtml(source))
        (anchor,) = root.findall(f".//{{{XHTML}}}a")
        assert anchor.get("href") is None
        assert anchor.text == "link", "the element stays; only the URL goes"

    @pytest.mark.parametrize(
        "attribute,value",
        [
            ("href", "#pdfpage-1"),
            ("href", "https://example.com/a"),
            ("title", "javascript: the language"),
        ],
        ids=["anchor", "http", "not-a-url-attribute"],
    )
    def test_ordinary_attributes_are_left_alone(
        self, attribute: str, value: str
    ) -> None:
        source = (
            f'<html><body><p><a {attribute}="{value}">x</a></p></body></html>'
        ).encode()
        root = etree.fromstring(normaliseToXhtml(source))
        (anchor,) = root.findall(f".//{{{XHTML}}}a")
        assert anchor.get(attribute) == value

    def test_inlined_images_are_left_alone(self) -> None:
        """Both converters' output is data: URIs by the time it arrives here."""
        source = (
            b'<html><body><img alt="" src="data:image/png;base64,iVBORw0KGgo="/>'
            b"</body></html>"
        )
        root = etree.fromstring(normaliseToXhtml(source))
        (img,) = root.findall(f".//{{{XHTML}}}img")
        assert img.get("src") == "data:image/png;base64,iVBORw0KGgo="


# ── rejection ─────────────────────────────────────────────────────────────────


class TestRejection:
    @pytest.mark.parametrize(
        "content",
        [b"", b"   \n  ", b"\x00\x01\x02not html"],
        ids=["empty", "whitespace", "binary"],
    )
    def test_unparseable_input_raises(self, content: bytes) -> None:
        with pytest.raises(PdfConversionError):
            normaliseToXhtml(content)

    def test_plain_text_is_recovered_rather_than_rejected(self) -> None:
        """An HTML parser's job is to make a document out of what it is given,
        and pdf2htmlEX's real output always has structure. Trying to tell
        "recovered badly" from "recovered fine" is not something this can do, so
        it does not pretend to: the guarantee is well-formed XHTML out, not a
        judgement on whether the converter did anything sensible."""
        result = normaliseToXhtml(b"just some text, no markup at all")
        assert b"just some text" in result
        etree.fromstring(result, etree.XMLParser())

    @pytest.mark.parametrize(
        "source",
        [
            b"<html><body><p>a<o:p>x</o:p></p></body></html>",
            b"<html><body><ix:header><ix:resources/></ix:header></body></html>",
            b"<html><body><p>a<svg><circle/></svg></p></body></html>",
            b"<html><body><article>a</article></body></html>",
        ],
        ids=["prefixed", "prefixed-ix", "other-vocabulary", "html5"],
    )
    def test_elements_xhtml_strict_does_not_define_are_rejected(
        self, source: bytes
    ) -> None:
        """Neither translated nor dropped: translation is a guess at whether the
        element was block or inline, and dropping is silent. Failing is how we
        find out what the converters we use actually emit. A rejected annex
        still ships, unconverted, as a warning."""
        with pytest.raises(PdfConversionError):
            normaliseToXhtml(source)

    def test_the_rejection_names_what_was_not_recognised(self) -> None:
        source = b"<html><body><article>a</article><aside>b</aside></body></html>"
        with pytest.raises(PdfConversionError, match="article, aside"):
            normaliseToXhtml(source)

    @pytest.mark.parametrize(
        "source",
        [
            (
                b'<html><body><object data="x.swf"><param name="a" value="b"/>'
                b"</object></body></html>"
            ),
            (
                b'<html><body><form action="/x"><input name="a"/>'
                b"<button>go</button></form></body></html>"
            ),
            (
                b'<html><head><base href="https://example.com/"/></head>'
                b"<body>x</body></html>"
            ),
        ],
        ids=["embedding", "form", "base"],
    )
    def test_elements_an_annex_has_no_use_for_are_rejected(self, source: bytes) -> None:
        """Valid XHTML Strict, so nothing else here would stop them: <object>
        embeds something the browser runs, a form submits somewhere, and <base>
        silently re-points every relative URL. Neither converter emits any of
        them, so one appearing means this is not converter output."""
        with pytest.raises(PdfConversionError, match="no use for"):
            normaliseToXhtml(source)

    def test_a_literally_named_ix_element_never_becomes_inline_xbrl(self) -> None:
        """An annex contributes content, never XBRL. From the HTML parser
        "ix:header" is only a name, but the root this module builds declares the
        ix prefix, so emitting it would make a real one on re-parse."""
        source = b"<html><body><ix:header><ix:resources/></ix:header></body></html>"
        with pytest.raises(PdfConversionError, match="ix:header"):
            normaliseToXhtml(source)
