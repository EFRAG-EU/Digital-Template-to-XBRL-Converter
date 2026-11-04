"""Arelle as the tests use it: offline, with the taxonomy packages that
taxonomy-packages/fetch-external-packages.py downloads, so no test waits on the network.

The packages come in sets, one directory each, as Arelle warns about packages
that overlap: "default" for most reports, "vsme-2024-12-17" for the 1.0.x ones.

Set $TAXONOMY_PACKAGE_DIR to a curated directory of non-overlapping packages to
use it instead, for every set, as the web app uses its own; the tests only ever
read it."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from mireport.arelle.report_info import ArelleReportProcessor

TAXONOMY_PACKAGES_ROOT = Path(__file__).resolve().parent / "taxonomy-packages"

# Built-in taxonomy JSON that only a curated directory regenerates unchanged, and why.
PUBLIC_PACKAGES_DIFFER = {
    "vsme-2024-12-17.json": (
        "The public May 2025 package bundles an earlier NACE 2025-01-01, with one more "
        "concept (nace:SEC1AllESRSSec1SectorsNA) than the one the JSON was made from."
    ),
}


def curatedPackagesDir() -> Path | None:
    curated = os.environ.get("TAXONOMY_PACKAGE_DIR")
    return Path(curated) if curated else None


def taxonomyPackagesDir(packageSet: str = "default") -> Path:
    """The directory of one set of packages; fails the test if it has none."""
    directory = curatedPackagesDir() or TAXONOMY_PACKAGES_ROOT / packageSet
    if not any(directory.glob("*.zip")):
        pytest.fail(
            f"No taxonomy packages in {directory}: set TAXONOMY_PACKAGE_DIR to a "
            "directory of them, or run tests/taxonomy-packages/fetch-external-packages.py."
        )
    return directory


def offlineArelle(packageSet: str = "default") -> ArelleReportProcessor:
    return ArelleReportProcessor(
        taxonomyPackages=ArelleReportProcessor.getTaxonomyPackagesFromDir(
            taxonomyPackagesDir(packageSet)
        ),
        workOffline=True,
    )
