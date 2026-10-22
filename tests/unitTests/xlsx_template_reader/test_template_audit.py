"""auditTemplate: where a workbook's named ranges and its taxonomy disagree,
and the check-template.py script that reports it."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

import pytest
from openpyxl import Workbook
from openpyxl.workbook.defined_name import DefinedName

from mireport.exceptions import TaxonomyException
from mireport.taxonomy import getTaxonomy
from mireport.xlsx_template_reader.templateaudit import (
    FindingKind,
    TemplateAudit,
    auditTemplate,
)

SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "check-template.py"
VS_ENTRY_POINT = "https://xbrl.efrag.org/taxonomy/vs/2026-10-16/vs-all.xsd"


@pytest.fixture(scope="module")
def script() -> ModuleType:
    spec = importlib.util.spec_from_file_location("check_template_script", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def workbookWith(**names: str) -> Workbook:
    """A workbook with one sheet, Data, and these defined names (name → ref)."""
    wb = Workbook()
    ws = wb.active
    assert ws is not None
    ws.title = "Data"
    for name, ref in names.items():
        wb.defined_names[name] = DefinedName(name, attr_text=ref)
    return wb


def audit(wb: Workbook, reference: Workbook | None = None) -> TemplateAudit:
    return auditTemplate(wb, taxonomy=getTaxonomy(VS_ENTRY_POINT), reference=reference)


def names(audit: TemplateAudit, kind: FindingKind) -> set[str]:
    return {f.name for f in audit.findings if f.kind is kind}


def test_concept_name_binds():
    result = audit(workbookWith(Turnover="Data!$B$2"))
    assert "Turnover" not in names(result, FindingKind.nameWithoutConcept)
    assert "Turnover" not in names(result, FindingKind.conceptWithoutName)
    assert "Assets" in names(result, FindingKind.conceptWithoutName)


def test_names_binding_to_nothing():
    result = audit(workbookWith(Bogus="Data!$B$2", Bogus_unit="Data!$C$2"))
    assert names(result, FindingKind.nameWithoutConcept) == {"Bogus", "Bogus_unit"}


def test_damaged_name_is_not_also_unbound():
    result = audit(workbookWith(Turnover="Missing!$B$2"))
    assert names(result, FindingKind.damagedName) == {"Turnover"}
    assert "Turnover" not in names(result, FindingKind.nameWithoutConcept)


def test_alias_is_informational():
    aliased = "NameOfOtherPollutantsTypedAxis"
    result = audit(workbookWith(**{aliased: "Data!$B$2:$B$5"}))
    assert names(result, FindingKind.aliasedName) == {aliased}
    assert all(f.kind is not FindingKind.aliasedName for f in result.problems)


def test_unnamed_hypercube_lists_its_dimensions():
    result = audit(workbookWith())
    [finding] = [
        f
        for f in result.findings
        if f.kind is FindingKind.hypercubeWithoutName
        and f.name == "AmountOfOtherPollutantEmissionTable"
    ]
    assert "NameOfOtherPollutantTypedAxis" in finding.detail


def test_layout_against_reference_ignores_labels():
    wb = workbookWith(
        Turnover="Data!$B$2", Assets="Data!$B$3", template_label_x="Data!$A$1"
    )
    reference = workbookWith(
        Turnover="Data!$B$9", Bogus="Data!$B$4", template_label_y="Data!$A$1"
    )
    result = audit(wb, reference)
    assert names(result, FindingKind.onlyInWorkbook) == {"Assets"}
    assert names(result, FindingKind.onlyInReference) == {"Bogus"}
    assert names(result, FindingKind.movedName) == {"Turnover"}


def test_taxonomy_from_schemaref():
    wb = workbookWith(template_reporting_schemaRef="Data!$A$1")
    wb["Data"]["A1"] = VS_ENTRY_POINT
    assert auditTemplate(wb).taxonomy is getTaxonomy(VS_ENTRY_POINT)


def test_unusable_schemaref():
    with pytest.raises(TaxonomyException):
        auditTemplate(workbookWith())


def test_script_exit_codes(script, tmp_path):
    path = tmp_path / "template.xlsx"
    wb = workbookWith(template_reporting_schemaRef="Data!$A$1", Bogus="Data!$B$2")
    wb["Data"]["A1"] = VS_ENTRY_POINT
    wb.save(path)
    assert script.main([str(path)]) == 1
    assert script.main([str(tmp_path / "missing.xlsx")]) == 2
