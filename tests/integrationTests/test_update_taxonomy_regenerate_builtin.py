"""End-to-end check of scripts/update-taxonomy.py --regenerate-builtin --dry-run.

Regenerates every built-in taxonomy JSON from the tests' taxonomy packages
(tests/offline_arelle.py), every set of them, and expects each to be reported
unchanged, without the checked-in files being touched.
"""

import os
import subprocess
import sys
from pathlib import Path

import pytest
from tests.offline_arelle import (
    PUBLIC_PACKAGES_DIFFER,
    curatedPackagesDir,
    taxonomyPackagesDir,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
TAXONOMY_DATA_DIR = REPO_ROOT / "src" / "mireport" / "data" / "taxonomies"


@pytest.mark.slow
@pytest.mark.integration
def test_regenerate_builtin_dry_run_reports_all_unchanged() -> None:
    # One curated directory stands in for both sets, so pass it once.
    packageDirs = sorted(
        {str(taxonomyPackagesDir(s)) for s in ("default", "vsme-2024-12-17")}
    )

    mayChange = set() if curatedPackagesDir() else set(PUBLIC_PACKAGES_DIFFER)

    builtIn = sorted(TAXONOMY_DATA_DIR.glob("*.json"))
    before = {p: p.read_bytes() for p in builtIn}

    result = subprocess.run(
        [
            sys.executable,
            str(REPO_ROOT / "scripts" / "update-taxonomy.py"),
            "--regenerate-builtin",
            "--dry-run",
            *packageDirs,
        ],
        capture_output=True,
        encoding="utf-8",
        env={"COLUMNS": "200", **os.environ},
        check=False,
    )
    output = f"STDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"

    assert (result.returncode == 0) == (not mayChange), output
    for path in builtIn:
        assert path.read_bytes() == before[path], f"{path.name} was modified"
        # The last mention is the summary table row (earlier ones are progress).
        summaryRow = next(
            (l for l in reversed(result.stdout.splitlines()) if path.name in l), ""
        )
        status = "would change" if path.name in mayChange else "unchanged"
        assert status in summaryRow, output
    assert not list(TAXONOMY_DATA_DIR.glob(".regenerate-*")), "temp dir left behind"
