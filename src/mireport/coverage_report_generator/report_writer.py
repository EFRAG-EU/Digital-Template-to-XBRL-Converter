"""
The public entry points for coverage_report_generator: bake a taxonomy, build its
three standard coverage reports, and write them to disk. Everything in
_sampling.py is an internal building block of buildCoverageReportSet() below; a
caller should not need it directly.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from mireport.arelle.taxonomy_info import callArelleForTaxonomyInfo
from mireport.conversionresults import Severity
from mireport.coverage_report_generator._sampling import (
    NS_XBRL,
    SampleEntityPeriod,
    buildFacts,
    makeDocument,
    reportableConcepts,
)
from mireport.data import registries
from mireport.exceptions import SampleGenerationException
from mireport.json import getResource
from mireport.taxonomy import loadTaxonomyJSON

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path

    from mireport.taxonomy import Taxonomy


def bakeTaxonomy(
    entry_point: str | Sequence[str], packages: list[str], taxonomy_json_path: Path
) -> Taxonomy:
    """Run Arelle over *packages* via mireport and return the loaded Taxonomy.

    TODO: wrong home. There is nothing coverage-report-specific about "bake a
    taxonomy and refuse to hand back one Arelle logged error-severity messages
    about" -- this lives here for now only because two callers (this package's own
    scripts/generate-test-reports.py, and esrs-taxonomy's thin wrapper around it)
    needed to stop duplicating it, and this was the nearest shared home. It likely
    belongs closer to mireport.arelle.taxonomy_info/mireport.taxonomy instead; move
    it deliberately rather than leaving it here by inertia.

    A structurally broken DTS already raises (Arelle's abortOnMajorError, or an
    ArelleModelInconsistency from the extractor), and loadTaxonomyJSON() raises on a
    structurally broken JSON file -- so nothing further is needed for "is this
    taxonomy internally consistent". utrValidate is a different question: a unit
    used in a definition/instance context that the UTR does not permit is a normal
    logged validation message, not an exception, so it would otherwise bake, load
    and feed report generation in complete silence -- hence the explicit check
    below, raising SampleGenerationException rather than letting it pass.
    """
    result = callArelleForTaxonomyInfo(
        entry_point,
        packages,
        taxonomy_json_path,
        # Passing the UTR turns on Arelle's utrValidate, so baking also checks the
        # taxonomy's own unit usage.
        utr_json_path=str(getResource(registries, "utr.json")),
    )
    if not taxonomy_json_path.is_file():
        raise SampleGenerationException(
            f"mireport wrote no taxonomy JSON for {entry_point}. Arelle result: {result}"
        )
    if result.has_exceptions or any(
        message.severity is Severity.ERROR for message in result.messages
    ):
        errors = "\n".join(
            f"  {message}"
            for message in result.messages
            if message.severity is Severity.ERROR
        )
        raise SampleGenerationException(
            f"Baking {entry_point} logged error-severity validation messages "
            f"(has_exceptions={result.has_exceptions}):\n{errors}"
        )
    return loadTaxonomyJSON(taxonomy_json_path)


@dataclass(frozen=True)
class CoverageReportSet:
    """The three standard coverage-report documents, ready to write, plus notes
    about anything unusual buildFacts() found while generating them -- see
    buildCoverageReportSet()."""

    reportableConceptCount: int
    noFacts: dict[str, Any]
    allNil: dict[str, Any]
    allValues: dict[str, Any]
    emptyDomains: list[str]
    emptySetDomains: list[str]


def buildCoverageReportSet(
    taxonomy: Taxonomy,
    samplePeriod: SampleEntityPeriod,
    entryPointDocuments: Sequence[str],
) -> CoverageReportSet:
    """
    Build the three standard coverage reports for *taxonomy*:

        no-facts.json    an empty report
        all-nil.json     one xsi:nil fact per reportable concept, plus further facts
                         exercising every hypercube dimension a concept carries
        all-values.json  the same, with real (non-nil) values

    See buildFacts() for what "exercising every hypercube dimension" means and how
    it is done.
    """
    concepts = reportableConcepts(taxonomy)
    nil_facts, nil_namespaces, _, _ = buildFacts(
        taxonomy, concepts, samplePeriod, nil=True
    )
    valued_facts, valued_namespaces, emptyDomains, emptySetDomains = buildFacts(
        taxonomy, concepts, samplePeriod, nil=False
    )
    return CoverageReportSet(
        reportableConceptCount=len(concepts),
        noFacts=makeDocument({}, {"xbrl": NS_XBRL}, entryPointDocuments),
        allNil=makeDocument(nil_facts, nil_namespaces, entryPointDocuments),
        allValues=makeDocument(valued_facts, valued_namespaces, entryPointDocuments),
        emptyDomains=emptyDomains,
        emptySetDomains=emptySetDomains,
    )


def writeCoverageReportSet(
    reportSet: CoverageReportSet, output_dir: Path
) -> dict[str, int]:
    """
    Write no-facts.json, all-nil.json and all-values.json into *output_dir*
    (creating it if needed), returning each file's fact count.

    Each file is pretty-printed JSON with a trailing newline; newline="\\n" is not
    optional, since a report committed for later comparison must not gain CRLF when
    this runs on Windows.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    counts: dict[str, int] = {}
    for name, document in (
        ("no-facts.json", reportSet.noFacts),
        ("all-nil.json", reportSet.allNil),
        ("all-values.json", reportSet.allValues),
    ):
        (output_dir / name).write_text(
            json.dumps(document, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
            newline="\n",
        )
        counts[name] = len(document["facts"])
    return counts
