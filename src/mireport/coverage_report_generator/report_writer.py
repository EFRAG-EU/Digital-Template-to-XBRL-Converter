"""
The public entry points for coverage_report_generator: build its three standard
coverage reports and write them to disk. Everything in _sampling.py is an internal
building block of buildCoverageReportSet() below; a caller should not need it
directly.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from mireport.coverage_report_generator._sampling import (
    NS_XBRL,
    SampleEntityPeriod,
    buildFacts,
    makeDocument,
    reportableConcepts,
)

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path

    from mireport.taxonomy import Taxonomy


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
