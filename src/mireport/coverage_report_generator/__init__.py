"""Generates a maximal xBRL-JSON (OIM) report proving that every reportable concept
in a taxonomy, and every hypercube dimension or enumeration domain it carries, is
individually usable -- not just declared. Taxonomy-agnostic: everything about the
taxonomy comes from an already-baked mireport.taxonomy.Taxonomy -- getting one is a
caller's own concern (loadTaxonomyJSON() for an already-built taxonomy, or
mireport.arelle.taxonomy_info.callArelleForTaxonomyInfo() to bake one first).

Public surface: buildCoverageReportSet() to build the three standard reports (see
CoverageReportSet), and writeCoverageReportSet() to write them to disk. Everything
else, including _sampling.py in its entirety, is an internal building block.
"""

from mireport.coverage_report_generator._sampling import SampleEntityPeriod
from mireport.coverage_report_generator.report_writer import (
    CoverageReportSet,
    buildCoverageReportSet,
    writeCoverageReportSet,
)

__all__ = [
    "CoverageReportSet",
    "SampleEntityPeriod",
    "buildCoverageReportSet",
    "writeCoverageReportSet",
]
