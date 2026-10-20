"""
The public entry points for coverage_report_generator: build its three standard
coverage reports and write them to disk as xBRL-JSON. Everything in _sampling.py is an
internal building block of buildCoverageReportSet() below; a caller should not need it
directly.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from mireport.coverage_report_generator._sampling import (
    SampleEntityPeriod,
    buildFacts,
    newReport,
    reportableConcepts,
)
from mireport.xbrljson_reader.document import XbrlJsonDocument
from mireport.xbrljson_writer import toXbrlJson

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path

    from mireport.report import Report
    from mireport.taxonomy import Taxonomy


@dataclass(frozen=True)
class CoverageReportSet:
    """The three standard coverage reports, plus notes about anything unusual
    buildFacts() found while generating them -- see buildCoverageReportSet()."""

    reportableConceptCount: int
    noFacts: Report
    allNil: Report
    allValues: Report
    emptyDomains: list[str]
    emptySetDomains: list[str]

    @property
    def documents(self) -> dict[str, XbrlJsonDocument]:
        """Each report as the xBRL-JSON document it is written as, by file name."""
        return {
            "no-facts.json": toXbrlJson(self.noFacts),
            "all-nil.json": toXbrlJson(self.allNil),
            "all-values.json": toXbrlJson(self.allValues),
        }


def buildCoverageReportSet(
    taxonomy: Taxonomy,
    samplePeriod: SampleEntityPeriod,
    entryPointDocuments: Sequence[str],
) -> CoverageReportSet:
    """
    Build the three standard coverage reports for *taxonomy*, each referring to every
    document in *entryPointDocuments*:

        no-facts.json    an empty report
        all-nil.json     one xsi:nil fact per reportable concept, plus further facts
                         exercising every hypercube dimension a concept carries
        all-values.json  the same, with real (non-nil) values

    See buildFacts() for what "exercising every hypercube dimension" means and how
    it is done.
    """
    concepts = reportableConcepts(taxonomy)
    allNil, _, _ = buildFacts(taxonomy, concepts, samplePeriod, nil=True)
    allValues, emptyDomains, emptySetDomains = buildFacts(
        taxonomy, concepts, samplePeriod, nil=False
    )
    noFacts = newReport(taxonomy, samplePeriod)
    for report in (noFacts, allNil, allValues):
        for document in entryPointDocuments:
            report.addSchemaRef(document)
    return CoverageReportSet(
        reportableConceptCount=len(concepts),
        noFacts=noFacts,
        allNil=allNil,
        allValues=allValues,
        emptyDomains=emptyDomains,
        emptySetDomains=emptySetDomains,
    )


def writeCoverageReportSet(
    reportSet: CoverageReportSet, output_dir: Path
) -> dict[str, int]:
    """
    Write no-facts.json, all-nil.json and all-values.json into *output_dir*
    (creating it if needed), returning each file's fact count.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    counts: dict[str, int] = {}
    for name, document in reportSet.documents.items():
        document.toFile(output_dir / name)
        counts[name] = len(document.facts)
    return counts
