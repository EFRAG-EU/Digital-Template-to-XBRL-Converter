"""Unit tests for the taxonomy-info Arelle plugin harness.

The extraction logic itself is tested in test_taxonomy_extraction.py; the
end-to-end plugin run is covered by
tests/integrationTests/test_taxonomy_info_regeneration.py.
"""

from __future__ import annotations

import logging
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest

from mireport.arelle import taxonomy_info
from mireport.arelle.taxonomy_info import TaxonomyInfoPluginData


class TestTaxonomyInfoPluginData:
    def test_instances_have_independent_taxonomy_dicts(self) -> None:
        first = TaxonomyInfoPluginData("first")
        second = TaxonomyInfoPluginData("second")
        first.Taxonomy["concepts"] = {"vsme:Thing": {}}
        assert second.Taxonomy == {}
        assert first.Taxonomy is not second.Taxonomy

    def test_instances_have_independent_utr_dicts(self) -> None:
        first = TaxonomyInfoPluginData("first")
        second = TaxonomyInfoPluginData("second")
        first.UTR["utr"] = [{"unitId": "kg"}]
        assert second.UTR == {}
        assert first.UTR is not second.UTR


class StubCntlr:
    def __init__(self) -> None:
        self.pluginData: Any = None
        self.logged: list[tuple[str, int]] = []

    def getPluginData(self, name: str) -> Any:
        return self.pluginData

    def setPluginData(self, data: Any) -> None:
        self.pluginData = data

    def addToLog(self, message: str, level: int = logging.INFO, **kwargs: Any) -> None:
        self.logged.append((message, level))


class StubExtractor:
    def __init__(self, hasErrors: bool) -> None:
        self.diagnostics = SimpleNamespace(hasErrors=hasErrors)

    def extract(self) -> dict[str, Any]:
        return {"concepts": {"vsme:Thing": {}}}


class TestRunTaxonomyInfo:
    def run(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
        *,
        hasErrors: bool,
        writeDataDespiteErrors: bool | None = None,
    ) -> tuple[StubCntlr, Path]:
        monkeypatch.setattr(
            taxonomy_info,
            "TaxonomyInfoExtractor",
            lambda *args, **kwargs: StubExtractor(hasErrors),
        )
        jsonPath = tmp_path / "taxonomy.json"
        cntlr = StubCntlr()
        options = SimpleNamespace(taxonomyDataFile=str(jsonPath), utrValidate=False)
        if writeDataDespiteErrors is not None:
            options.writeDataDespiteErrors = writeDataDespiteErrors
        taxonomy_info.runTaxonomyInfo(
            cast(Any, cntlr), cast(Any, options), cast(Any, None)
        )
        return cntlr, jsonPath

    def test_writes_the_json_when_there_are_no_errors(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        _, jsonPath = self.run(monkeypatch, tmp_path, hasErrors=False)
        assert jsonPath.exists()

    def test_withholds_the_json_when_extraction_reported_errors(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        cntlr, jsonPath = self.run(monkeypatch, tmp_path, hasErrors=True)
        assert not jsonPath.exists()
        assert cntlr.pluginData.Taxonomy == {}
        errors = [m for m, level in cntlr.logged if level == logging.ERROR]
        assert len(errors) == 1
        assert "not written" in errors[0]

    def test_writes_the_json_despite_errors_when_asked(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        # For a caller that stages the file and decides for itself whether to
        # publish it, so that it can still check the data and report on it.
        cntlr, jsonPath = self.run(
            monkeypatch, tmp_path, hasErrors=True, writeDataDespiteErrors=True
        )
        assert jsonPath.exists()
        assert not [m for m, level in cntlr.logged if level == logging.ERROR]

    def test_still_withholds_when_despite_errors_is_explicitly_off(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        _, jsonPath = self.run(
            monkeypatch, tmp_path, hasErrors=True, writeDataDespiteErrors=False
        )
        assert not jsonPath.exists()
