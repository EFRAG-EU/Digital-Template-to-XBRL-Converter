"""Unit tests for support.py's Arelle session/QName support classes."""

import json

import pytest
from arelle.ModelValue import QName

from mireport.arelle.diagnostics import Diagnostic
from mireport.arelle.support import (
    ArelleModelInconsistency,
    ArelleProcessingResult,
    ArelleQNameCanonicaliser,
    ArelleRelatedException,
)
from mireport.conversionresults import MessageType, Severity
from mireport.filesupport import FilelikeAndFileName
from mireport.xml import getBootstrapQNameMaker


def makeJsonLog(*records: tuple[str, str, str]) -> str:
    """Build an Arelle JSON log from (code, level, text) tuples."""
    return json.dumps(
        {
            "log": [
                {"code": code, "level": level, "message": {"text": text}}
                for code, level, text in records
            ]
        }
    )


class TestArelleProcessingResultDiagnostics:
    def test_no_diagnostics_by_default(self) -> None:
        result = ArelleProcessingResult()
        assert result.diagnostics == []

    def test_add_diagnostics_accumulates_in_order(self) -> None:
        result = ArelleProcessingResult()
        first = Diagnostic.warning("first")
        second = Diagnostic.info("second")
        result.addDiagnostics([first])
        result.addDiagnostics([second])
        assert result.diagnostics == [first, second]

    def test_diagnostics_property_returns_copy(self) -> None:
        result = ArelleProcessingResult()
        result.addDiagnostics([Diagnostic.warning("only")])
        result.diagnostics.clear()
        assert len(result.diagnostics) == 1


class TestImportArelleMessages:
    def test_coded_record_becomes_validation_message(self) -> None:
        result = ArelleProcessingResult.fromArelleLogs(
            makeJsonLog(("xbrl.5.2.5.2:calcInconsistency", "error", "Bad calc")), []
        )
        [message] = result.messages
        assert message.messageText == "[xbrl.5.2.5.2:calcInconsistency] Bad calc"
        assert message.severity is Severity.ERROR
        assert message.messageType is MessageType.XbrlValidation

    def test_severity_is_worst_of_code_and_level(self) -> None:
        result = ArelleProcessingResult.fromArelleLogs(
            makeJsonLog(("warning", "info", "Careful now")), []
        )
        [message] = result.messages
        assert message.severity is Severity.WARNING

    def test_blank_code_kept_as_devinfo(self) -> None:
        result = ArelleProcessingResult.fromArelleLogs(
            makeJsonLog(("", "info", "Anything at all")), []
        )
        [message] = result.messages
        assert message.messageText == "Anything at all"
        assert message.severity is Severity.INFO
        assert message.messageType is MessageType.DevInfo

    def test_interesting_info_kept_as_devinfo(self) -> None:
        result = ArelleProcessingResult.fromArelleLogs(
            makeJsonLog(("info", "info", "report.xhtml validated in 1.23 secs")), []
        )
        [message] = result.messages
        assert message.messageType is MessageType.DevInfo

    @pytest.mark.parametrize(
        "text",
        [
            "Activation of package VSME successful",
            "Activation of plug-in Taxonomy Information Extractor",
            "Option foo set",
            "something entirely unexpected",
        ],
    )
    def test_other_info_produces_no_message(self, text: str) -> None:
        result = ArelleProcessingResult.fromArelleLogs(
            makeJsonLog(("info", "info", text)), []
        )
        assert result.messages == []

    def test_log_lines_are_kept(self) -> None:
        result = ArelleProcessingResult.fromArelleLogs(makeJsonLog(), ["one", "two"])
        assert result.log_lines == ["one", "two"]


class TestArelleProcessingResultOutputs:
    def test_xbrl_json_raises_when_absent(self) -> None:
        result = ArelleProcessingResult()
        assert result.has_json is False
        with pytest.raises(ArelleRelatedException):
            _ = result.xbrl_json

    def test_xbrl_json_returns_stored_file(self) -> None:
        result = ArelleProcessingResult()
        stored = FilelikeAndFileName(fileContent=b"{}", filename="report.json")
        result._xbrlJson = stored
        assert result.has_json is True
        assert result.xbrl_json is stored

    def test_duplicate_xBRL_JSON_property_is_gone(self) -> None:
        result = ArelleProcessingResult()
        assert not hasattr(result, "xBRL_JSON")

    def test_viewer_raises_when_absent(self) -> None:
        result = ArelleProcessingResult()
        assert result.has_viewer is False
        with pytest.raises(ArelleRelatedException):
            _ = result.viewer

    def test_has_exceptions_is_a_property(self) -> None:
        result = ArelleProcessingResult()
        assert result.has_exceptions is False
        result.addException(ValueError("boom"), message="Context")
        assert result.has_exceptions is True
        assert any("boom" in m.messageText for m in result.messages)


class TestArelleModelInconsistency:
    def test_from_string(self) -> None:
        exc = ArelleModelInconsistency("plain message")
        assert str(exc) == "plain message"
        assert exc.diagnostic is None

    def test_from_diagnostic(self) -> None:
        diagnostic = Diagnostic.error("Bad shape", elr="https://elr")
        exc = ArelleModelInconsistency(diagnostic)
        assert str(exc) == "Bad shape\n  elr: https://elr"
        assert exc.diagnostic is diagnostic


def makeCanonicaliser() -> ArelleQNameCanonicaliser:
    return ArelleQNameCanonicaliser(getBootstrapQNameMaker())


class TestConvert:
    def test_converts_fully_qualified_qname(self) -> None:
        canonicaliser = makeCanonicaliser()
        converted = canonicaliser.convert(
            QName("vsme", "https://example.com/vsme", "Thing")
        )
        assert str(converted) == "vsme:Thing"

    @pytest.mark.parametrize(
        "qname",
        [
            QName(None, "https://example.com/vsme", "Thing"),
            QName("vsme", None, "Thing"),
        ],
        ids=["no-prefix", "no-namespace"],
    )
    def test_raises_on_incomplete_qname(self, qname: QName) -> None:
        canonicaliser = makeCanonicaliser()
        with pytest.raises(ArelleModelInconsistency):
            canonicaliser.convert(qname)
