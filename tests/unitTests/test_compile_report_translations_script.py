"""scripts/compile-report-translations.py: what it compiles. tests/conftest.py compiles the
checkout's catalogs before the tests run."""

from __future__ import annotations

import importlib.util
from io import BytesIO
from pathlib import Path
from types import ModuleType

import pytest
from babel.messages.catalog import Catalog
from babel.messages.pofile import write_po
from babel.support import Translations

SCRIPT = (
    Path(__file__).resolve().parents[2] / "scripts" / "compile-report-translations.py"
)


@pytest.fixture(scope="module")
def script() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "compile_report_translations_script", SCRIPT
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_a_fuzzy_entry_is_left_out(script: ModuleType, tmp_path: Path) -> None:
    catalog = Catalog(locale="de")
    catalog.add("Report generated", "Bericht erstellt")
    catalog.add("Introduction", "Einführung", flags={"fuzzy"})
    po = tmp_path / "po" / "vsme" / "de.po"
    po.parent.mkdir(parents=True)
    with po.open("wb") as f:
        write_po(f, catalog)

    (mo,) = script.compileCatalogs(tmp_path / "po", tmp_path / "mo")

    assert mo == tmp_path / "mo" / "vsme" / "de.mo"
    translations = Translations(BytesIO(mo.read_bytes()))
    assert translations.gettext("Report generated") == "Bericht erstellt"
    assert translations.gettext("Introduction") == "Introduction"


def test_a_removed_catalog_leaves_nothing_behind(
    script: ModuleType, tmp_path: Path
) -> None:
    stale = tmp_path / "mo" / "vsme" / "xx.mo"
    stale.parent.mkdir(parents=True)
    stale.write_bytes(b"")
    (tmp_path / "po").mkdir()

    assert script.compileCatalogs(tmp_path / "po", tmp_path / "mo") == []
    assert not stale.exists()
