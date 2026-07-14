"""Unit tests for the structured taxonomy diagnostics."""

import logging
from typing import Any, cast

import pytest
from arelle.Cntlr import Cntlr
from arelle.ModelValue import QName

from mireport.arelle.diagnostics import (
    Diagnostic,
    DiagnosticCollector,
    DiagnosticEmitter,
    logTo,
)


def qn(local: str) -> QName:
    return QName("vsme", "https://example.com/vsme", local)


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


class StubCntlr:
    def __init__(self) -> None:
        self.logged: list[tuple[str, int]] = []

    def addToLog(self, message: str, level: int = logging.INFO, **kwargs: Any) -> None:
        self.logged.append((message, level))


class TestLogTo:
    def test_passes_formatted_message_and_level(self) -> None:
        cntlr = StubCntlr()
        d = Diagnostic.warning("Presentation is empty", elr="https://elr")
        logTo(cast(Cntlr, cntlr), d)
        assert cntlr.logged == [
            ("Presentation is empty\n  elr: https://elr", logging.WARNING)
        ]


class TestDiagnosticCollector:
    def test_open_close_lifecycle(self) -> None:
        token = DiagnosticCollector.open()
        try:
            assert DiagnosticCollector.exists(token)
        finally:
            assert DiagnosticCollector.close(token) == []
        assert not DiagnosticCollector.exists(token)

    def test_close_pops_and_second_close_raises(self) -> None:
        token = DiagnosticCollector.open()
        DiagnosticCollector.close(token)
        with pytest.raises(KeyError):
            DiagnosticCollector.close(token)

    def test_tokens_are_unique(self) -> None:
        first = DiagnosticCollector.open()
        second = DiagnosticCollector.open()
        try:
            assert first != second
        finally:
            DiagnosticCollector.close(first)
            DiagnosticCollector.close(second)


class TestDiagnosticEmitter:
    def test_collects_when_token_registered(self) -> None:
        cntlr = StubCntlr()
        token = DiagnosticCollector.open()
        try:
            emitter = DiagnosticEmitter(cast(Cntlr, cntlr), token)
            first = Diagnostic.warning("first")
            second = Diagnostic.info("second")
            emitter.emit(first)
            emitter.emit(second)
        finally:
            collected = DiagnosticCollector.close(token)
        assert collected == [first, second]
        assert cntlr.logged == [], "collected diagnostics must not also be logged"

    @pytest.mark.parametrize("token", [None, "no-such-token"], ids=["none", "unknown"])
    def test_falls_back_to_log_without_registered_token(
        self, token: str | None
    ) -> None:
        cntlr = StubCntlr()
        emitter = DiagnosticEmitter(cast(Cntlr, cntlr), token)
        emitter.emit(Diagnostic.warning("orphan"))
        assert cntlr.logged == [("orphan", logging.WARNING)]
