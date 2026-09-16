"""
Generate a maximal xBRL-JSON (OIM) coverage report for a taxonomy: one fact per
reportable concept, plus further facts that individually exercise every hypercube
dimension and enumeration domain a concept carries. See mireport.coverage_report_generator
for what "maximal" means here and why.

    python scripts/generate-test-reports.py <primary package glob>
        [support package glob ...]
        --entity-scheme URI --entity-identifier ID --entity-prefix PREFIX
        --period-instant ISO-DATETIME --period-duration ISO-DATETIME/ISO-DATETIME
        [--output-dir DIR] [--entry-point URL ...] [--taxonomy-json-out PATH]

Bakes the taxonomy with Arelle via mireport and asks it for everything -- which
concepts are reportable, their period and data types, the members of an enumeration
domain, which units the UTR permits, and which hypercube dimensions a concept
carries and their domains -- so nothing here parses a schema or a linkbase.

Writes three files to --output-dir:

    no-facts.json    an empty report
    all-nil.json     one xsi:nil fact per reportable concept, plus further facts
                     exercising every hypercube dimension a concept carries
    all-values.json  the same, with real (non-nil) values

The entry point comes from the *primary* package's own declared entry point (an
error if it declares more than one, unless --entry-point overrides it); support
packages are loaded into the DTS but not scanned for entry points, since they may
well declare their own irrelevant ones (a country/currency/LEI codelist package,
say).
"""

from __future__ import annotations

import argparse
import glob as glob_module
import tempfile
from pathlib import Path

from mireport.cli import (
    configure_rich_output,
)
from mireport.cli import (
    console_print as print,
)
from mireport.coverage_report_generator import (
    SampleEntityPeriod,
    bakeTaxonomy,
    buildCoverageReportSet,
    writeCoverageReportSet,
)
from mireport.exceptions import SampleGenerationException, TaxonomyPackageException
from mireport.taxonomy_package import entryPointsFromPackage


def resolvePackage(package_glob: str) -> str:
    """Resolve a taxonomy-package glob to a single file."""
    if not (matches := sorted(glob_module.glob(package_glob))):
        raise SystemExit(f"Error: no file matching {package_glob!r} -- is it built?")
    if len(matches) > 1:
        others = ", ".join(repr(m) for m in matches[1:])
        print(
            f"Warning: multiple files match {package_glob!r}; using {matches[0]!r}. "
            f"Ignored: {others}"
        )
    return matches[0]


def entryPointFromPrimaryPackage(package: str) -> tuple[str, ...]:
    """The primary package's own single declared entry point.

    Taken from the package rather than assembled from a filename, so the entry point
    baked here is the one the package actually declares.
    """
    try:
        found = entryPointsFromPackage(package)
    except TaxonomyPackageException as e:
        raise SystemExit(f"Error: could not read entry points from {package}: {e}")
    if len(found) != 1:
        names = [ep.hrefs for ep in found]
        raise SystemExit(
            f"Error: expected exactly one entry point in {package}, found {names}. "
            "Pass --entry-point to choose one explicitly."
        )
    return found[0].hrefs


def parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Generate a maximal xBRL-JSON coverage report for a taxonomy."
    )
    parser.add_argument(
        "primary_package",
        help="Glob for the taxonomy package whose entry point is baked, e.g. "
        "MyTaxonomy-*.zip.",
    )
    parser.add_argument(
        "support_packages",
        nargs="*",
        help="Globs for further taxonomy packages loaded into the DTS alongside "
        "the primary one (a country/currency/LEI codelist package, say). Not "
        "scanned for entry points.",
    )
    parser.add_argument(
        "--entry-point",
        type=str,
        action="append",
        default=None,
        help="Entry point to bake. Repeat it for an entry point that names several "
        "documents. If omitted, the primary package's own single declared entry "
        "point is used.",
    )
    parser.add_argument(
        "--entity-scheme",
        required=True,
        help='Entity scheme namespace URI, e.g. "http://standards.iso.org/iso/17442" '
        "for LEI.",
    )
    parser.add_argument(
        "--entity-identifier", required=True, help="Entity identifier, e.g. an LEI."
    )
    parser.add_argument(
        "--entity-prefix",
        required=True,
        help='Namespace prefix for --entity-scheme, e.g. "lei".',
    )
    parser.add_argument(
        "--period-instant",
        required=True,
        help="ISO datetime for an instant-period fact, e.g. 2027-01-01T00:00:00.",
    )
    parser.add_argument(
        "--period-duration",
        required=True,
        help="ISO datetime interval for a duration-period fact, e.g. "
        "2026-01-01T00:00:00/2027-01-01T00:00:00.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        required=True,
        help="Where to write no-facts.json, all-nil.json and all-values.json.",
    )
    parser.add_argument(
        "--taxonomy-json-out",
        type=Path,
        help="Keep the baked taxonomy JSON at this path instead of a temporary one. "
        "For inspection; it is an intermediate, not a report.",
    )
    return parser


def main() -> None:
    args = parser().parse_args()

    primary_package = resolvePackage(args.primary_package)
    support_packages = [resolvePackage(glob) for glob in args.support_packages]
    packages = [primary_package, *support_packages]

    entry_point: tuple[str, ...] = (
        tuple(args.entry_point)
        if args.entry_point
        else entryPointFromPrimaryPackage(primary_package)
    )

    samplePeriod = SampleEntityPeriod(
        entity=f"{args.entity_prefix}:{args.entity_identifier}",
        entityPrefix=args.entity_prefix,
        entityNamespace=args.entity_scheme,
        periodInstant=args.period_instant,
        periodDuration=args.period_duration,
    )

    print(f"Baking taxonomy for {entry_point}")
    for package in packages:
        print(f"  package: {package}")

    try:
        with tempfile.TemporaryDirectory(prefix="taxonomy-info-") as tmp:
            taxonomy_json_path = args.taxonomy_json_out or Path(tmp) / "taxonomy.json"
            taxonomy = bakeTaxonomy(entry_point, packages, taxonomy_json_path)
            if args.taxonomy_json_out:
                print(f"Kept baked taxonomy JSON at {args.taxonomy_json_out}")

            reportSet = buildCoverageReportSet(taxonomy, samplePeriod, entry_point)
    except SampleGenerationException as e:
        raise SystemExit(f"Error: {e}")

    print(f"Reportable concepts: {reportSet.reportableConceptCount}")

    counts = writeCoverageReportSet(reportSet, args.output_dir)
    for name, count in counts.items():
        print(f"Wrote {args.output_dir / name} ({count} facts)")

    if reportSet.emptyDomains:
        print(
            f"\nNote: {len(reportSet.emptyDomains)} entries in all-values.json "
            "resolved to no members -- either an enumeration *single* left nil "
            "because its own domain is empty, or a 'concept (dimension)' pair "
            "skipped because that explicit dimension's domain is empty."
        )
        for name in reportSet.emptyDomains:
            print(f"  {name}")

    if reportSet.emptySetDomains:
        print(
            f"\nNote: {len(reportSet.emptySetDomains)} enumeration *set* concept(s) "
            "in all-values.json have a domain that resolved to no members, so "
            "their one fact carries the (legal, non-nil) empty-set value rather "
            "than a populated one."
        )
        for name in reportSet.emptySetDomains:
            print(f"  {name}")


if __name__ == "__main__":
    configure_rich_output()
    main()
