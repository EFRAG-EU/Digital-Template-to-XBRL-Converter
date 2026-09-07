"""Unit tests for the mireport-model structured diagnostics.

This mirrors tests/unitTests/arelle/test_diagnostics.py, which covers the
same AbstractDiagnostic behaviour via the Arelle-facing ArelleDiagnostic
subclass. logTo()/DiagnosticCollector/DiagnosticEmitter are Arelle-specific
and stay tested there.
"""

import logging

from mireport.diagnostics import Diagnostic
from mireport.xml import QName, getBootstrapQNameMaker

_qnameMaker = getBootstrapQNameMaker()
_qnameMaker.addNamespacePrefix("vsme", "https://example.com/vsme")


def qn(local: str) -> QName:
    return _qnameMaker.fromString(f"vsme:{local}")


class TestConstructors:
    def test_default_level_is_info(self) -> None:
        assert Diagnostic("hello").level == logging.INFO

    def test_level_shortcuts(self) -> None:
        assert Diagnostic.info("x").level == logging.INFO
        assert Diagnostic.warning("x").level == logging.WARNING
        assert Diagnostic.error("x").level == logging.ERROR

    def test_shortcut_kwargs_become_details(self) -> None:
        d = Diagnostic.warning(
            "x", elr="https://elr", concepts=(qn("A"),), hint="fix it", role="label"
        )
        assert d.elr == "https://elr"
        assert d.concepts == (qn("A"),)
        assert d.hint == "fix it"
        assert d.details == {"role": "label"}

    def test_concepts_normalised_to_tuple(self) -> None:
        d = Diagnostic.info("x", concepts=[qn("A"), qn("B")])
        assert d.concepts == (qn("A"), qn("B"))


class TestFormat:
    def test_text_only(self) -> None:
        assert Diagnostic("Something happened").format() == "Something happened"

    def test_elr_on_own_line(self) -> None:
        d = Diagnostic.warning("Presentation is empty", elr="https://example.com/elr")
        assert d.format() == ("Presentation is empty\n  elr: https://example.com/elr")

    def test_single_concept(self) -> None:
        d = Diagnostic.warning("Dimension has no domain", concepts=(qn("FooAxis"),))
        assert d.format() == ("Dimension has no domain\n  concept: vsme:FooAxis")

    def test_multiple_concepts_listed(self) -> None:
        d = Diagnostic.warning("Multiple roots", concepts=(qn("RootB"), qn("RootA")))
        # order is caller-controlled, not sorted here
        assert d.format() == (
            "Multiple roots\n  concepts:\n    vsme:RootB\n    vsme:RootA"
        )

    def test_details_in_insertion_order(self) -> None:
        d = Diagnostic.warning("Duplicate labels", lang="en", role="std", label="X")
        assert d.format() == ("Duplicate labels\n  lang: en\n  role: std\n  label: X")

    def test_details_sequence_values_comma_joined(self) -> None:
        d = Diagnostic.error("Too many defaults", members=[qn("M1"), qn("M2")])
        assert d.format() == ("Too many defaults\n  members: vsme:M1, vsme:M2")

    def test_hint_is_last(self) -> None:
        d = Diagnostic.error(
            "QName has no namespace defined",
            concepts=(qn("Thing"),),
            hint="check elementFormDefault",
            context="of concept",
        )
        assert d.format() == (
            "QName has no namespace defined\n"
            "  concept: vsme:Thing\n"
            "  context: of concept\n"
            "  hint: check elementFormDefault"
        )

    def test_field_order_elr_concepts_details(self) -> None:
        d = Diagnostic.warning(
            "Domain head oddity",
            elr="https://elr",
            concepts=(qn("FooAxis"),),
            domainHead=qn("BarDomain"),
        )
        assert d.format() == (
            "Domain head oddity\n"
            "  elr: https://elr\n"
            "  concept: vsme:FooAxis\n"
            "  domainHead: vsme:BarDomain"
        )
