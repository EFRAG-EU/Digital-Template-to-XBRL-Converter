"""Inlining a converted document's sibling files as data: URIs.

A document set member travels in the report package alone, so a reference to a
file the converter left in its working directory is a broken image once the
temporary directory is gone. pdftohtml always produces such references — the
Poppler build this was written against has no -dataurls option — so this step is
what makes its output usable at all.
"""

from __future__ import annotations

import base64
from pathlib import Path

import pytest

from mireport.pdf_converter._inline import inlineLocalResources

PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmM"
    "IQAAAABJRU5ErkJggg=="
)


@pytest.fixture
def workingDir(tmp_path: Path) -> Path:
    (tmp_path / "page001.png").write_bytes(PNG)
    (tmp_path / "page002.png").write_bytes(PNG)
    (tmp_path / "fonts.css").write_bytes(b"body{color:red}")
    return tmp_path


# ── attribute references ──────────────────────────────────────────────────────


class TestImageInlining:
    def test_relative_image_becomes_a_data_uri(self, workingDir: Path) -> None:
        result = inlineLocalResources(
            b'<html><body><img src="page001.png"/></body></html>', workingDir
        )
        assert b"data:image/png;base64," in result
        assert b'src="page001.png"' not in result

    def test_every_image_is_inlined(self, workingDir: Path) -> None:
        result = inlineLocalResources(
            b'<html><body><img src="page001.png"/><img src="page002.png"/></body></html>',
            workingDir,
        )
        assert result.count(b"data:image/png;base64,") == 2
        assert b".png" not in result

    def test_stylesheet_link_is_inlined(self, workingDir: Path) -> None:
        result = inlineLocalResources(
            b'<html><head><link rel="stylesheet" href="fonts.css"/></head></html>',
            workingDir,
        )
        assert b"data:text/css;base64," in result

    def test_url_encoded_reference_is_resolved(self, tmp_path: Path) -> None:
        (tmp_path / "a b.png").write_bytes(PNG)
        result = inlineLocalResources(
            b'<html><body><img src="a%20b.png"/></body></html>', tmp_path
        )
        assert b"data:image/png;base64," in result


class TestLeftAlone:
    @pytest.mark.parametrize(
        "reference",
        [
            b"data:image/png;base64,AAAA",
            b"https://example.com/a.png",
            b"http://example.com/a.png",
            b"//example.com/a.png",
            b"#anchor",
        ],
        ids=["data", "https", "http", "protocol-relative", "anchor"],
    )
    def test_non_local_references_are_untouched(
        self, workingDir: Path, reference: bytes
    ) -> None:
        source = b'<html><body><img src="' + reference + b'"/></body></html>'
        assert reference in inlineLocalResources(source, workingDir)

    def test_missing_file_is_left_as_is(self, workingDir: Path) -> None:
        """Better a visibly broken reference than a crash mid-conversion."""
        result = inlineLocalResources(
            b'<html><body><img src="nope.png"/></body></html>', workingDir
        )
        assert b"nope.png" in result

    def test_unparseable_content_is_returned_unchanged(self, workingDir: Path) -> None:
        """Normalisation runs next and reports the problem properly."""
        assert inlineLocalResources(b"\x00\x01", workingDir) == b"\x00\x01"

    def test_document_without_references_is_unchanged(self, workingDir: Path) -> None:
        source = b"<html><body><p>text only</p></body></html>"
        assert inlineLocalResources(source, workingDir) == source


# ── escaping the working directory ────────────────────────────────────────────


class TestPathTraversal:
    def test_parent_directory_is_refused(self, tmp_path: Path) -> None:
        """References derive from a converter's handling of an uploaded file;
        nothing outside its own scratch space is ever legitimate."""
        secret = tmp_path / "secret.png"
        secret.write_bytes(b"TOP SECRET")
        working = tmp_path / "work"
        working.mkdir()
        result = inlineLocalResources(
            b'<html><body><img src="../secret.png"/></body></html>', working
        )
        assert b"data:" not in result
        assert base64.b64encode(b"TOP SECRET") not in result

    def test_absolute_path_is_refused(self, tmp_path: Path) -> None:
        secret = tmp_path / "secret.png"
        secret.write_bytes(PNG)
        working = tmp_path / "work"
        working.mkdir()
        source = (
            b'<html><body><img src="'
            + str(secret).replace("\\", "/").encode()
            + b'"/></body></html>'
        )
        assert b"data:" not in inlineLocalResources(source, working)

    def test_nested_file_inside_the_working_dir_is_allowed(
        self, tmp_path: Path
    ) -> None:
        nested = tmp_path / "images"
        nested.mkdir()
        (nested / "a.png").write_bytes(PNG)
        result = inlineLocalResources(
            b'<html><body><img src="images/a.png"/></body></html>', tmp_path
        )
        assert b"data:image/png;base64," in result


# ── CSS url() references ──────────────────────────────────────────────────────


class TestCssUrls:
    def test_url_in_a_style_element_is_inlined(self, workingDir: Path) -> None:
        result = inlineLocalResources(
            b"<html><head><style>.a{background:url(page001.png)}</style></head></html>",
            workingDir,
        )
        assert b"url(data:image/png;base64," in result

    def test_quoted_url_is_inlined(self, workingDir: Path) -> None:
        """The author's quoting is preserved; only the reference is replaced."""
        result = inlineLocalResources(
            b'<html><head><style>.a{background:url("page001.png")}</style></head></html>',
            workingDir,
        )
        assert b'url("data:image/png;base64,' in result

    def test_url_in_a_style_attribute_is_inlined(self, workingDir: Path) -> None:
        result = inlineLocalResources(
            b'<html><body><div style="background:url(page001.png)"></div></body></html>',
            workingDir,
        )
        assert b"url(data:image/png;base64," in result

    def test_data_url_is_not_re_inlined(self, workingDir: Path) -> None:
        source = b"<html><head><style>.a{background:url(data:image/png;base64,AA)}</style></head></html>"
        assert b"url(data:image/png;base64,AA)" in inlineLocalResources(
            source, workingDir
        )

    def test_remote_url_is_left_alone(self, workingDir: Path) -> None:
        source = b"<html><head><style>.a{background:url(https://x.test/a.png)}</style></head></html>"
        assert b"https://x.test/a.png" in inlineLocalResources(source, workingDir)


class TestOnlyRealCssReferences:
    """Only genuine url() tokens are rewritten.

    Each of these reads as a reference to anything scanning the document for a
    url() pattern, and none of them is one.
    """

    def test_url_inside_a_css_string_is_left_alone(self, workingDir: Path) -> None:
        source = b'<html><head><style>.a:before{content:"url(page001.png)"}</style></head></html>'
        assert b"page001.png" in inlineLocalResources(source, workingDir)

    def test_url_inside_a_css_comment_is_left_alone(self, workingDir: Path) -> None:
        source = b"<html><head><style>/* url(page001.png) */.a{color:red}</style></head></html>"
        assert b"page001.png" in inlineLocalResources(source, workingDir)

    def test_url_in_body_text_is_left_alone(self, workingDir: Path) -> None:
        source = b"<html><body><p>url(page001.png)</p></body></html>"
        assert inlineLocalResources(source, workingDir) == source
