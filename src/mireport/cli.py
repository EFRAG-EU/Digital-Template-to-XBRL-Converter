import logging
import os
import sys
from argparse import ArgumentParser
from glob import glob
from typing import Any

import rich.traceback
from rich.console import Console
from rich.logging import RichHandler

_CONSOLE = Console()


# Build a translation table for fast emoji deletion.
# Covers: Emoticons, Alchemical, Misc Symbols, Dingbats, and most of the
# Supplemental Symbols & Pictographs, Supplemental Arrows-C, etc.
_EMOJI_DELETE_TABLE = str.maketrans(
    {
        i: None
        for start, end in [
            (0x1F1E0, 0x1F1FF),  # Regional Indicators
            (0x1F300, 0x1F5FF),  # Misc Symbols & Pictographs
            (0x1F600, 0x1F64F),  # Emoticons
            (0x1F680, 0x1F6FF),  # Transport & Map Symbols
            (0x1F700, 0x1F77F),  # Alchemical Symbols
            (0x1F780, 0x1F7FF),  # Geometric Shapes Extended
            (0x1F800, 0x1F8FF),  # Supplemental Arrows-C
            (0x1F900, 0x1F9FF),  # Supplemental Symbols & Pictographs
            (0x1FA00, 0x1FA7F),  # Chess Symbols
            (0x1FA80, 0x1FAFF),  # Symbols and Pictographs Extended-A
            (0x2600, 0x26FF),  # Miscellaneous Symbols
            (0x2700, 0x27BF),  # Dingbats
        ]
        for i in range(start, end + 1)
    }
    | {0xFE0F: None}  # Add variation selector
)


def _strip_emoji(text: str) -> str:
    """Remove emoji from text using fast translation table."""
    return text.translate(_EMOJI_DELETE_TABLE)


def getListofPathsFromListOfGlobs(globs: list[str]) -> list[str]:
    paths = [
        glob_result for glob_candidate in globs for glob_result in glob(glob_candidate)
    ]
    return paths


def configure_utf8_output() -> None:
    """Configure stdout/stderr to use UTF-8 encoding on Windows.

    This ensures emoji and other Unicode characters can be output even when
    writing to pipes or redirected output on Windows systems.
    """
    if sys.platform == "win32":
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")


def get_console() -> Console:
    return _CONSOLE


def console_print(*args: Any, **kwargs: Any) -> None:
    console = get_console()
    if not console.is_terminal:
        args = tuple(_strip_emoji(arg) if isinstance(arg, str) else arg for arg in args)
    console.print(*args, **kwargs)


def configure_rich_output(*, locals_max_length: int | None = None) -> Console:
    configure_utf8_output()
    traceback_kwargs: dict[str, Any] = {"show_locals": False}
    if locals_max_length is not None:
        traceback_kwargs["locals_max_length"] = locals_max_length
    rich.traceback.install(**traceback_kwargs)
    logging.basicConfig(
        format="%(message)s",
        datefmt="[%Y-%m-%d %H:%M:%S]",
        handlers=[RichHandler(rich_tracebacks=True, console=get_console())],
    )
    logging.captureWarnings(True)
    return get_console()


def validateTaxonomyPackages(globList: list[str], parser: ArgumentParser) -> list[str]:
    console_print("Zip files specified", " ".join(globList))
    taxonomy_zips: list[str] = getListofPathsFromListOfGlobs(globList)
    console_print("Zip files to use  ", " ".join(taxonomy_zips))

    if not all([os.path.exists(taxonomy_zip) for taxonomy_zip in taxonomy_zips]):
        raise parser.error(f"Not all specified files found: {taxonomy_zips}")
    elif not all([taxonomy_zip.endswith(".zip") for taxonomy_zip in taxonomy_zips]):
        raise parser.error(f"Not all specified files are Zip files: {taxonomy_zips}")
    return taxonomy_zips
