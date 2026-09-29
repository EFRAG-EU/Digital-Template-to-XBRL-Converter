"""End-to-end check of scripts/update-taxonomy.py --regenerate-builtin --dry-run.

Regenerates every built-in taxonomy JSON from the packages in the sibling
webapp_taxonomies directory and expects each to be reported unchanged, without
the checked-in files being touched. Skips when the packages are not available.
"""

import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
TAXONOMY_DATA_DIR = REPO_ROOT / "src" / "mireport" / "data" / "taxonomies"
PACKAGES_DIR = REPO_ROOT.parent / "webapp_taxonomies"


@pytest.mark.slow
@pytest.mark.integration
def test_regenerate_builtin_dry_run_reports_all_unchanged() -> None:
    taxonomy_zips = sorted(str(p) for p in PACKAGES_DIR.glob("*.zip"))
    if not taxonomy_zips:
        pytest.skip(f"No taxonomy packages available in {PACKAGES_DIR}")

    builtIn = sorted(TAXONOMY_DATA_DIR.glob("*.json"))
    before = {p: p.read_bytes() for p in builtIn}

    result = subprocess.run(
        [
            sys.executable,
            str(REPO_ROOT / "scripts" / "update-taxonomy.py"),
            "--regenerate-builtin",
            "--dry-run",
            *taxonomy_zips,
        ],
        capture_output=True,
        encoding="utf-8",
        env={"COLUMNS": "200", **os.environ},
        check=False,
    )
    output = f"STDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"

    assert result.returncode == 0, output
    for path in builtIn:
        assert path.read_bytes() == before[path], f"{path.name} was modified"
        # The last mention is the summary table row (earlier ones are progress).
        summaryRow = next(
            (l for l in reversed(result.stdout.splitlines()) if path.name in l), ""
        )
        assert "unchanged" in summaryRow, output
    assert not list(TAXONOMY_DATA_DIR.glob(".regenerate-*")), "temp dir left behind"
