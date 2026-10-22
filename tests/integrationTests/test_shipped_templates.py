"""Every shipped Digital Template audited against its taxonomy. The known
disagreements are recorded here, so a new one, or a fixed one, fails until
this table is updated: it doubles as the list of template issues to resolve."""

from __future__ import annotations

from pathlib import Path

import pytest

from mireport.taxonomy import loadBuiltInTaxonomyJSON
from mireport.xlsx_template_reader.templateaudit import FindingKind, auditTemplate
from mireport.xlsx_template_reader.util import loadExcelFromPathOrFileLike

TEMPLATES = Path(__file__).resolve().parents[2] / "digital-templates"

# Every VSME taxonomy has this hypercube; no template has a table for it.
ESTIMATED_GHG = (
    FindingKind.hypercubeWithoutName,
    "EstimatedGreenhouseGasEmissionsTable",
)

# vsme.json lists this taxonomy as .../vsme-entry-point.xsd, but the 1.0.x
# templates and the built-in taxonomy JSON name vsme-all.xsd.
UNLISTED_2024_12_17 = (
    FindingKind.unknownEntryPoint,
    "https://xbrl.efrag.org/taxonomy/vsme/2024-12-17/vsme-all.xsd",
)

EXPECTED_PROBLEMS: dict[str, set[tuple[FindingKind, str]]] = {
    "VSME-Digital-Template-1.0.0.xlsx": {ESTIMATED_GHG, UNLISTED_2024_12_17},
    "VSME-Digital-Template-1.0.1.xlsx": {ESTIMATED_GHG, UNLISTED_2024_12_17},
    "VSME-Digital-Template-1.1.0.xlsx": {ESTIMATED_GHG},
    "VSME-Digital-Template-1.1.1.xlsx": {ESTIMATED_GHG},
    "VSME-Digital-Template-1.2.0.xlsx": {ESTIMATED_GHG},
    "VSME-Digital-Template-1.3.0.xlsx": {ESTIMATED_GHG},
    "VSME-Digital-Template-Sample-1.0.0.xlsx": {ESTIMATED_GHG, UNLISTED_2024_12_17},
    "VSME-Digital-Template-Sample-1.0.1.xlsx": {ESTIMATED_GHG, UNLISTED_2024_12_17},
    "VSME-Digital-Template-Sample-1.1.0.xlsx": {
        ESTIMATED_GHG,
        # The blank 1.1.0 template doesn't have this stray name.
        (
            FindingKind.nameWithoutConcept,
            "MostSeniorLevelAccountableForImplementationOfPracticesPoliciesAndOrFutureInitiatives",
        ),
    },
    "VSME-Digital-Template-Sample-1.1.1.xlsx": {ESTIMATED_GHG},
    "VSME-Digital-Template-Sample-1.2.0.xlsx": {ESTIMATED_GHG},
    "VSME-Digital-Template-Sample-1.3.0.xlsx": {ESTIMATED_GHG},
}


@pytest.fixture(scope="module", autouse=True)
def _load_taxonomies():
    loadBuiltInTaxonomyJSON()


def test_every_shipped_template_is_listed():
    shipped = {p.name for p in TEMPLATES.glob("VSME-Digital-Template-*.xlsx")}
    assert shipped == EXPECTED_PROBLEMS.keys()


@pytest.mark.parametrize("name", sorted(EXPECTED_PROBLEMS))
def test_template_problems(name: str):
    audit = auditTemplate(loadExcelFromPathOrFileLike(TEMPLATES / name))
    assert {(f.kind, f.name) for f in audit.problems} == EXPECTED_PROBLEMS[name]
