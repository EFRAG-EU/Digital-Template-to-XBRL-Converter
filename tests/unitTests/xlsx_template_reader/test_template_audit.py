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
OLD_VSME_ENTRY_POINT = "https://xbrl.efrag.org/taxonomy/vsme/2026-05-01/vsme-all.xsd"


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


def stamped(entryPoint: str, version: str, **names: str) -> Workbook:
    """A workbook whose schemaRef and template version cells say these."""
    wb = workbookWith(
        template_reporting_schemaRef="Data!$A$1",
        template_reporting_template_version="Data!$A$2",
        **names,
    )
    wb["Data"]["A1"] = entryPoint
    wb["Data"]["A2"] = version
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
    result = auditTemplate(stamped(VS_ENTRY_POINT, "2.0.0"))
    assert result.taxonomy is getTaxonomy(VS_ENTRY_POINT)
    assert not names(result, FindingKind.templateVersionMismatch)
    assert not names(result, FindingKind.oldEntryPoint)


def test_template_version_pinned_by_disclosure_system():
    result = auditTemplate(stamped(VS_ENTRY_POINT, "1.9.0"))
    assert names(result, FindingKind.templateVersionMismatch) == {"1.9.0"}


def test_old_entry_point_is_informational():
    """Its template version is "auto" (the migration tool's), so not checked."""
    result = auditTemplate(stamped(OLD_VSME_ENTRY_POINT, "1.3.0"))
    assert names(result, FindingKind.oldEntryPoint) == {OLD_VSME_ENTRY_POINT}
    assert not names(result, FindingKind.templateVersionMismatch)
    assert all(f.kind is not FindingKind.oldEntryPoint for f in result.problems)


def test_schemaref_in_no_disclosure_system():
    unknown = "https://example.com/taxonomy/other.xsd"
    result = audit(stamped(unknown, "1.0.0"))
    assert names(result, FindingKind.unknownEntryPoint) == {unknown}


def test_unusable_schemaref():
    with pytest.raises(TaxonomyException):
        auditTemplate(workbookWith())


def test_script_exit_codes(script, tmp_path):
    path = tmp_path / "template.xlsx"
    stamped(VS_ENTRY_POINT, "2.0.0", Bogus="Data!$B$2").save(path)
    assert script.main([str(path)]) == 1
    assert script.main([str(tmp_path / "missing.xlsx")]) == 2


def test_script_markdown(script, tmp_path, capsys):
    path = tmp_path / "template.xlsx"
    stamped(VS_ENTRY_POINT, "2.0.0", Bogus="Data!$B$2").save(path)
    assert script.main([str(path), "--markdown"]) == 1
    out = capsys.readouterr().out
    assert "| Item | Value | Check |" in out
    assert "| **Workbook** | `template.xlsx` |" in out
    assert "### Named ranges that bind to no taxonomy concept (1)" in out
    assert "| `Bogus` | Data!$B$2 |" in out
    assert "| :white_check_mark: supported entry point |" in out
    assert out.isascii()


def test_markdown_cells_escape_pipes(script):
    assert script.markdownCell("a|b\nc", code=True) == r"`a\|b c`"
