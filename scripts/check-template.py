import argparse
import logging
from itertools import groupby
from pathlib import Path

import mireport
from mireport.cli import configure_rich_output, console_print_plain
from mireport.cli import console_print as print
from mireport.entrypoints import describeEntryPointSet
from mireport.exceptions import TaxonomyException
from mireport.taxonomy import loadTaxonomyJSON
from mireport.xlsx_template_reader.templateaudit import (
    INFORMATIONAL_KINDS,
    TemplateAudit,
    auditTemplate,
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


def printAudit(audit: TemplateAudit) -> None:
    print(f"Workbook schemaRef: {audit.entryPoint or '(none)'}")
    print(f"Checked against: {describeEntryPointSet(audit.taxonomy.entryPointSet)}")
    print(f"Template version: {audit.templateVersion or '(none)'}")
    if not audit.findings:
        print("[green]No differences between the template and the taxonomy.[/green]")
        return
    for kind, findings in groupby(audit.findings, key=lambda f: f.kind):
        lines = [f"{f.name}  {f.detail}".strip() for f in findings]
        colour = "yellow" if kind in INFORMATIONAL_KINDS else "red"
        print(f"\n[{colour}]{kind.value} ({len(lines)}):[/{colour}]")
        console_print_plain(lines)


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
    print(f"Workbook: {args.workbook}")
    if args.reference:
        print(f"Reference: {args.reference}")
    try:
        audit = auditTemplate(workbook, taxonomy=taxonomy, reference=reference)
    except TaxonomyException as e:
        print(f"[red]{e}[/red]")
        return 2

    printAudit(audit)
    if problems := audit.problems:
        print(f"\n[red]{len(problems)} problems found.[/red]")
        return 1
    return 0


if __name__ == "__main__":
    configure_rich_output(locals_max_length=4)
    raise SystemExit(main())
