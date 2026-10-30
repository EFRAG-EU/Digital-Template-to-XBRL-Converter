"""Arelle as the tests use it: offline, with the taxonomy packages that
taxonomy-packages/fetch-external-packages.py downloads, so no test waits on the network.

The packages come in sets, one directory each, as Arelle warns about packages
that overlap: "default" for most reports, "vsme-2024-12-17" for the 1.0.x ones."""

from __future__ import annotations

from pathlib import Path

import pytest

from mireport.arelle.report_info import ArelleReportProcessor

TAXONOMY_PACKAGES_ROOT = Path(__file__).resolve().parent / "taxonomy-packages"


def taxonomyPackagesDir(packageSet: str = "default") -> Path:
    """The directory of one set of packages; fails the test if it has none."""
    directory = TAXONOMY_PACKAGES_ROOT / packageSet
    if not any(directory.glob("*.zip")):
        pytest.fail(
            f"No taxonomy packages in {directory}: "
            "run tests/taxonomy-packages/fetch-external-packages.py first."
        )
    return directory


def offlineArelle(packageSet: str = "default") -> ArelleReportProcessor:
    return ArelleReportProcessor(
        taxonomyPackages=ArelleReportProcessor.getTaxonomyPackagesFromDir(
            taxonomyPackagesDir(packageSet)
        ),
        workOffline=True,
    )
