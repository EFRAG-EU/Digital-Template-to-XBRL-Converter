import logging
import os
import sys
import warnings
from argparse import ArgumentParser
from glob import glob
from typing import Any

import rich.traceback
from rich import box
from rich.console import Console
from rich.logging import RichHandler
from rich.prompt import Prompt
from rich.table import Table
from rich.text import Text

from mireport.exceptions import TaxonomyPackageException
from mireport.taxonomy_package import PackageEntryPoint, entryPointsFromPackage

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

    if not all(os.path.exists(taxonomy_zip) for taxonomy_zip in taxonomy_zips):
        raise parser.error(f"Not all specified files found: {taxonomy_zips}")
    elif not all(taxonomy_zip.endswith(".zip") for taxonomy_zip in taxonomy_zips):
        raise parser.error(f"Not all specified files are Zip files: {taxonomy_zips}")
    return taxonomy_zips


def getEntryPointsFromPackages(
    taxonomy_zips: list[str], parser: ArgumentParser
) -> list[PackageEntryPoint]:
    """Read the entry points declared by each package, warning about (but not
    failing on) any package we can't read."""
    entryPoints: list[PackageEntryPoint] = []
    seen: set[tuple[str, ...]] = set()
    for taxonomy_zip in taxonomy_zips:
        try:
            found = entryPointsFromPackage(taxonomy_zip)
        except TaxonomyPackageException as e:
            warning = Text("Skipping ", style="yellow")
            warning.append(f"{taxonomy_zip}: {e}", style="none")
            console_print(warning)
            continue
        for entryPoint in found:
            # Two packages can declare the same entry point.
            if entryPoint.hrefs not in seen:
                seen.add(entryPoint.hrefs)
                entryPoints.append(entryPoint)
    if not entryPoints:
        raise parser.error(
            f"No taxonomy entry points declared by any of: {taxonomy_zips}"
        )
    return entryPoints


def printEntryPointTable(entryPoints: list[PackageEntryPoint]) -> None:
    """List one row per entry point, noting how many documents each names."""
    showPackage = len({ep.packagePath for ep in entryPoints}) > 1

    table = Table(show_header=True, box=box.SIMPLE)
    table.add_column("#", style="bold cyan", justify="right", no_wrap=True)
    table.add_column("Entry point")
    table.add_column("URL", overflow="fold")
    if showPackage:
        table.add_column("Package")
    for num, entryPoint in enumerate(entryPoints, start=1):
        # Text() not markup: entry point names contain things like "[en]".
        url = Text(entryPoint.hrefs[0], style="cyan")
        if (extra := len(entryPoint.hrefs) - 1) > 0:
            url.append(f" (+{extra} more)", style="dim")
        row = [Text(str(num)), Text(entryPoint.name), url]
        if showPackage:
            row.append(Text(entryPoint.packagePath.name, style="dim"))
        table.add_row(*row)
    console_print(table)


def pickEntryPointFromPackages(
    taxonomy_zips: list[str], parser: ArgumentParser
) -> tuple[str, ...]:
    """Show the entry points declared by the given packages and prompt for one.

    Returns every document the chosen entry point names.
    """
    entryPoints = getEntryPointsFromPackages(taxonomy_zips, parser)
    printEntryPointTable(entryPoints)

    byNumber = {str(num): ep for num, ep in enumerate(entryPoints, start=1)}
    byHref = {ep.hrefs[0]: ep for ep in entryPoints}
    if len(entryPoints) == 1:
        response = Prompt.ask("Number or URL", default=entryPoints[0].hrefs[0]).strip()
    else:
        response = Prompt.ask("Number or URL").strip()

    chosen = byNumber.get(response) or byHref.get(response)
    if chosen is None:
        raise parser.error(f"{response!r} is not one of the listed entry points.")
    return chosen.hrefs
