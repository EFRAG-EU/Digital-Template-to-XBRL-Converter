"""normaliseFragment: HTML that joins a report, such as a Word text block,
made XHTML Strict by the same rules as a document, without the assembly."""

from __future__ import annotations

import pytest
from lxml import etree

from mireport.html_to_xhtml import XhtmlError, normaliseFragment

XHTML = "http://www.w3.org/1999/xhtml"


def _parsed(fragment: str) -> etree._Element:
    """The normalised @fragment, inside the default namespace a report gives it."""
    return etree.fromstring(f'<div xmlns="{XHTML}">{normaliseFragment(fragment)}</div>')


def test_plain_mammoth_output_is_unchanged():
    fragment = (
        "<h1>Policy</h1><p>Some <strong>bold</strong> and <em>italic</em> text"
        '<sup><a href="#footnote-1" id="footnote-ref-1">[1]</a></sup>.</p>'
        "<ul><li>one</li><li>two</li></ul>"
        "<table><tr><td>a</td><td>b</td></tr></table><p>line<br />break</p>"
    )
    assert normaliseFragment(fragment) == fragment.replace("<br />", "<br/>")


def test_leading_text_is_kept_and_escaped():
    assert normaliseFragment("a &amp; b<p>c</p>") == "a &amp; b<p>c</p>"


def test_empty_fragment_is_empty():
    assert normaliseFragment("  ") == ""


def test_strikethrough_becomes_a_line_through_span():
    assert normaliseFragment("<p>a <s>b</s> c</p>") == (
        '<p>a <span style="text-decoration:line-through">b</span> c</p>'
    )


@pytest.mark.parametrize(
    ("checkbox", "box"),
    [
        ('<input type="checkbox" checked="checked" disabled="disabled" />', "☒"),
        ('<input type="checkbox" disabled="disabled" />', "☐"),
    ],
    ids=["checked", "unchecked"],
)
def test_a_checkbox_becomes_its_box_character(checkbox, box):
    assert normaliseFragment(f"<p>{checkbox} agreed</p>") == f"<p>{box} agreed</p>"


def test_an_image_without_alt_gets_an_empty_one():
    (image,) = _parsed('<p><img src="data:image/png;base64,AA" /></p>').iter(
        f"{{{XHTML}}}img"
    )
    assert image.get("alt") == ""


def test_an_image_keeps_its_own_alt():
    assert 'alt="A chart"' in normaliseFragment('<img src="a.png" alt="A chart" />')


def test_script_in_every_form_is_removed():
    result = normaliseFragment(
        '<p onclick="x()">a</p><script>alert(1)</script>'
        '<a href="javascript:alert(1)">b</a>'
    )
    assert result == "<p>a</p><a>b</a>"


def test_an_element_strict_does_not_define_is_refused():
    with pytest.raises(XhtmlError, match="XHTML Strict does not define: article"):
        normaliseFragment("<article>a</article>")


def test_an_element_of_a_whole_document_is_refused():
    with pytest.raises(XhtmlError, match="whole document.*title"):
        normaliseFragment("<p>a</p><title>t</title>")


def test_the_result_is_well_formed_in_the_xhtml_namespace():
    root = _parsed("<p>a<br>b</p><p>c")
    assert [child.tag for child in root] == [f"{{{XHTML}}}p", f"{{{XHTML}}}p"]
