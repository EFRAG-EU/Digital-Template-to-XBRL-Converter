"""Unit tests for the structured taxonomy diagnostics."""

import logging
from dataclasses import replace
from typing import Any, cast

from arelle.Cntlr import Cntlr
from arelle.ModelValue import QName

from mireport.arelle.diagnostics import (
    ArelleDiagnostic,
    DiagnosticEmitter,
    logTo,
)


def qn(local: str) -> QName:
    return QName("vsme", "https://example.com/vsme", local)


class TestConstructors:
    def test_default_level_is_info(self) -> None:
        assert ArelleDiagnostic("hello").level == logging.INFO

    def test_level_shortcuts(self) -> None:
        assert ArelleDiagnostic.info("x").level == logging.INFO
        assert ArelleDiagnostic.warning("x").level == logging.WARNING
        assert ArelleDiagnostic.error("x").level == logging.ERROR

    def test_shortcut_kwargs_become_details(self) -> None:
        d = ArelleDiagnostic.warning(
            "x", elr="https://elr", concepts=(qn("A"),), hint="fix it", role="label"
        )
        assert d.elr == "https://elr"
        assert d.concepts == (qn("A"),)
        assert d.hint == "fix it"
        assert d.details == {"role": "label"}

    def test_concepts_normalised_to_tuple(self) -> None:
        d = ArelleDiagnostic.info("x", concepts=[qn("A"), qn("B")])
        assert d.concepts == (qn("A"), qn("B"))


class TestFormat:
    def test_text_only(self) -> None:
        assert ArelleDiagnostic("Something happened").format() == "Something happened"

    def test_elr_on_own_line(self) -> None:
        d = ArelleDiagnostic.warning(
            "Presentation is empty", elr="https://example.com/elr"
        )
        assert d.format() == ("Presentation is empty\n  elr: https://example.com/elr")

    def test_elr_definition_follows_elr(self) -> None:
        d = replace(
            ArelleDiagnostic.warning("Presentation is empty", elr="https://e.com/elr"),
            elrDefinition="[100] General information",
        )
        assert d.format() == (
            "Presentation is empty\n"
            "  elr: https://e.com/elr\n"
            "  elr definition: [100] General information"
        )

    def test_single_concept(self) -> None:
        d = ArelleDiagnostic.warning(
            "Dimension has no domain", concepts=(qn("FooAxis"),)
        )
        assert d.format() == ("Dimension has no domain\n  concept: vsme:FooAxis")

    def test_multiple_concepts_listed(self) -> None:
        d = ArelleDiagnostic.warning(
            "Multiple roots", concepts=(qn("RootB"), qn("RootA"))
        )
        # order is caller-controlled, not sorted here
        assert d.format() == (
            "Multiple roots\n  concepts:\n    vsme:RootB\n    vsme:RootA"
        )

    def test_details_in_insertion_order(self) -> None:
        d = ArelleDiagnostic.warning(
            "Duplicate labels", lang="en", role="std", label="X"
        )
        assert d.format() == ("Duplicate labels\n  lang: en\n  role: std\n  label: X")

    def test_details_sequence_values_comma_joined(self) -> None:
        d = ArelleDiagnostic.error("Too many defaults", members=[qn("M1"), qn("M2")])
        assert d.format() == ("Too many defaults\n  members: vsme:M1, vsme:M2")

    def test_hint_is_last(self) -> None:
        d = ArelleDiagnostic.error(
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
        d = ArelleDiagnostic.warning(
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


class StubCntlr:
    def __init__(self) -> None:
        self.logged: list[tuple[str, int]] = []

    def addToLog(self, message: str, level: int = logging.INFO, **kwargs: Any) -> None:
        self.logged.append((message, level))


class TestLogTo:
    def test_passes_formatted_message_and_level(self) -> None:
        cntlr = StubCntlr()
        d = ArelleDiagnostic.warning("Presentation is empty", elr="https://elr")
        logTo(cast(Cntlr, cntlr), d)
        assert cntlr.logged == [
            ("Presentation is empty\n  elr: https://elr", logging.WARNING)
        ]


class TestDiagnosticEmitter:
    def test_collects_into_the_sink(self) -> None:
        cntlr = StubCntlr()
        collected: list[ArelleDiagnostic] = []
        emitter = DiagnosticEmitter(cast(Cntlr, cntlr), collected)
        first = ArelleDiagnostic.warning("first")
        second = ArelleDiagnostic.info("second")
        emitter.emit(first)
        emitter.emit(second)
        assert collected == [first, second]
        assert cntlr.logged == [], "collected diagnostics must not also be logged"

    def test_falls_back_to_log_without_a_sink(self) -> None:
        cntlr = StubCntlr()
        emitter = DiagnosticEmitter(cast(Cntlr, cntlr), None)
        emitter.emit(ArelleDiagnostic.warning("orphan"))
        assert cntlr.logged == [("orphan", logging.WARNING)]

    def emitted(
        self, diagnostic: ArelleDiagnostic, definitions: dict[str, str]
    ) -> ArelleDiagnostic:
        collected: list[ArelleDiagnostic] = []
        emitter = DiagnosticEmitter(
            cast(Cntlr, StubCntlr()), collected, elrDefinition=definitions.get
        )
        emitter.emit(diagnostic)
        (only,) = collected
        return only

    def test_fills_elr_definition_from_resolver(self) -> None:
        collected = self.emitted(
            ArelleDiagnostic.warning("w", elr="https://e.com/elr"),
            {"https://e.com/elr": "[100] General"},
        )
        assert collected.elrDefinition == "[100] General"

    def test_keeps_an_elr_definition_already_given(self) -> None:
        given = replace(
            ArelleDiagnostic.warning("w", elr="https://e.com/elr"),
            elrDefinition="given",
        )
        collected = self.emitted(given, {"https://e.com/elr": "[100] General"})
        assert collected.elrDefinition == "given"

    def test_without_elr_or_definition_leaves_it_unset(self) -> None:
        assert self.emitted(ArelleDiagnostic.warning("w"), {}).elrDefinition is None
        unknown = ArelleDiagnostic.warning("w", elr="https://e.com/unknown")
        assert self.emitted(unknown, {}).elrDefinition is None
