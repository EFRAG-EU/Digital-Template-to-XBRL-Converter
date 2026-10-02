"""diagnosticMarkdown(): the diagnostics as a copy-pastable markdown table."""

from dataclasses import replace

from arelle.ModelValue import QName

from mireport.arelle.diagnostics import ArelleDiagnostic
from mireport.cli import diagnosticMarkdown
from mireport.diagnostics import Diagnostic

ELR = "https://example.com/elr/99007"
HEADER = "| Level | Message | ELR | Concepts | Details |"
SEPARATOR = "|---|---|---|---|---|"


def rows(markdown: str) -> list[str]:
    """The table body rows (everything between the separator and the blank line)."""
    lines = markdown.splitlines()
    start = lines.index(SEPARATOR) + 1
    body = []
    for line in lines[start:]:
        if not line.startswith("|"):
            break
        body.append(line)
    return body


def test_title_header_and_separator() -> None:
    markdown = diagnosticMarkdown("Taxonomy diagnostics", [Diagnostic.warning("w")])

    lines = markdown.splitlines()
    assert lines[0] == "### Taxonomy diagnostics"
    assert lines[1] == ""
    assert lines[2] == HEADER
    assert lines[3] == SEPARATOR


def test_rows_are_sorted_by_descending_severity() -> None:
    markdown = diagnosticMarkdown(
        "t",
        [
            Diagnostic.info("an info"),
            Diagnostic.error("an error"),
            Diagnostic.warning("a warning"),
        ],
    )

    assert [row.split(" | ")[0] for row in rows(markdown)] == [
        "| Error",
        "| Warning",
        "| Info",
    ]


def test_message_is_not_truncated() -> None:
    message = "Explicit dimension has no dimension-domain relationships " * 3

    (row,) = rows(diagnosticMarkdown("t", [Diagnostic.error(message.strip())]))

    assert message.strip() in row


def test_pipes_in_cells_are_escaped() -> None:
    (row,) = rows(diagnosticMarkdown("t", [Diagnostic.warning("a | b")]))

    assert r"a \| b" in row


def test_multiline_details_and_hint_become_line_breaks() -> None:
    diagnostic = Diagnostic.warning("w", hint="first\nsecond", domainHead="vsme:X")

    (row,) = rows(diagnosticMarkdown("t", [diagnostic]))

    assert "domainHead: vsme:X<br>hint: first<br>second" in row
    assert "\n" not in row


def test_elr_is_code_with_definition_after_a_line_break() -> None:
    diagnostic = replace(
        Diagnostic.warning("w", elr=ELR), elrDefinition="[99007]\n  Practices"
    )

    (row,) = rows(diagnosticMarkdown("t", [diagnostic]))

    assert f"`{ELR}`<br>[99007] Practices" in row


def test_elr_without_definition_is_just_the_code_uri() -> None:
    (row,) = rows(diagnosticMarkdown("t", [Diagnostic.warning("w", elr=ELR)]))

    assert f"| `{ELR}` |" in row


def test_no_elr_gives_an_empty_cell() -> None:
    (row,) = rows(diagnosticMarkdown("t", [Diagnostic.warning("w")]))

    assert row.split(" | ")[2] == ""


def test_concepts_are_code_joined_by_line_breaks() -> None:
    diagnostic = ArelleDiagnostic.warning(
        "w",
        concepts=[
            QName("vsme", "https://example.com/vsme", "A"),
            QName("vsme", "https://example.com/vsme", "B"),
        ],
    )

    (row,) = rows(diagnosticMarkdown("t", [diagnostic]))

    assert "`vsme:A`<br>`vsme:B`" in row


def test_summary_counts_follow_the_table() -> None:
    markdown = diagnosticMarkdown(
        "Taxonomy diagnostics",
        [Diagnostic.error("e"), Diagnostic.warning("a"), Diagnostic.warning("b")],
    )

    assert (
        markdown.rstrip().splitlines()[-1]
        == "2 warnings, 1 error (taxonomy diagnostics)."
    )


def test_no_diagnostics_says_so_without_a_table() -> None:
    markdown = diagnosticMarkdown("Taxonomy diagnostics", [])

    assert (
        markdown == "### Taxonomy diagnostics\n\nNo taxonomy diagnostics to report.\n"
    )
