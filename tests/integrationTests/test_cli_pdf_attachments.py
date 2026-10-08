"""The CLI's --extra-data "pdfAttachments" key, end to end through the script.

Driven as a subprocess, the way tests/integrationTests/test_expectedFactCounts.py
drives it, because that is the only way to exercise the argument parsing and JSON
wiring as a user meets them.

No pdf2htmlEX needed: with no converter installed the PDFs are still attached,
which is the behaviour under test here. The real-binary path is covered by
test_real_pdf2htmlex.py.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

SAMPLE = Path("tests/data/VSME-Digital-Template-Sample-1.2.0.xlsx").resolve()
PDF_BYTES = b"%PDF-1.7\n1 0 obj\n<< /Type /Catalog >>\nendobj\n%%EOF\n"

pytestmark = pytest.mark.slow


def runCli(outputDir: Path, extraData: Path | None) -> subprocess.CompletedProcess:
    argv = [
        sys.executable,
        "scripts/parse-and-ixbrl.py",
        "--skip-validation",
        str(SAMPLE),
        str(outputDir),
    ]
    if extraData is not None:
        argv.extend(["--extra-data", str(extraData)])
    return subprocess.run(argv, capture_output=True, encoding="utf-8", check=False)


def writeExtraData(directory: Path, paths: list[str]) -> Path:
    for name in paths:
        (directory / name).parent.mkdir(parents=True, exist_ok=True)
        (directory / name).write_bytes(PDF_BYTES)
    extra = directory / "extra.json"
    extra.write_text(
        json.dumps({"pdfAttachments": [{"path": p} for p in paths]}),
        encoding="utf-8",
    )
    return extra


def packageNames(outputDir: Path) -> list[str]:
    zips = list(outputDir.glob("*.zip"))
    assert len(zips) == 1, [p.name for p in zips]
    with zipfile.ZipFile(zips[0]) as zf:
        return zf.namelist()


class TestPdfAttachments:
    def test_single_pdf_is_attached(self, tmp_path: Path) -> None:
        extra = writeExtraData(tmp_path, ["annex1.pdf"])
        out = tmp_path / "out"
        result = runCli(out, extra)
        assert result.returncode == 0, result.stdout + result.stderr
        assert any(n.endswith("/attachments/annex1.pdf") for n in packageNames(out)), (
            packageNames(out)
        )

    def test_several_pdfs_are_all_attached(self, tmp_path: Path) -> None:
        extra = writeExtraData(tmp_path, ["a.pdf", "b.pdf", "c.pdf"])
        out = tmp_path / "out"
        assert runCli(out, extra).returncode == 0
        assert sum("/attachments/" in n for n in packageNames(out)) == 3

    def test_paths_resolve_relative_to_the_extra_data_file(
        self, tmp_path: Path
    ) -> None:
        extra = writeExtraData(tmp_path, ["nested/annex1.pdf"])
        out = tmp_path / "out"
        assert runCli(out, extra).returncode == 0
        assert any(n.endswith("/attachments/annex1.pdf") for n in packageNames(out))

    def test_missing_pdf_is_reported_not_silently_skipped(self, tmp_path: Path) -> None:
        extra = tmp_path / "extra.json"
        extra.write_text(json.dumps({"pdfAttachments": [{"path": "nope.pdf"}]}))
        result = runCli(tmp_path / "out", extra)
        assert result.returncode != 0
        assert "nope.pdf" in (result.stdout + result.stderr)

    @pytest.mark.skipif(
        shutil.which("pdf2htmlEX") is not None,
        reason="pdf2htmlEX is installed, so conversion is expected to succeed",
    )
    def test_without_a_converter_the_report_layout_is_unchanged(
        self, tmp_path: Path
    ) -> None:
        """No document set members means the report stays directly in reports/."""
        extra = writeExtraData(tmp_path, ["annex1.pdf"])
        out = tmp_path / "out"
        assert runCli(out, extra).returncode == 0
        names = packageNames(out)
        assert any(n.count("/") == 2 and "/reports/" in n for n in names), names


class TestWithoutPdfAttachments:
    def test_package_is_unchanged(self, tmp_path: Path) -> None:
        """The feature must be invisible when unused."""
        out = tmp_path / "out"
        assert runCli(out, None).returncode == 0
        names = packageNames(out)
        assert not any("/attachments/" in n for n in names)
        assert sum("/reports/" in n for n in names) == 1
        assert any(n.count("/") == 2 and "/reports/" in n for n in names), names
