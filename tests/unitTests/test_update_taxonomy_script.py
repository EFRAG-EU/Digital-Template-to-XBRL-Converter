"""The publish decision in scripts/update-taxonomy.py: the taxonomy JSON is
staged, and only a run with no problems may replace the real file."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

import pytest

from mireport.arelle.diagnostics import ArelleDiagnostic
from mireport.arelle.support import ArelleProcessingResult

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "update-taxonomy.py"


@pytest.fixture(scope="module")
def script() -> ModuleType:
    spec = importlib.util.spec_from_file_location("update_taxonomy_script", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def resultsWith(*diagnostics: ArelleDiagnostic) -> ArelleProcessingResult:
    results = ArelleProcessingResult()
    results.addDiagnostics(diagnostics)
    return results


@pytest.fixture
def staged(tmp_path: Path) -> Path:
    path = tmp_path / "staging" / "taxonomy.json"
    path.parent.mkdir()
    path.write_text('{"new": true}', encoding="utf-8")
    return path


class TestSucceeded:
    def test_clean_run_with_a_file(self, script: ModuleType, staged: Path) -> None:
        assert script.succeeded(resultsWith(), staged)

    def test_warnings_and_info_are_fine(self, script: ModuleType, staged: Path) -> None:
        results = resultsWith(ArelleDiagnostic.warning("w"), ArelleDiagnostic.info("i"))
        assert script.succeeded(results, staged)

    def test_an_error_diagnostic_fails_the_run(
        self, script: ModuleType, staged: Path
    ) -> None:
        results = resultsWith(ArelleDiagnostic.error("e"))
        assert not script.succeeded(results, staged)

    def test_no_file_fails_the_run(self, script: ModuleType, tmp_path: Path) -> None:
        assert not script.succeeded(resultsWith(), tmp_path / "missing.json")


class TestPublishIfSucceeded:
    def test_a_clean_run_replaces_the_target(
        self, script: ModuleType, staged: Path, tmp_path: Path
    ) -> None:
        target = tmp_path / "out" / "taxonomy.json"
        target.parent.mkdir()
        target.write_text('{"old": true}', encoding="utf-8")

        assert script.publishIfSucceeded(resultsWith(), staged, target) is True

        assert target.read_text(encoding="utf-8") == '{"new": true}'
        assert not staged.exists()

    def test_a_failed_run_leaves_no_file_where_there_was_none(
        self, script: ModuleType, staged: Path, tmp_path: Path
    ) -> None:
        target = tmp_path / "out" / "taxonomy.json"
        target.parent.mkdir()
        results = resultsWith(ArelleDiagnostic.error("e"))

        assert script.publishIfSucceeded(results, staged, target) is False

        assert not target.exists()

    def test_a_failed_run_leaves_an_existing_target_alone(
        self, script: ModuleType, staged: Path, tmp_path: Path
    ) -> None:
        target = tmp_path / "taxonomy.json"
        target.write_text('{"old": true}', encoding="utf-8")
        results = resultsWith(ArelleDiagnostic.error("e"))

        assert script.publishIfSucceeded(results, staged, target) is False

        assert target.read_text(encoding="utf-8") == '{"old": true}'
