"""builtInTaxonomyJsonPaths(): the on-disk built-in taxonomy JSON files and the
entry point each one records, as used by update-taxonomy.py --regenerate-builtin."""

import json
from pathlib import Path

import pytest

import mireport.taxonomy
from mireport.exceptions import TaxonomyException
from mireport.taxonomy import builtInTaxonomyJsonPaths

TAXONOMY_DATA_DIR = (
    Path(__file__).resolve().parents[2] / "src" / "mireport" / "data" / "taxonomies"
)


def useJsonFiles(monkeypatch: pytest.MonkeyPatch, directory: Path) -> None:
    monkeypatch.setattr(
        mireport.taxonomy,
        "getJsonFiles",
        lambda _module: (p for p in directory.iterdir() if p.suffix == ".json"),
    )


def writeJson(path: Path, bits: dict) -> Path:
    path.write_text(json.dumps(bits), encoding="utf-8")
    return path


def test_builtin_files_and_entry_points() -> None:
    found = builtInTaxonomyJsonPaths()

    assert [(path.name, entryPoint) for path, entryPoint in found] == [
        (
            f"vsme-{date}.json",
            f"https://xbrl.efrag.org/taxonomy/vsme/{date}/vsme-all.xsd",
        )
        for date in ("2024-12-17", "2025-07-30", "2026-02-01", "2026-05-01")
    ]
    for path, _ in found:
        assert path.parent == TAXONOMY_DATA_DIR
        assert path.is_file()


def test_sorted_by_file_name(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    writeJson(tmp_path / "b.json", {"entryPoint": "https://example.com/b.xsd"})
    writeJson(tmp_path / "a.json", {"entryPoint": "https://example.com/a.xsd"})
    useJsonFiles(monkeypatch, tmp_path)

    assert builtInTaxonomyJsonPaths() == [
        (tmp_path / "a.json", "https://example.com/a.xsd"),
        (tmp_path / "b.json", "https://example.com/b.xsd"),
    ]


def test_missing_entry_point_raises(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    writeJson(tmp_path / "bad.json", {"concepts": {}})
    useJsonFiles(monkeypatch, tmp_path)

    with pytest.raises(TaxonomyException, match="bad.json"):
        builtInTaxonomyJsonPaths()


@pytest.mark.parametrize("entryPoint", [42, ["https://example.com/a.xsd"], "", "  "])
def test_non_string_or_blank_entry_point_raises(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, entryPoint: object
) -> None:
    writeJson(tmp_path / "bad.json", {"entryPoint": entryPoint})
    useJsonFiles(monkeypatch, tmp_path)

    with pytest.raises(TaxonomyException, match="bad.json"):
        builtInTaxonomyJsonPaths()


def test_non_filesystem_resource_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    class ZippedResource:
        name = "vsme.json"

        def read_bytes(self) -> bytes:
            return b'{"entryPoint": "https://example.com/a.xsd"}'

    monkeypatch.setattr(
        mireport.taxonomy, "getJsonFiles", lambda _module: iter([ZippedResource()])
    )

    with pytest.raises(TaxonomyException, match="vsme.json"):
        builtInTaxonomyJsonPaths()
