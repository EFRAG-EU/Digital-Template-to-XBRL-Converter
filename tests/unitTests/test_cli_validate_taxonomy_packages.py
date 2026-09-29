"""validateTaxonomyPackages(): every package glob must match, and every match
must be a zip -- a mistyped path must never silently drop out of the list."""

from argparse import ArgumentParser
from pathlib import Path

import pytest

from mireport.cli import validateTaxonomyPackages


def touch(path: Path) -> str:
    path.write_bytes(b"")
    return str(path)


def errorMessage(capsys: pytest.CaptureFixture[str], globs: list[str]) -> str:
    with pytest.raises(SystemExit):
        validateTaxonomyPackages(globs, ArgumentParser(prog="prog"))
    return capsys.readouterr().err


def test_zips_and_globs_expand(tmp_path: Path) -> None:
    a = touch(tmp_path / "a.zip")
    b = touch(tmp_path / "b.zip")

    found = validateTaxonomyPackages(
        [str(tmp_path / "*.zip")], ArgumentParser(prog="prog")
    )

    assert sorted(found) == [a, b]


def test_path_matching_nothing_is_an_error_naming_it(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    a = touch(tmp_path / "a.zip")
    missing = str(tmp_path / "missing.zip")

    err = errorMessage(capsys, [a, missing])

    assert missing in err
    assert a not in err


def test_glob_matching_nothing_is_an_error(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    pattern = str(tmp_path / "*.zip")

    assert pattern in errorMessage(capsys, [pattern])


def test_non_zip_is_an_error_naming_only_it(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    a = touch(tmp_path / "a.zip")
    notZip = touch(tmp_path / "taxonomy.json")

    err = errorMessage(capsys, [notZip, a])

    assert notZip in err
    assert a not in err
