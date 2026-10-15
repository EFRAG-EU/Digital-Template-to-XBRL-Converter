"""
Generate a maximal xBRL-JSON (OIM) coverage report for a taxonomy: one fact per
reportable concept, plus further facts that individually exercise every hypercube
dimension and enumeration domain a concept carries. See mireport.coverage_report_generator
for what "maximal" means here and why.

    python scripts/generate-test-reports.py
        --entity-scheme URI --entity-identifier ID --entity-prefix PREFIX
        --period-instant ISO-DATETIME --period-duration ISO-DATETIME/ISO-DATETIME
        --output-dir DIR
        [--entry-point URL] [--taxonomy PATH ...]

Works against taxonomies mireport already knows about -- the built-in ones, plus any
extra taxonomy JSON files (as produced by scripts/update-taxonomy.py) named via
--taxonomy -- exactly as scripts/dump-taxonomy.py does, prompting for an entry point
if --entry-point is not given. This is deliberately not the tool for baking a
taxonomy from package zips in the first place: that is scripts/update-taxonomy.py's
job, and a taxonomy under active local development that has no stable JSON yet is a
different problem again (see esrs-taxonomy's own wrapper, which bakes fresh via
mireport.arelle.taxonomy_info.bakeTaxonomy() for exactly that reason).

Writes three files to --output-dir:

    no-facts.json    an empty report
    all-nil.json     one xsi:nil fact per reportable concept, plus further facts
                     exercising every hypercube dimension a concept carries
    all-values.json  the same, with real (non-nil) values
"""

from __future__ import annotations

import argparse
from pathlib import Path

import mireport
from mireport.cli import configure_rich_output, pickEntryPointFromLoadedTaxonomies
from mireport.cli import console_print as print
from mireport.coverage_report_generator import (
    SampleEntityPeriod,
    buildCoverageReportSet,
    writeCoverageReportSet,
)
from mireport.entrypoints import describeEntryPointSet
from mireport.exceptions import SampleGenerationException
from mireport.taxonomy import getTaxonomy, loadTaxonomyJSON


def parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Generate a maximal xBRL-JSON coverage report for a taxonomy."
    )
    parser.add_argument(
        "--taxonomy",
        metavar="TAXONOMY",
        type=Path,
        nargs="+",
        help="Path to one or more taxonomy JSON files (as produced by "
        "update-taxonomy.py) to load in addition to the built-in ones.",
    )
    parser.add_argument(
        "--entry-point",
        help="Entry point to generate a coverage report for. Prompts if omitted.",
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
    return parser


def main() -> None:
    argParser = parser()
    args = argParser.parse_args()

    mireport.loadBuiltInTaxonomyJSON()
    for taxonomyPath in args.taxonomy or ():
        loadTaxonomyJSON(taxonomyPath)

    entry_point = args.entry_point or pickEntryPointFromLoadedTaxonomies(argParser)
    taxonomy = getTaxonomy(entry_point)

    samplePeriod = SampleEntityPeriod(
        entity=f"{args.entity_prefix}:{args.entity_identifier}",
        entityPrefix=args.entity_prefix,
        entityNamespace=args.entity_scheme,
        periodInstant=args.period_instant,
        periodDuration=args.period_duration,
    )

    print(
        "Generating coverage report for "
        + describeEntryPointSet(taxonomy.entryPointSet)
    )

    try:
        reportSet = buildCoverageReportSet(
            taxonomy, samplePeriod, sorted(taxonomy.entryPointSet)
        )
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
