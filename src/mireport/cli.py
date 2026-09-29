import logging
import os
import sys
import warnings
from argparse import ArgumentParser
from collections import Counter
from collections.abc import Iterable, Sequence
from glob import escape as escape_glob
from glob import glob
from pathlib import Path
from typing import Any

import rich.traceback
from rich import box
from rich.console import Console
from rich.logging import RichHandler
from rich.markup import escape
from rich.prompt import Prompt
from rich.table import Table
from rich.text import Text

from mireport.diagnostics import AbstractDiagnostic
from mireport.exceptions import TaxonomyPackageException
from mireport.taxonomy import listTaxonomies
from mireport.taxonomy_package import PackageEntryPoint, entryPointsFromPackage

_CONSOLE = Console()

_DIAGNOSTIC_LEVEL_STYLES = {
    logging.ERROR: "bold red",
    logging.WARNING: "yellow",
    logging.INFO: "dim",
}


def expandPackageArgument(pattern: str) -> list[Path]:
    """The paths one package argument stands for: a glob (PowerShell passes
    them through unexpanded) or path, where a directory means the zips
    directly in it. glob.glob() rather than Path.glob(): the latter rejects
    absolute patterns."""
    paths: list[Path] = []
    for match in map(Path, glob(pattern)):
        if match.is_dir():
            paths.extend(
                p for p in match.iterdir() if p.is_file() and p.suffix.lower() == ".zip"
            )
        else:
            paths.append(match)
    return paths


def getListofPathsFromListOfGlobs(globs: list[str]) -> list[Path]:
    """Expand package arguments into unique paths, sorted by file name then
    path so console output is consistent."""
    # A set, not unique_list(): the result is sorted anyway, and unique_list
    # lives behind the Arelle boundary.
    paths = {path for pattern in globs for path in expandPackageArgument(pattern)}
    return sorted(paths, key=lambda path: (path.name, str(path)))


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


def console_print_plain(lines: Iterable[object], indent: str = "\t") -> None:
    """Print lines whose square brackets are data, not rich markup.

    Arelle log lines and mireport messages contain message codes and QName lists
    like "[xbrldie:PrimaryItemDimensionallyInvalidError]", which rich would
    otherwise swallow as a style tag.
    """
    console_print(Text("\n".join(f"{indent}{line}" for line in lines)))


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


def packageListLines(paths: Sequence[Path]) -> list[str]:
    """Lines listing paths grouped by directory: each directory once, then
    (tab-indented) the name of every file in it, in the order given. A file
    alone in its directory is just one line, its full path."""
    byParent: dict[Path, list[Path]] = {}
    for path in paths:
        byParent.setdefault(path.parent, []).append(path)
    lines = []
    for parent, children in byParent.items():
        if len(children) == 1:
            lines.append(str(children[0]))
            continue
        lines.append(f"{parent}{os.sep}")
        lines.extend(f"\t{child.name}" for child in children)
    return lines


def validateTaxonomyPackages(globList: list[str], parser: ArgumentParser) -> list[Path]:
    # Only worth echoing when there are patterns to expand (PowerShell passes
    # them through); after a bash expansion it would just repeat the list.
    if any(escape_glob(g) != g for g in globList):
        console_print(Text(f"Zip globs specified {' '.join(globList)}"))
    # A path, glob or directory that yields nothing would otherwise just drop
    # out of the list, so a mistyped package would be silently left out.
    if unmatched := [g for g in globList if not expandPackageArgument(g)]:
        raise parser.error(f"No files found for: {' '.join(unmatched)}")
    taxonomy_zips = getListofPathsFromListOfGlobs(globList)
    console_print("Zip files to use:")
    console_print_plain(packageListLines(taxonomy_zips))

    if notZips := [str(z) for z in taxonomy_zips if z.suffix.lower() != ".zip"]:
        raise parser.error(f"Specified files are not Zip files: {' '.join(notZips)}")
    return taxonomy_zips


def getEntryPointsFromPackages(
    taxonomy_zips: Sequence[Path], parser: ArgumentParser
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
            "No taxonomy entry points declared by any of: "
            + " ".join(str(z) for z in taxonomy_zips)
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
    taxonomy_zips: Sequence[Path], parser: ArgumentParser
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


def pickEntryPointFromLoadedTaxonomies(
    parser: ArgumentParser, *, default: str | None = None
) -> str:
    """Show the entry point of every taxonomy already loaded (via
    loadBuiltInTaxonomyJSON()/loadTaxonomyJSON()) and prompt for one.

    Unlike pickEntryPointFromPackages(), there is no package zip or Arelle baking
    involved -- this only ever offers a choice among taxonomies mireport already
    knows about.
    """
    available = {
        str(num): ep for num, ep in enumerate(sorted(listTaxonomies()), start=1)
    }
    if not available:
        raise parser.error("No taxonomies loaded.")

    table = Table(show_header=True, box=box.SIMPLE)
    table.add_column("#", style="bold cyan", justify="right", no_wrap=True)
    table.add_column("Entry point")
    for num, url in available.items():
        marker = "  [bold green]← default[/bold green]" if url == default else ""
        table.add_row(num, url + marker)
    console_print(table)

    prompt = (
        "Number or URL (enter for default)" if default is not None else "Number or URL"
    )
    response = (
        Prompt.ask(prompt, default=default)
        if default is not None
        else Prompt.ask(prompt)
    ).strip()

    if default is not None and response == default:
        return default
    if (entry_point := available.get(response, response)) in available.values():
        return entry_point
    raise parser.error(f"{response!r} is not one of the loaded entry points.")


def _diagnosticLevelName(level: int) -> str:
    return logging.getLevelName(level).title()


def _diagnosticELR(diagnostic: AbstractDiagnostic[Any]) -> Text:
    """The ELR URI with its role definition underneath, dimmed and on one
    logical line (rich wraps it to the column)."""
    if diagnostic.elr is None:
        return Text()
    cell = Text(diagnostic.elr)
    if diagnostic.elrDefinition is not None:
        definition = " ".join(diagnostic.elrDefinition.split())
        cell.append(f"\n{definition}", style="dim")
    return cell


def _diagnosticDetails(diagnostic: AbstractDiagnostic[Any]) -> str:
    lines = [f"{key}: {value}" for key, value in diagnostic.details.items()]
    if diagnostic.hint is not None:
        lines.append(f"hint: {diagnostic.hint}")
    return "\n".join(lines)


def printDiagnosticTable(
    title: str, diagnostics: Sequence[AbstractDiagnostic[Any]]
) -> None:
    """Render diagnostics (extraction Diagnostics or checker findings) as a
    table, sorted by descending severity, followed by a one-line count
    summary."""
    if not diagnostics:
        console_print(f"No {title.lower()} to report.")
        return

    table = Table(title=title, show_lines=True)
    table.add_column("Level", no_wrap=True)
    table.add_column("Message", max_width=40)
    table.add_column("ELR", overflow="fold")
    table.add_column("Concepts", overflow="fold")
    table.add_column("Details", overflow="fold")

    for diagnostic in sorted(diagnostics, key=lambda d: -d.level):
        table.add_row(
            f"[{_DIAGNOSTIC_LEVEL_STYLES.get(diagnostic.level, '')}]"
            f"{_diagnosticLevelName(diagnostic.level)}[/]",
            escape(diagnostic.text),
            _diagnosticELR(diagnostic),
            escape("\n".join(str(qname) for qname in diagnostic.concepts)),
            escape(_diagnosticDetails(diagnostic)),
        )
    console_print(table)

    counts = Counter(_diagnosticLevelName(d.level) for d in diagnostics)
    summary = ", ".join(
        f"{count} {name.lower()}{'s' if count != 1 else ''}"
        for name, count in counts.most_common()
    )
    console_print(f"{summary} ({title.lower()}).")
