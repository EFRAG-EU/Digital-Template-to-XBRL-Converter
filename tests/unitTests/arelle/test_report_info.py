"""Unit tests for report_info.py's ArelleReportProcessor helpers.

The Session-driving methods themselves (validateReportPackage, ...) are
exercised end-to-end by the integration tests; these tests cover the
option-building and response-handling helpers.
"""

import zipfile
from io import BytesIO
from pathlib import Path
from typing import Any, BinaryIO

import pytest
from arelle.CntlrCmdLine import RuntimeOptions

from mireport.arelle.report_info import (
    INLINE_DOCUMENT_SET_PLUGIN,
    ArelleReportProcessor,
    _filesFromResponseZip,
    _singleFileFromResponseZip,
)
from mireport.arelle.support import ArelleProcessingResult, ArelleRelatedException
from mireport.filesupport import FilelikeAndFileName


def makeZip(entries: dict[str, bytes]) -> BytesIO:
    stream = BytesIO()
    with zipfile.ZipFile(stream, "w") as zf:
        for name, content in entries.items():
            zf.writestr(name, content)
    return stream


class TestSingleFileFromResponseZip:
    def test_returns_single_entry_content(self) -> None:
        stream = makeZip({"foo.json": b'{"documentInfo": {}}'})
        assert _singleFileFromResponseZip(stream, "test") == b'{"documentInfo": {}}'

    def test_rewinds_before_reading(self) -> None:
        stream = makeZip({"foo.json": b"content"})
        stream.seek(0, 2)  # simulate a fully-written, unrewound stream
        assert _singleFileFromResponseZip(stream, "test") == b"content"

    def test_raises_on_empty_zip(self) -> None:
        with pytest.raises(ArelleRelatedException, match="test thing"):
            _singleFileFromResponseZip(makeZip({}), "test thing")

    def test_raises_on_multiple_entries(self) -> None:
        """xBRL-JSON is still one file per report; more than one is a bug."""
        stream = makeZip({"foo.json": b"{}", "viewer.html": b"<html/>"})
        with pytest.raises(ArelleRelatedException, match="viewer.html"):
            _singleFileFromResponseZip(stream, "test thing")


class TestFilesFromResponseZip:
    """The viewer emits one file per document set member, so its reader takes
    however many there are — in order, because entry 0 is the one the viewer
    injects its script and data into (iXBRLViewer.py:657)."""

    def test_single_entry(self) -> None:
        files = _filesFromResponseZip(makeZip({"a.html": b"<html/>"}), "test")
        assert [(f.filename, f.fileContent) for f in files] == [("a.html", b"<html/>")]

    def test_preserves_zip_order(self) -> None:
        stream = makeZip(
            {"report.html": b"one", "annex1.html": b"two", "annex2.html": b"three"}
        )
        files = _filesFromResponseZip(stream, "test")
        assert [f.filename for f in files] == [
            "report.html",
            "annex1.html",
            "annex2.html",
        ]
        assert files[0].fileContent == b"one"

    def test_rewinds_before_reading(self) -> None:
        stream = makeZip({"a.html": b"content"})
        stream.seek(0, 2)
        assert _filesFromResponseZip(stream, "test")[0].fileContent == b"content"

    def test_raises_on_empty_zip(self) -> None:
        with pytest.raises(ArelleRelatedException, match="test thing"):
            _filesFromResponseZip(makeZip({}), "test thing")


class TestMakeOptions:
    def makeProcessor(self, **kwargs: Any) -> ArelleReportProcessor:
        return ArelleReportProcessor(
            taxonomyPackages=[Path("a.zip"), Path("b.zip")], **kwargs
        )

    def test_shared_defaults(self) -> None:
        options = self.makeProcessor()._makeOptions()
        assert options.internetConnectivity == "offline"
        assert options.keepOpen is True
        assert options.logFile == "logToBuffer"
        assert options.logFormat == "%(message)s"
        assert options.logPropagate is False
        assert options.packages == ["a.zip", "b.zip"]
        assert options.validate is True
        assert options.calcs == "c11r"
        assert options.utrValidate is True
        assert options.validateDuplicateFacts == "inconsistent"
        assert options.showOptions is False

    def test_online_when_not_offline(self) -> None:
        options = self.makeProcessor(workOffline=False)._makeOptions()
        assert options.internetConnectivity == "online"

    def test_overrides(self) -> None:
        options = self.makeProcessor()._makeOptions(
            calcs="none",
            plugins=["ixbrl-viewer"],
            pluginOptions={"viewerURL": "viewer.js"},
        )
        assert options.calcs == "none"
        # RuntimeOptions flattens pluginOptions into attributes via setattr
        assert options.viewerURL == "viewer.js"


class TestPlugins:
    """Each run enables exactly the plug-ins it needs. The document set plug-in
    goes on any run that may load Inline XBRL: our packages put the report in a
    subdirectory of reports/ once it has document set members, and Arelle refuses
    to load a multi-file report entry without it."""

    def runPlugins(
        self,
        monkeypatch: pytest.MonkeyPatch,
        call: str,
        source: FilelikeAndFileName,
        response: BytesIO | None = None,
    ) -> tuple[list[str], ArelleProcessingResult]:
        """The plug-ins `call` runs Arelle with, and its result; Arelle itself is
        not run, and `response` stands in for what it would write."""
        seen: list[RuntimeOptions] = []

        def fakeRun(
            source: FilelikeAndFileName,
            options: RuntimeOptions,
            responseZipStream: BinaryIO | None = None,
        ) -> ArelleProcessingResult:
            seen.append(options)
            if response is not None and responseZipStream is not None:
                responseZipStream.write(response.getvalue())
            return ArelleProcessingResult()

        processor = ArelleReportProcessor()
        monkeypatch.setattr(processor, "_run", fakeRun)
        result = getattr(processor, call)(source)
        (options,) = seen
        return (options.plugins.split("|") if options.plugins else []), result

    PACKAGE = FilelikeAndFileName(fileContent=b"", filename="report.zip")

    def test_none_unless_asked_for(self) -> None:
        assert ArelleReportProcessor()._makeOptions().plugins is None

    def test_pipe_separated_for_several_plugins(self) -> None:
        options = ArelleReportProcessor()._makeOptions(plugins=["a", "b"])
        assert options.plugins == "a|b"

    def test_validating_a_package_enables_the_document_set_plugin(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        plugins, _ = self.runPlugins(monkeypatch, "validateReportPackage", self.PACKAGE)
        assert plugins == [INLINE_DOCUMENT_SET_PLUGIN]

    def test_the_viewer_enables_the_document_set_plugin(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        plugins, _ = self.runPlugins(monkeypatch, "generateInlineViewer", self.PACKAGE)
        assert plugins == [INLINE_DOCUMENT_SET_PLUGIN, "ixbrl-viewer"]

    def test_validating_xbrl_json_needs_no_plugin(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        document = FilelikeAndFileName(fileContent=b"{}", filename="report.json")
        plugins, _ = self.runPlugins(monkeypatch, "validateXbrlJson", document)
        assert plugins == []

    def test_generating_xbrl_json_saves_arelles_json_of_the_report(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        plugins, result = self.runPlugins(
            monkeypatch,
            "generateXbrlJson",
            self.PACKAGE,
            makeZip({"report.json": b"{}"}),
        )
        assert plugins == [INLINE_DOCUMENT_SET_PLUGIN, "saveLoadableOIM"]
        assert result.has_json
        assert result.xbrl_json.filename == "report.json"
        assert result.xbrl_json.fileContent == b"{}"

    def test_generating_xbrl_json_records_a_missing_json(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _, result = self.runPlugins(
            monkeypatch, "generateXbrlJson", self.PACKAGE, makeZip({})
        )
        assert not result.has_json
        assert result.has_exceptions
