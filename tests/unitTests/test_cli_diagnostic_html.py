"""diagnosticHtml(): the diagnostics as an HTML table that pastes into Teams
(and other rich-text boxes) as a real table when copied from a browser."""

from dataclasses import replace

from arelle.ModelValue import QName

from mireport.arelle.diagnostics import ArelleDiagnostic
from mireport.cli import diagnosticHtml
from mireport.diagnostics import Diagnostic

ELR = "https://example.com/elr/99007"


def test_has_title_header_cells_and_a_bordered_table() -> None:
    html = diagnosticHtml("Taxonomy diagnostics", [Diagnostic.warning("w")])

    assert "<h3>Taxonomy diagnostics</h3>" in html
    assert '<table border="1" cellpadding="4" cellspacing="0">' in html
    for column in ("Level", "Message", "ELR", "Concepts", "Details"):
        assert f"<th>{column}</th>" in html


def test_rows_are_sorted_by_descending_severity() -> None:
    html = diagnosticHtml(
        "t",
        [Diagnostic.info("an info"), Diagnostic.error("an error")],
    )

    assert html.index("an error") < html.index("an info")
    assert html.count("<tr>") == 3  # header + two rows


def test_markup_in_text_is_escaped() -> None:
    html = diagnosticHtml("t", [Diagnostic.warning("a <b> & c")])

    assert "a &lt;b&gt; &amp; c" in html
    assert "<b>" not in html


def test_multiline_cells_use_line_breaks() -> None:
    html = diagnosticHtml("t", [Diagnostic.warning("w", hint="first\nsecond")])

    assert "hint: first<br>second" in html


def test_elr_is_code_with_definition_after_a_line_break() -> None:
    diagnostic = replace(Diagnostic.warning("w", elr=ELR), elrDefinition="[1]\n  Two")

    html = diagnosticHtml("t", [diagnostic])

    assert f"<code>{ELR}</code><br>[1] Two" in html


def test_concepts_are_code_joined_by_line_breaks() -> None:
    diagnostic = ArelleDiagnostic.warning(
        "w",
        concepts=[
            QName("vsme", "https://example.com/vsme", "A"),
            QName("vsme", "https://example.com/vsme", "B"),
        ],
    )

    html = diagnosticHtml("t", [diagnostic])

    assert "<code>vsme:A</code><br><code>vsme:B</code>" in html


def test_summary_follows_the_table() -> None:
    html = diagnosticHtml("Taxonomy diagnostics", [Diagnostic.error("e")])

    assert html.rstrip().endswith("<p>1 error (taxonomy diagnostics).</p>")


def test_no_diagnostics_says_so_without_a_table() -> None:
    html = diagnosticHtml("Taxonomy diagnostics", [])

    assert "<table" not in html
    assert "<p>No taxonomy diagnostics to report.</p>" in html
