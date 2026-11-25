"""Characterization test for the taxonomy JSON produced by taxonomy_info.py.

Regenerates the taxonomy JSON using the tests' taxonomy packages
(tests/offline_arelle.py) and asserts it is identical to the JSON checked in to
src/mireport/data/taxonomies.
"""

import json
from pathlib import Path

import pytest
from tests.offline_arelle import (
    PUBLIC_PACKAGES_DIFFER,
    curatedPackagesDir,
    taxonomyPackagesDir,
)

from mireport.arelle.report_info import ArelleReportProcessor
from mireport.arelle.taxonomy_info import callArelleForTaxonomyInfo

REPO_ROOT = Path(__file__).resolve().parents[2]
TAXONOMY_DATA_DIR = REPO_ROOT / "src" / "mireport" / "data" / "taxonomies"

# (taxonomy JSON, the set of taxonomy packages to regenerate it with)
TEST_CASES = [
    ("vsme-2024-12-17.json", "vsme-2024-12-17"),
    ("vsme-2025-07-30.json", "default"),
    ("vsme-2026-02-01.json", "default"),
    ("vsme-2026-05-01.json", "default"),
    ("vs-draft-2026-10-16.json", "default"),
]


@pytest.mark.slow
@pytest.mark.integration
@pytest.mark.parametrize(("json_name", "packageSet"), TEST_CASES)
def test_taxonomy_json_regeneration(
    json_name: str, packageSet: str, tmp_path: Path
) -> None:
    if json_name in PUBLIC_PACKAGES_DIFFER and not curatedPackagesDir():
        pytest.skip(PUBLIC_PACKAGES_DIFFER[json_name])
    taxonomy_zips = ArelleReportProcessor.getTaxonomyPackagesFromDir(
        taxonomyPackagesDir(packageSet)
    )

    expected = json.loads((TAXONOMY_DATA_DIR / json_name).read_text(encoding="utf-8"))
    output_path = tmp_path / json_name

    results = callArelleForTaxonomyInfo(
        # Legacy JSON records a single "entryPoint" in place of the set.
        entry_point=expected.get("entryPointSet") or [expected["entryPoint"]],
        taxonomy_zips=taxonomy_zips,
        taxonomy_json_path=str(output_path),
    )

    assert output_path.exists(), (
        "Taxonomy JSON was not written. Arelle log:\n" + "\n".join(results.log_lines)
    )
    actual = json.loads(output_path.read_text(encoding="utf-8"))

    assert sorted(actual.keys()) == sorted(expected.keys())
    for key in expected:
        assert actual[key] == expected[key], f"Mismatch in top-level key {key!r}"
