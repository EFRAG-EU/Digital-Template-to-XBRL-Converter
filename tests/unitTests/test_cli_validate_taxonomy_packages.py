"""validateTaxonomyPackages(): every package glob must match, and every match
must be a zip -- a mistyped path must never silently drop out of the list. The
result is Paths, de-duplicated and sorted so console output is consistent."""

import os
from argparse import ArgumentParser
from pathlib import Path

import pytest

from mireport.cli import packageListLines, validateTaxonomyPackages


def touch(path: Path) -> Path:
    path.write_bytes(b"")
    return path


def validate(globs: list[str]) -> list[Path]:
    return validateTaxonomyPackages(globs, ArgumentParser(prog="prog"))


def errorMessage(capsys: pytest.CaptureFixture[str], globs: list[str]) -> str:
    with pytest.raises(SystemExit):
        validateTaxonomyPackages(globs, ArgumentParser(prog="prog"))
    return capsys.readouterr().err


def test_zips_and_globs_expand(tmp_path: Path) -> None:
    a = touch(tmp_path / "a.zip")
    b = touch(tmp_path / "b.zip")

    assert validate([str(tmp_path / "*.zip")]) == [a, b]


def test_result_is_sorted_by_file_name(tmp_path: Path) -> None:
    (tmp_path / "x").mkdir()
    (tmp_path / "y").mkdir()
    b = touch(tmp_path / "x" / "b.zip")
    a = touch(tmp_path / "y" / "a.zip")

    assert validate([str(b), str(a)]) == [a, b]


def test_overlapping_globs_give_each_package_once(tmp_path: Path) -> None:
    a = touch(tmp_path / "a.zip")
    b = touch(tmp_path / "b.zip")

    assert validate([str(tmp_path / "*.zip"), str(a)]) == [a, b]


def test_zip_suffix_is_case_insensitive(tmp_path: Path) -> None:
    upper = touch(tmp_path / "UPPER.ZIP")

    assert validate([str(upper)]) == [upper]


def test_directory_means_the_zips_directly_in_it(tmp_path: Path) -> None:
    a = touch(tmp_path / "a.zip")
    upper = touch(tmp_path / "B.ZIP")
    touch(tmp_path / "notes.txt")
    (tmp_path / "nested").mkdir()
    touch(tmp_path / "nested" / "deeper.zip")

    assert validate([str(tmp_path)]) == [upper, a]


def test_directory_with_trailing_separator(tmp_path: Path) -> None:
    a = touch(tmp_path / "a.zip")

    assert validate([f"{tmp_path}{os.sep}"]) == [a]


def test_directory_and_its_zips_given_twice_give_each_once(tmp_path: Path) -> None:
    a = touch(tmp_path / "a.zip")

    assert validate([str(tmp_path), str(a), str(tmp_path / "*.zip")]) == [a]


def test_directory_without_zips_is_an_error_naming_it(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    a = touch(tmp_path / "a.zip")
    empty = tmp_path / "empty"
    empty.mkdir()
    touch(empty / "notes.txt")

    err = errorMessage(capsys, [str(a), str(empty)])

    assert str(empty) in err
    assert str(a) not in err


def test_directory_named_like_a_zip_is_searched_like_any_directory(
    tmp_path: Path,
) -> None:
    directory = tmp_path / "unpacked.zip"
    directory.mkdir()
    inner = touch(directory / "inner.zip")

    assert validate([str(directory)]) == [inner]


def test_path_matching_nothing_is_an_error_naming_it(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    a = touch(tmp_path / "a.zip")
    missing = str(tmp_path / "missing.zip")

    err = errorMessage(capsys, [str(a), missing])

    assert missing in err
    assert str(a) not in err


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

    err = errorMessage(capsys, [str(notZip), str(a)])

    assert str(notZip) in err
    assert str(a) not in err


def test_package_lines_print_each_parent_once() -> None:
    webapp = Path("..") / "webapp_taxonomies"
    other = Path("other")

    lines = packageListLines(
        [
            webapp / "a.zip",
            other / "b.zip",
            webapp / "c.zip",
            other / "d.zip",
        ]
    )

    assert lines == [
        f"{webapp}{os.sep}",
        "\ta.zip",
        "\tc.zip",
        f"{other}{os.sep}",
        "\tb.zip",
        "\td.zip",
    ]


def test_package_lines_print_a_lone_file_on_one_line() -> None:
    webapp = Path("..") / "webapp_taxonomies"
    lone = Path("other") / "b.zip"

    lines = packageListLines([webapp / "a.zip", lone, webapp / "c.zip"])

    assert lines == [f"{webapp}{os.sep}", "\ta.zip", "\tc.zip", str(lone)]


def test_package_lines_for_no_packages() -> None:
    assert packageListLines([]) == []


def test_validate_lists_packages_under_their_directory(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    touch(tmp_path / "a.zip")
    touch(tmp_path / "b.zip")

    validate([str(tmp_path / "*.zip")])

    # Rich wraps long lines, so compare with the line breaks taken out.
    out = capsys.readouterr().out.replace("\n", "")
    assert "a.zip" in out and "b.zip" in out
    assert str(tmp_path / "a.zip") not in out
    assert str(tmp_path / "b.zip") not in out


def test_explicit_paths_are_not_echoed_before_expansion(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # What a shell that expands globs itself (bash) hands over.
    validate([str(touch(tmp_path / "a.zip")), str(touch(tmp_path / "b.zip"))])

    assert "Zip globs specified" not in capsys.readouterr().out


def test_patterns_are_echoed_before_expansion(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # What PowerShell hands over: the pattern, unexpanded.
    touch(tmp_path / "a.zip")

    validate([str(tmp_path / "*.zip")])

    assert "Zip globs specified" in capsys.readouterr().out
