import argparse
from contextlib import contextmanager
from time import perf_counter_ns
from typing import NamedTuple

import xlsxwriter

import mireport
from mireport.cli import configure_rich_output
from mireport.cli import console_print as print
from mireport.excelprocessor import VSME_DEFAULTS
from mireport.taxonomy import (
    DOCUMENTATION_LABEL_ROLE,
    LABEL_SUFFIX_PATTERN,
    MEASUREMENT_GUIDANCE_LABEL_ROLE,
    STANDARD_LABEL_ROLE,
    TERSE_LABEL_ROLE,
    TOTAL_LABEL_ROLE,
    VERBOSE_LABEL_ROLE,
    Concept,
    Taxonomy,
    getTaxonomy,
    listTaxonomies,
)
from mireport.taxonomy_checker import TaxonomyChecker


class LabelRow(NamedTuple):
    concept: Concept
    roleUri: str
    suffix: str
    labelNoSuffix: str


BRANCH = "\N{BOX DRAWINGS LIGHT VERTICAL AND RIGHT}\N{BOX DRAWINGS LIGHT HORIZONTAL}"
LEAF = "\N{BOX DRAWINGS LIGHT UP AND RIGHT}\N{BOX DRAWINGS LIGHT HORIZONTAL}"
VBAR_WITH_PADDING = "\N{BOX DRAWINGS LIGHT VERTICAL}  "
JUST_PADDING = " " * len(VBAR_WITH_PADDING)
assert len(VBAR_WITH_PADDING) == len(
    JUST_PADDING
)  # Otherwise the tree won't line up correctly


@contextmanager
def timer(label: str):
    start = perf_counter_ns()
    try:
        yield
    finally:
        elapsed_ms = (perf_counter_ns() - start) // 1_000_000
        print(f"✓ {label} in {elapsed_ms:,} milliseconds")


def indent(depth: int, active_depths: set[int]) -> str:
    """Build a tree-drawing prefix for the given nesting level.

    active_depths contains each ancestor depth that still has more
    siblings to come (i.e. should show a vertical bar).
    """

    def glyph(d: int) -> str:
        match (d == depth, d in active_depths):
            case (True, True):
                return BRANCH
            case (True, False):
                return LEAF
            case (_, True):
                return VBAR_WITH_PADDING
            case _:
                return JUST_PADDING

    return "".join(glyph(d) for d in range(depth + 1))


def format_relationship(relationship, active_depths: set[int]) -> str:
    """Format a relationship for display."""
    concept = relationship.concept
    prefix = indent(relationship.depth, active_depths)
    return f"{prefix} {concept.getStandardLabel()} [{concept.qname} {concept.dataType}]"


def compute_last_flags(relationships) -> tuple[bool, ...]:
    """Pre-compute whether each relationship is the last at its depth.

    Scans backwards in a single O(n) pass, tracking which depths
    have already been seen. The first occurrence of a depth (from the end)
    is the last at that depth within its group.
    """
    n = len(relationships)
    flags = [False] * n
    seen_depths: dict[int, None] = {}
    for i in range(n - 1, -1, -1):
        depth = relationships[i].depth
        # Pop any deeper depths — they belong to a previous group
        while seen_depths and next(reversed(seen_depths)) > depth:
            seen_depths.popitem()
        if depth not in seen_depths:
            flags[i] = True
            seen_depths[depth] = None
    return tuple(flags)


def dump_group(group) -> None:
    """Print a single presentation group as a tree."""
    print(f"{group.getLabel()} [{group.roleUri}]")
    last_flags = compute_last_flags(group.relationships)
    active_depths: set[int] = set()
    for relationship, is_last in zip(group.relationships, last_flags, strict=True):
        depth = relationship.depth
        # Prune any depths deeper than current — we've left those subtrees
        active_depths = {d for d in active_depths if d < depth}
        if not is_last:
            active_depths.add(depth)
        print(format_relationship(relationship, active_depths))


def pick_entry_point() -> str:
    """Prompt the user to select a taxonomy entry point."""
    default = VSME_DEFAULTS["taxonomyEntryPoints"]["supportedEntryPoint"]
    available = {
        str(num): ep for num, ep in enumerate(sorted(listTaxonomies()), start=1)
    }
    print(
        "Available taxonomies:",
        *[
            f"{num}: {url}{' *' if url == default else ''}"
            for num, url in available.items()
        ],
        sep="\n\t",
    )
    response = input("Specify alternate entry point or leave default (*): ").strip()

    if not response or response == default:
        return default
    if (entry_point := available.get(response, response)) in available.values():
        return entry_point
    raise SystemExit("Can't access specified entry point.")


LABEL_ROLE_NAMES: dict[str, str] = {
    STANDARD_LABEL_ROLE: "Standard",
    TERSE_LABEL_ROLE: "Terse",
    VERBOSE_LABEL_ROLE: "Verbose",
    TOTAL_LABEL_ROLE: "Total",
    DOCUMENTATION_LABEL_ROLE: "Documentation",
    MEASUREMENT_GUIDANCE_LABEL_ROLE: "Measurement Guidance",
}


def dump_translation_sheet(
    taxonomy: Taxonomy,
    output_path: str,
    languages: list[str],
    *,
    only_prefixes: list[str] | None = None,
    filter_measurement_guidance: bool = True,
) -> None:
    """Write a translation sheet Excel file with labels for every concept/role pair."""
    concepts = taxonomy.concepts

    if only_prefixes:
        prefix_set = frozenset(only_prefixes)
        concepts = frozenset(c for c in concepts if c.qname.prefix in prefix_set)

    rows: list[LabelRow] = []

    for concept in sorted(concepts):
        all_roles = concept.labelRoles

        if filter_measurement_guidance:
            all_roles -= {MEASUREMENT_GUIDANCE_LABEL_ROLE}

        for role_uri in sorted(all_roles):
            if (en_label := concept.getLabelForRole(role_uri, "en")) is None:
                continue

            suffix = (
                m.group(0).strip()
                if (m := LABEL_SUFFIX_PATTERN.search(en_label))
                else ""
            )
            label_no_suffix = LABEL_SUFFIX_PATTERN.sub("", en_label).strip()

            rows.append(LabelRow(concept, role_uri, suffix, label_no_suffix))

    with xlsxwriter.Workbook(output_path) as workbook:
        worksheet = workbook.add_worksheet("Labels")

        bold = workbook.add_format({"bold": True})

        headers = ["Concept", "Role", "Label Postfix", "en"] + languages
        for col, header in enumerate(headers):
            worksheet.write(0, col, header, bold)

        # Column widths: Label Postfix (col 2) is 15, everything else is 40
        worksheet.set_column(0, 0, 40)  # Concept
        worksheet.set_column(1, 1, 15)  # Role
        worksheet.set_column(2, 2, 15)  # Label Postfix
        worksheet.set_column(3, 3 + len(languages), 40)  # en + lang columns

        worksheet.freeze_panes(1, 0)
        worksheet.autofilter(0, 0, 0, len(headers) - 1)

        for row_idx, row in enumerate(rows, start=1):
            role_name = LABEL_ROLE_NAMES.get(row.roleUri, row.roleUri)
            worksheet.write(row_idx, 0, str(row.concept.qname))
            worksheet.write(row_idx, 1, role_name)
            worksheet.write(row_idx, 2, row.suffix)
            worksheet.write(row_idx, 3, row.labelNoSuffix)
            for col_offset, lang in enumerate(languages):
                worksheet.write(
                    row_idx,
                    4 + col_offset,
                    row.concept.getLabelForRole(row.roleUri, lang) or "",
                )

    print(f"✅ Translation sheet written to {output_path} ({len(rows):,} rows)")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Dump taxonomy information.")
    parser.add_argument(
        "--check",
        action="store_true",
        help="Run taxonomy checks after dumping.",
    )
    parser.add_argument(
        "--translation-sheet",
        metavar="OUTPUT.xlsx",
        help="Write a translation sheet to the given Excel file instead of dumping the tree.",
    )
    parser.add_argument(
        "--only-prefixes",
        nargs="+",
        metavar="PREFIX",
        help="Restrict output to concepts with these namespace prefixes (e.g. --only-prefixes vsme nace).",
    )
    parser.add_argument(
        "--include-measurement-guidance",
        action="store_true",
        help="Include measurement guidance labels (default: excluded).",
    )
    parser.add_argument(
        "--languages",
        "-l",
        nargs="+",
        metavar="LANG",
        default=[],
        help="Language codes for additional columns in the translation sheet (e.g. -l fr de).",
    )
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    with timer("Taxonomies loaded"):
        mireport.loadTaxonomyJSON()

    entry_point = pick_entry_point()
    taxonomy = getTaxonomy(entry_point)

    if args.translation_sheet:
        dump_translation_sheet(
            taxonomy,
            args.translation_sheet,
            args.languages,
            only_prefixes=args.only_prefixes,
            filter_measurement_guidance=not args.include_measurement_guidance,
        )
        return

    for group in taxonomy.presentation:
        dump_group(group)

    print(
        f"\nLabel languages: {', '.join(sorted(taxonomy.supportedLanguages))}",
        f"Default language: {taxonomy.defaultLanguage}",
        sep="\n",
    )
    if args.check:
        print()
        TaxonomyChecker(taxonomy).reportIssues()


if __name__ == "__main__":
    configure_rich_output()
    main()
