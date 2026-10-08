"""The ELR cell of printDiagnosticTable(): the ELR URI with its role
definition underneath, in full -- rich wraps it to the column."""

from dataclasses import replace

from mireport.cli import _diagnosticELR
from mireport.diagnostics import Diagnostic

ELR = "https://example.com/elr/99007"


def withDefinition(definition: str | None) -> Diagnostic:
    return replace(Diagnostic.warning("w", elr=ELR), elrDefinition=definition)


def test_definition_shown_under_uri() -> None:
    assert _diagnosticELR(withDefinition("[99007] Short")).plain == (
        f"{ELR}\n[99007] Short"
    )


def test_long_definition_shown_in_full() -> None:
    definition = "[99007] Enumeration of practices, policies and future initiatives"

    assert _diagnosticELR(withDefinition(definition)).plain == f"{ELR}\n{definition}"


def test_definition_whitespace_is_collapsed_to_one_line() -> None:
    assert _diagnosticELR(withDefinition("[1]\n  Two   words")).plain == (
        f"{ELR}\n[1] Two words"
    )


def test_no_definition_is_just_the_uri() -> None:
    assert _diagnosticELR(withDefinition(None)).plain == ELR


def test_no_elr_is_empty() -> None:
    assert _diagnosticELR(Diagnostic.warning("w")).plain == ""
