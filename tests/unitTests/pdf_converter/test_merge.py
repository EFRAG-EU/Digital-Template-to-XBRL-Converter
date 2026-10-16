"""Tests for turning one normalised, converted PDF into a mergeable annex, and
for splicing that annex into a finished report.

See the module docstring of :mod:`mireport.pdf_converter._merge` for why each
of these is needed: id uniqueness is a document-wide XML constraint that
survives even once the annex shares a document with the report, and CSS
isolation (``@scope`` plus an unlayered reset) is what stops the annex's own
styling leaking onto — or being overridden by — the report around it.
"""

from __future__ import annotations

import pytest
from lxml import etree

from mireport.filesupport import FilelikeAndFileName
from mireport.pdf_converter import PdfConversionError
from mireport.pdf_converter._merge import (
    ANNEX_CLASS,
    MergedAnnex,
    buildMergedAnnex,
    mergeAnnexesIntoReport,
)
from mireport.pdf_converter._xhtml import normaliseToXhtml

XHTML = "http://www.w3.org/1999/xhtml"


def _tag(localName: str) -> str:
    return f"{{{XHTML}}}{localName}"


NORMALISED_ANNEX = normaliseToXhtml(
    b"""<!DOCTYPE html>
<html><head><meta charset=utf-8>
<style type="text/css">
.pf{position:relative;background-color:white}
#special{color:red}
</style>
</head>
<body>
<div id="page-container">
<div class="pf" data-page-no=1>
<div class="t" id="special">Converted annex &amp; appendix</div>
<a href="#special">jump</a>
</div>
</div>
</body>
</html>
""",
    injectIxHeader=False,
)


@pytest.fixture
def annex():
    return buildMergedAnnex(NORMALISED_ANNEX, index=1, label="annex1.pdf")


@pytest.fixture
def bodyRoot(annex):
    return etree.fromstring(annex.body_html.encode("utf-8"))


class TestAnchorAndLabel:
    def test_anchor_id_is_derived_from_the_index(self, annex) -> None:
        assert annex.anchor_id == "pdf-annex-1"

    def test_label_is_the_original_filename(self, annex) -> None:
        assert annex.label == "annex1.pdf"

    def test_different_index_gives_a_different_anchor(self) -> None:
        other = buildMergedAnnex(NORMALISED_ANNEX, index=2, label="annex1.pdf")
        assert other.anchor_id == "pdf-annex-2"


class TestBodyFragment:
    def test_is_well_formed_xml(self, annex) -> None:
        etree.fromstring(annex.body_html.encode("utf-8"))

    def test_root_carries_the_anchor_id(self, bodyRoot) -> None:
        assert bodyRoot.get("id") == "pdf-annex-1"

    def test_root_carries_the_annex_class(self, bodyRoot) -> None:
        assert bodyRoot.get("class") == ANNEX_CLASS

    def test_root_is_in_the_xhtml_namespace(self, bodyRoot) -> None:
        assert bodyRoot.tag == _tag("div")

    def test_no_prefixed_elements(self, annex) -> None:
        """A stray auto-generated namespace prefix would still be valid XML,
        but every element belongs in the same (default) XHTML namespace as the
        report it joins."""
        assert "<html:" not in annex.body_html
        assert ":div" not in annex.body_html.replace("</div>", "")

    def test_original_content_survives(self, annex) -> None:
        assert "Converted annex &amp; appendix" in annex.body_html

    def test_ids_are_renamed_unique_to_this_annex(self, bodyRoot) -> None:
        ids = [e.get("id") for e in bodyRoot.iter() if e.get("id") is not None]
        assert "special" not in ids
        assert "pdf-annex-1-special" in ids

    def test_two_annexes_from_the_same_source_do_not_collide(self) -> None:
        first = buildMergedAnnex(NORMALISED_ANNEX, index=1, label="a.pdf")
        second = buildMergedAnnex(NORMALISED_ANNEX, index=2, label="b.pdf")
        assert "pdf-annex-1-special" in first.body_html
        assert "pdf-annex-2-special" in second.body_html
        assert "pdf-annex-2-special" not in first.body_html

    def test_same_document_href_is_rewritten_to_match(self, annex) -> None:
        assert 'href="#pdf-annex-1-special"' in annex.body_html
        assert 'href="#special"' not in annex.body_html


class TestScopedCss:
    def test_wrapped_in_scope_to_the_anchor(self, annex) -> None:
        assert "@scope (#pdf-annex-1)" in annex.head_css

    def test_no_layer_is_used(self, annex) -> None:
        """An unlayered reset must beat the report's own unlayered rules, and
        an unlayered annex rule must not lose to them either — so nothing here
        is layered. See the module docstring."""
        assert "@layer" not in annex.head_css

    def test_container_reset_precedes_the_scoped_rules(self, annex) -> None:
        resetIndex = annex.head_css.index("#pdf-annex-1 { all: revert; }")
        scopeIndex = annex.head_css.index("@scope (#pdf-annex-1)")
        assert resetIndex < scopeIndex

    def test_link_states_are_reset(self, annex) -> None:
        for state in ("a", "a:link", "a:visited", "a:hover"):
            assert f"#pdf-annex-1 {state}" in annex.head_css

    def test_id_selector_is_renamed_to_match(self, annex) -> None:
        assert "#pdf-annex-1-special" in annex.head_css

    def test_original_id_selector_does_not_survive_unrenamed(self, annex) -> None:
        import re

        # No "#special" that is not part of the longer renamed selector.
        assert not re.search(r"(?<!-)#special\b", annex.head_css)

    def test_class_selectors_are_left_alone(self, annex) -> None:
        """Isolation for class selectors comes from @scope, not renaming."""
        assert ".pf{" in annex.head_css

    def test_style_elements_do_not_appear_in_the_body_fragment(self, annex) -> None:
        assert "<style" not in annex.body_html

    def test_two_annexes_get_distinct_resets(self) -> None:
        first = buildMergedAnnex(NORMALISED_ANNEX, index=1, label="a.pdf")
        second = buildMergedAnnex(NORMALISED_ANNEX, index=2, label="b.pdf")
        assert "#pdf-annex-1 { all: revert; }" in first.head_css
        assert "#pdf-annex-2 { all: revert; }" in second.head_css
        assert "pdf-annex-2" not in first.head_css


class TestNoStylesAtAll:
    def test_handles_an_annex_with_no_style_element(self) -> None:
        normalised = normaliseToXhtml(
            b"<html><body><p>No CSS here</p></body></html>",
            injectIxHeader=False,
        )
        annex = buildMergedAnnex(normalised, index=3, label="plain.pdf")
        assert "No CSS here" in annex.body_html
        assert "@scope (#pdf-annex-3)" in annex.head_css


class TestMalformedInput:
    def test_raises_pdf_conversion_error_on_unparseable_xml(self) -> None:
        with pytest.raises(PdfConversionError):
            buildMergedAnnex(b"not xml at all <<<", index=1, label="bad.pdf")


def _annex(anchor_id: str = "pdf-annex-1", label: str = "annex1.pdf") -> MergedAnnex:
    return MergedAnnex(
        anchor_id=anchor_id,
        label=label,
        body_html=(
            f'<div xmlns="{XHTML}" id="{anchor_id}" class="{ANNEX_CLASS}">'
            "Annex body</div>"
        ),
        head_css=f"@scope (#{anchor_id}) {{ p {{ color: red }} }}",
    )


def _report(*, withToc: bool = True) -> FilelikeAndFileName:
    toc = (
        '<div class="page toc-page" id="table-of-contents">'
        '<ol class="toc-list"><li><span>G</span><ul>'
        '<li><a href="#section-class0">S1</a></li></ul></li></ol></div>'
        if withToc
        else ""
    )
    html = (
        f'<html xmlns="{XHTML}"><head><title>t</title></head>'
        f'<body>{toc}<div class="page">content</div></body></html>'
    )
    return FilelikeAndFileName(fileContent=html.encode("utf-8"), filename="r.html")


def _reportWithoutHead() -> FilelikeAndFileName:
    html = f'<html xmlns="{XHTML}"><body>no head here</body></html>'
    return FilelikeAndFileName(fileContent=html.encode("utf-8"), filename="r.html")


def _reportWithoutBody() -> FilelikeAndFileName:
    html = f'<html xmlns="{XHTML}"><head></head></html>'
    return FilelikeAndFileName(fileContent=html.encode("utf-8"), filename="r.html")


class TestMergeAnnexesIntoReport:
    """Splicing one or more MergedAnnex into a finished report's own bytes."""

    def test_empty_annexes_returns_the_same_object(self) -> None:
        report = _report()
        assert mergeAnnexesIntoReport(report, []) is report

    def test_filename_is_unchanged(self) -> None:
        result = mergeAnnexesIntoReport(_report(), [_annex()])
        assert result.filename == "r.html"

    def test_doctype_is_preserved(self) -> None:
        html = b"<!DOCTYPE html>\n" + _report().fileContent
        report = FilelikeAndFileName(fileContent=html, filename="r.html")
        result = mergeAnnexesIntoReport(report, [_annex()])
        assert result.fileContent.startswith(b"<!DOCTYPE html>")

    def test_annex_body_is_appended_to_the_body(self) -> None:
        result = mergeAnnexesIntoReport(_report(), [_annex()])
        content = result.fileContent.decode("utf-8")
        assert 'id="pdf-annex-1"' in content
        assert "Annex body" in content

    def test_annex_css_is_appended_to_the_head(self) -> None:
        annex = _annex()
        result = mergeAnnexesIntoReport(_report(), [annex])
        root = etree.fromstring(result.fileContent)
        head = root.find(f"{{{XHTML}}}head")
        styles = [s.text for s in head.findall(f"{{{XHTML}}}style")]
        assert annex.head_css in styles

    def test_toc_entry_is_added_under_an_annexes_heading(self) -> None:
        result = mergeAnnexesIntoReport(_report(), [_annex()])
        content = result.fileContent.decode("utf-8")
        assert "<span>Annexes</span>" in content
        assert 'href="#pdf-annex-1"' in content
        assert ">annex1.pdf<" in content

    def test_toc_label_is_configurable(self) -> None:
        result = mergeAnnexesIntoReport(_report(), [_annex()], tocLabel="Appendices")
        content = result.fileContent.decode("utf-8")
        assert "<span>Appendices</span>" in content

    def test_existing_toc_entries_are_preserved(self) -> None:
        result = mergeAnnexesIntoReport(_report(), [_annex()])
        content = result.fileContent.decode("utf-8")
        assert 'href="#section-class0"' in content

    def test_handles_several_annexes_independently(self) -> None:
        first, second = _annex("pdf-annex-1", "a.pdf"), _annex("pdf-annex-2", "b.pdf")
        result = mergeAnnexesIntoReport(_report(), [first, second])
        content = result.fileContent.decode("utf-8")
        assert 'id="pdf-annex-1"' in content
        assert 'id="pdf-annex-2"' in content
        assert first.head_css in content
        assert second.head_css in content
        assert content.count("<li>") >= 3  # existing entry + 2 annex entries

    def test_raises_if_there_is_no_head(self) -> None:
        with pytest.raises(PdfConversionError, match="<head>"):
            mergeAnnexesIntoReport(_reportWithoutHead(), [_annex()])

    def test_raises_if_there_is_no_body(self) -> None:
        with pytest.raises(PdfConversionError, match="<body>"):
            mergeAnnexesIntoReport(_reportWithoutBody(), [_annex()])

    def test_raises_if_there_is_no_toc(self) -> None:
        with pytest.raises(PdfConversionError, match="toc-list"):
            mergeAnnexesIntoReport(_report(withToc=False), [_annex()])

    def test_raises_on_unparseable_report_bytes(self) -> None:
        report = FilelikeAndFileName(
            fileContent=b"not xml at all <<<", filename="r.html"
        )
        with pytest.raises(PdfConversionError):
            mergeAnnexesIntoReport(report, [_annex()])
