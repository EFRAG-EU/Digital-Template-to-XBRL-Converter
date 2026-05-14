import logging
import os
import sys
import warnings
from argparse import ArgumentParser
from glob import glob
from typing import Any
import warnings

import rich.traceback
from rich.console import Console
from rich.logging import RichHandler

_CONSOLE = Console()


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
    if sys.platform != "win32":
        return

    for stream in (sys.stdout, sys.stderr):
        # Some test runners and redirected streams may not expose reconfigure().
        if stream is None:
            continue
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            reconfigure(encoding="utf-8", errors="replace")


def get_console() -> Console:
    return _CONSOLE


def console_print(*args: Any, **kwargs: Any) -> None:
    console = get_console()
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
    warnings.filterwarnings("default", category=DeprecationWarning)
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
