import argparse
import logging
import sys
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
from mireport.taxonomy import Taxonomy
from mireport.xlsx_template_reader.templateaudit import (
    INFORMATIONAL_KINDS,
    FindingKind,
    TemplateAudit,
    TemplateFinding,
    auditTemplate,
    pinnedTemplateVersion,
)
from mireport.xlsx_template_reader.util import loadExcelFromPathOrFileLike

# Sections longer than this fold away in Markdown, to keep an issue readable.
MARKDOWN_FOLD_ROWS = 10

# A header check's mark, by its colour: glyphs in the terminal, and ASCII
# emoji shortcodes in Markdown so the console's code page can't garble them.
TERMINAL_MARKS = {"green": "✓ ", "red": "✗ ", "yellow": ""}
MARKDOWN_MARKS = {"green": ":white_check_mark: ", "red": ":x: ", "yellow": ":warning: "}


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
        "--markdown",
        action=argparse.BooleanOptionalAction,
        help="Write GitHub-flavoured Markdown, for pasting into an issue, instead of tables.",
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


def headerRows(
    audit: TemplateAudit, workbook: str, reference: str | None
) -> list[tuple[str, str, str, str]]:
    """(label, value, status, colour) for the workbook, what it targets, and
    how the disclosure system rates that. The colour picks the status's mark."""
    if findingFor(audit, FindingKind.unknownEntryPoint) is not None:
        schemaRef = ("not in any disclosure system", "red")
    elif (old := findingFor(audit, FindingKind.oldEntryPoint)) is not None:
        schemaRef = (f"old entry point: {old}", "yellow")
    else:
        schemaRef = ("supported entry point", "green")
    if (pinned := pinnedTemplateVersion(audit.entryPoint)) is None:
        version = ("not pinned by the disclosure system", "")
    elif findingFor(audit, FindingKind.templateVersionMismatch) is not None:
        version = (f"disclosure system pins {pinned}", "red")
    else:
        version = ("pinned by the disclosure system", "green")

    rows = [("Workbook", workbook, "", "")]
    if reference is not None:
        rows.append(("Reference", reference, "", ""))
    rows += [
        ("SchemaRef", audit.entryPoint or "(none)", *schemaRef),
        (
            "Checked against",
            describeEntryPointSet(audit.taxonomy.entryPointSet),
            "",
            "",
        ),
        ("Template version", audit.templateVersion or "(none)", *version),
    ]
    return rows


def sections(
    audit: TemplateAudit,
) -> list[tuple[FindingKind, list[TemplateFinding], list[tuple[str, str]]]]:
    """The findings by kind, each with the (heading, field) columns that have
    something in them."""
    result = []
    for kind, group in groupby(audit.findings, key=lambda f: f.kind):
        findings = list(group)
        columns = [
            (heading, field)
            for heading, field in (("Name", "name"), ("Detail", "detail"))
            if any(getattr(f, field) for f in findings)
        ]
        result.append((kind, findings, columns))
    return result


def printAudit(audit: TemplateAudit, workbook: Path, reference: Path | None) -> None:
    header = Table(box=box.SIMPLE, show_header=False)
    header.add_column(style="bold")
    header.add_column(overflow="fold")
    header.add_column()
    for label, value, status, colour in headerRows(
        audit, str(workbook), str(reference) if reference else None
    ):
        mark = TERMINAL_MARKS.get(colour, "")
        header.add_row(label, Text(value), Text(mark + status, style=colour))
    print(header)
    if not audit.findings:
        print("[green]No differences between the template and the taxonomy.[/green]")
    for kind, findings, columns in sections(audit):
        colour = "yellow" if kind in INFORMATIONAL_KINDS else "red"
        table = Table(
            title=f"{kind.value} ({len(findings)})",
            title_style=f"bold {colour}",
            title_justify="left",
            box=box.SIMPLE_HEAD,
        )
        for heading, _ in columns:
            table.add_column(heading, overflow="fold")
        for f in findings:
            # Text, not markup: names and messages can contain square brackets.
            table.add_row(*(Text(getattr(f, field)) for _, field in columns))
        print(table)
    if problems := audit.problems:
        print(f"\n[red]{len(problems)} problems found.[/red]")


def markdownCell(text: str, code: bool = False) -> str:
    text = text.replace("|", "\\|").replace("\n", " ")
    return f"`{text}`" if code and text else text


def markdownAudit(audit: TemplateAudit, workbook: Path, reference: Path | None) -> str:
    """The audit as GitHub-flavoured Markdown, to paste into an issue. Files are
    named without their local paths."""
    lines = ["| Item | Value | Check |", "|---|---|---|"]
    lines += [
        f"| **{label}** | {markdownCell(value, code=True)} "
        f"| {MARKDOWN_MARKS.get(colour, '')}{markdownCell(status)} |"
        for label, value, status, colour in headerRows(
            audit, workbook.name, reference.name if reference else None
        )
    ]
    if not audit.findings:
        lines += ["", "No differences between the template and the taxonomy."]
    for kind, findings, columns in sections(audit):
        title = f"{kind.value} ({len(findings)})"
        table = [
            "| " + " | ".join(heading for heading, _ in columns) + " |",
            "|" + "---|" * len(columns),
        ]
        table += [
            "| "
            + " | ".join(
                markdownCell(getattr(f, field), code=field == "name")
                for _, field in columns
            )
            + " |"
            for f in findings
        ]
        if len(findings) > MARKDOWN_FOLD_ROWS:
            lines += ["", f"<details><summary>{title}</summary>", "", *table]
            lines += ["", "</details>"]
        else:
            lines += ["", f"### {title}", "", *table]
    lines += ["", f"**{len(audit.problems)} problems found.**"]
    return "\n".join(lines) + "\n"


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
        # Built, not registered: it may well share a built-in's entry point.
        taxonomy = Taxonomy.fromJSON(args.taxonomy_json)

    workbook = loadExcelFromPathOrFileLike(args.workbook)
    reference = loadExcelFromPathOrFileLike(args.reference) if args.reference else None
    try:
        audit = auditTemplate(workbook, taxonomy=taxonomy, reference=reference)
    except TaxonomyException as e:
        print(f"[red]{e}[/red]")
        return 2

    if args.markdown:
        sys.stdout.write(markdownAudit(audit, args.workbook, args.reference))
    else:
        printAudit(audit, args.workbook, args.reference)
    return 1 if audit.problems else 0


if __name__ == "__main__":
    configure_rich_output(locals_max_length=4)
    raise SystemExit(main())
