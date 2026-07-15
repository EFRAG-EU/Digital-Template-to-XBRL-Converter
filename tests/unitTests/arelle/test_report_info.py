"""Unit tests for report_info.py's ArelleReportProcessor helpers.

The Session-driving methods themselves (validateReportPackage, ...) are
exercised end-to-end by the integration tests; these tests cover the
option-building and response-handling helpers.
"""

import zipfile
from io import BytesIO
from pathlib import Path
from typing import Any

import pytest

from mireport.arelle.report_info import (
    ArelleReportProcessor,
    _singleFileFromResponseZip,
)
from mireport.arelle.support import ArelleRelatedException


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
        stream = makeZip({"foo.json": b"{}", "viewer.html": b"<html/>"})
        with pytest.raises(ArelleRelatedException, match="viewer.html"):
            _singleFileFromResponseZip(stream, "test thing")


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
        assert options.plugins is None

    def test_online_when_not_offline(self) -> None:
        options = self.makeProcessor(workOffline=False)._makeOptions()
        assert options.internetConnectivity == "online"

    def test_overrides(self) -> None:
        options = self.makeProcessor()._makeOptions(
            calcs="none",
            plugins="saveLoadableOIM",
            pluginOptions={"saveLoadableOIM": "out.json"},
        )
        assert options.calcs == "none"
        assert options.plugins == "saveLoadableOIM"
        # RuntimeOptions flattens pluginOptions into attributes via setattr
        assert options.saveLoadableOIM == "out.json"
