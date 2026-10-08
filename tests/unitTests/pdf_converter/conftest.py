"""Stand-in converter executables, so the subprocess layer is really run.

The shim is named after the backend it stands in for, because that is how
``_run._backendFor`` infers which command line to build from an explicit path.
"""

from __future__ import annotations

import os
import stat
import sys
from pathlib import Path

import pytest

from mireport.pdf_converter._backends import (
    BACKENDS_IN_PREFERENCE_ORDER,
    PdfConverterBackend,
)
from mireport.pdf_converter._run import PATH_ENV_VAR

STUB_SCRIPT = Path("tests/data/stub-pdf-converter.py").resolve()


@pytest.fixture(autouse=True)
def _noConfiguredConverter(monkeypatch: pytest.MonkeyPatch) -> None:
    """findConverter reads the environment, which the machine running the
    tests is entitled to have set."""
    monkeypatch.delenv(PATH_ENV_VAR, raising=False)


def _makeShim(directory: Path, name: str) -> Path:
    """An executable called @name that runs the stub script."""
    if os.name == "nt":
        shim = directory / f"{name}.cmd"
        shim.write_text(f'@echo off\r\n"{sys.executable}" "{STUB_SCRIPT}" %*\r\n')
    else:
        shim = directory / name
        shim.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{STUB_SCRIPT}" "$@"\n')
        shim.chmod(shim.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return shim


@pytest.fixture(scope="session")
def stubBinaries(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Path]:
    """One shim per supported backend, keyed by backend name."""
    directory = tmp_path_factory.mktemp("stub-bin")
    return {
        backend.name: _makeShim(directory, backend.executable)
        for backend in BACKENDS_IN_PREFERENCE_ORDER
    }


@pytest.fixture(params=[b.name for b in BACKENDS_IN_PREFERENCE_ORDER])
def backend(request: pytest.FixtureRequest) -> PdfConverterBackend:
    """Runs the test once per supported backend."""
    from mireport.pdf_converter._backends import backendByName

    found = backendByName(request.param)
    assert found is not None
    return found


@pytest.fixture
def stubBinary(backend: PdfConverterBackend, stubBinaries: dict[str, Path]) -> Path:
    """The shim for whichever backend this parametrisation is exercising."""
    return stubBinaries[backend.name]


@pytest.fixture
def behaviour(monkeypatch: pytest.MonkeyPatch):
    """Switch what the stub does. See tests/data/stub-pdf-converter.py."""

    def set(name: str) -> None:
        monkeypatch.setenv("STUB_BEHAVIOUR", name)

    return set


@pytest.fixture
def pdfBytes() -> bytes:
    """Enough of a PDF to be written to disk and handed over."""
    return b"%PDF-1.7\n1 0 obj\n<< /Type /Catalog >>\nendobj\ntrailer\n%%EOF\n"
