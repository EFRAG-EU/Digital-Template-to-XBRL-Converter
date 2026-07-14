"""Characterization test for the taxonomy JSON produced by taxonomy_info.py.

Regenerates the taxonomy JSON using the taxonomy packages in the sibling
webapp_taxonomies directory (which includes the required supporting packages:
codelists, country, nace, waste) and asserts it is identical to the JSON
checked in to src/mireport/data/taxonomies. Skips when the packages are not
available.
"""

import json
from pathlib import Path

import pytest

from mireport.arelle.taxonomy_info import callArelleForTaxonomyInfo

REPO_ROOT = Path(__file__).resolve().parents[2]
TAXONOMY_DATA_DIR = REPO_ROOT / "src" / "mireport" / "data" / "taxonomies"
PACKAGES_DIR = REPO_ROOT.parent / "webapp_taxonomies"

TEST_CASES = [
    "vsme-2024-12-17.json",
    "vsme-2025-07-30.json",
    "vsme-2026-02-01.json",
    "vsme-2026-05-01.json",
]


@pytest.mark.slow
@pytest.mark.integration
@pytest.mark.parametrize("json_name", TEST_CASES)
def test_taxonomy_json_regeneration(json_name: str, tmp_path: Path) -> None:
    taxonomy_zips = sorted(str(p) for p in PACKAGES_DIR.glob("*.zip"))
    if not taxonomy_zips:
        pytest.skip(f"No taxonomy packages available in {PACKAGES_DIR}")

    expected = json.loads((TAXONOMY_DATA_DIR / json_name).read_text(encoding="utf-8"))
    output_path = tmp_path / json_name

    results = callArelleForTaxonomyInfo(
        entry_point=expected["entryPoint"],
        taxonomy_zips=taxonomy_zips,
        taxonomy_json_path=str(output_path),
    )

    assert output_path.exists(), (
        "Taxonomy JSON was not written. Arelle log:\n" + "\n".join(results.logLines)
    )
    actual = json.loads(output_path.read_text(encoding="utf-8"))

    assert sorted(actual.keys()) == sorted(expected.keys())
    for key in expected:
        assert actual[key] == expected[key], f"Mismatch in top-level key {key!r}"
