import argparse
import logging
from itertools import groupby
from pathlib import Path

from rich import box
from rich.table import Table
from rich.text import Text

import mireport
from mireport.cli import configure_rich_output
from mireport.cli import console_print as print
from mireport.entrypoints import describeEntryPointSet
from mireport.exceptions import TaxonomyException
from mireport.taxonomy import loadTaxonomyJSON
from mireport.xlsx_template_reader.templateaudit import (
    INFORMATIONAL_KINDS,
    FindingKind,
    TemplateAudit,
    auditTemplate,
    pinnedTemplateVersion,
)
from mireport.xlsx_template_reader.util import loadExcelFromPathOrFileLike


def parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Check a Digital Template's named ranges against its taxonomy: names that bind to nothing, reportable concepts with no named range, and, given a reference template, names added, removed or moved."
    )
    parser.add_argument(
        "workbook", type=Path, help="The Digital Template (blank or filled in)."
    )
    parser.add_argument(
        "--reference",
        type=Path,
        default=None,
        help="A template to compare named ranges with, such as the published one.",
    )
    parser.add_argument(
        "--taxonomy-json",
        type=Path,
        default=None,
        help="Check against this taxonomy JSON (from update-taxonomy.py) instead of the built-in one the workbook's schemaRef names.",
    )
    parser.add_argument(
        "--debug",
        action=argparse.BooleanOptionalAction,
        help="Turn on debugging output.",
    )
    return parser


def findingFor(audit: TemplateAudit, kind: FindingKind) -> str | None:
    """The detail of the audit's finding of this kind, if it has one."""
    return next((f.detail for f in audit.findings if f.kind is kind), None)


def headerTable(audit: TemplateAudit, workbook: Path, reference: Path | None) -> Table:
    """The workbook, what it targets, and how the disclosure system rates that."""
    unknown = findingFor(audit, FindingKind.unknownEntryPoint)
    old = findingFor(audit, FindingKind.oldEntryPoint)
    if unknown is not None:
        schemaRefStatus = "[red]✗ not in any disclosure system[/red]"
    elif old is not None:
        schemaRefStatus = f"[yellow]old entry point: {old}[/yellow]"
    else:
        schemaRefStatus = "[green]✓ supported entry point[/green]"
    if (pinned := pinnedTemplateVersion(audit.entryPoint)) is None:
        versionStatus = "not pinned by the disclosure system"
    elif findingFor(audit, FindingKind.templateVersionMismatch) is not None:
        versionStatus = f"[red]✗ disclosure system pins {pinned}[/red]"
    else:
        versionStatus = "[green]✓ pinned by the disclosure system[/green]"

    table = Table(box=box.SIMPLE, show_header=False)
    table.add_column(style="bold")
    table.add_column(overflow="fold")
    table.add_column()
    table.add_row("Workbook", Text(str(workbook)), "")
    if reference is not None:
        table.add_row("Reference", Text(str(reference)), "")
    table.add_row("SchemaRef", Text(audit.entryPoint or "(none)"), schemaRefStatus)
    table.add_row(
        "Checked against",
        Text(describeEntryPointSet(audit.taxonomy.entryPointSet)),
        "",
    )
    table.add_row(
        "Template version", Text(audit.templateVersion or "(none)"), versionStatus
    )
    return table


def printAudit(audit: TemplateAudit, workbook: Path, reference: Path | None) -> None:
    print(headerTable(audit, workbook, reference))
    if not audit.findings:
        print("[green]No differences between the template and the taxonomy.[/green]")
        return
    for kind, group in groupby(audit.findings, key=lambda f: f.kind):
        findings = list(group)
        colour = "yellow" if kind in INFORMATIONAL_KINDS else "red"
        table = Table(
            title=f"{kind.value} ({len(findings)})",
            title_style=f"bold {colour}",
            title_justify="left",
            box=box.SIMPLE_HEAD,
        )
        columns = [
            (heading, field)
            for heading, field in (("Name", "name"), ("Detail", "detail"))
            if any(getattr(f, field) for f in findings)
        ]
        for heading, _ in columns:
            table.add_column(heading, overflow="fold")
        for f in findings:
            # Text, not markup: names and messages can contain square brackets.
            table.add_row(*(Text(getattr(f, field)) for _, field in columns))
        print(table)


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    if args.debug:
        logging.root.setLevel(logging.DEBUG)

    for path in (args.workbook, args.reference, args.taxonomy_json):
        if path is not None and not path.is_file():
            print(f"[red]{path} cannot be found.[/red]")
            return 2

    taxonomy = None
    mireport.loadBuiltInTaxonomyJSON()
    if args.taxonomy_json is not None:
        taxonomy = loadTaxonomyJSON(args.taxonomy_json)

    workbook = loadExcelFromPathOrFileLike(args.workbook)
    reference = loadExcelFromPathOrFileLike(args.reference) if args.reference else None
    try:
        audit = auditTemplate(workbook, taxonomy=taxonomy, reference=reference)
    except TaxonomyException as e:
        print(f"[red]{e}[/red]")
        return 2

    printAudit(audit, args.workbook, args.reference)
    if problems := audit.problems:
        print(f"\n[red]{len(problems)} problems found.[/red]")
        return 1
    return 0


if __name__ == "__main__":
    configure_rich_output(locals_max_length=4)
    raise SystemExit(main())
