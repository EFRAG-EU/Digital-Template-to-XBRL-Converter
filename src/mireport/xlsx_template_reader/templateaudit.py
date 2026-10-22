"""Audit a Digital Template's named ranges against its taxonomy.

Binds the workbook exactly as conversion does, then reports where the template
and taxonomy disagree: names that bind to nothing, reportable concepts and
hypercubes with no named range, and (given a reference template) names added,
removed or moved.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING

from mireport.conversionresults import ConversionResultsBuilder, Severity
from mireport.data.disclosures import VSME_DEFAULTS
from mireport.taxonomy import getTaxonomy
from mireport.xlsx_template_reader._binder import WorkbookBinder
from mireport.xlsx_template_reader._constants import (
    IGNORED_DEFINED_NAME_PREFIXES,
    TAXONOMY_NAME_ALIASES,
    UNHANDLED_NAMES_TO_IGNORE,
)
from mireport.xlsx_template_reader._reader import WorkbookReader

if TYPE_CHECKING:
    from openpyxl import Workbook

    from mireport.taxonomy import Concept, Taxonomy
    from mireport.xlsx_template_reader._bindings import WorkbookBindings


class FindingKind(StrEnum):
    nameWithoutConcept = "Named ranges that bind to no taxonomy concept"
    damagedName = "Damaged named ranges"
    conceptWithoutName = "Reportable concepts with no named range"
    hypercubeWithoutName = "Hypercubes with no table named range"
    bindingMessage = "Problems binding named ranges"
    onlyInWorkbook = "Named ranges only in this workbook"
    onlyInReference = "Named ranges only in the reference template"
    movedName = "Named ranges pointing at different cells from the reference"
    aliasedName = "Named ranges bound through a name alias"


# Workarounds still in use rather than problems to fix.
INFORMATIONAL_KINDS = frozenset({FindingKind.aliasedName})


@dataclass(frozen=True)
class TemplateFinding:
    kind: FindingKind
    name: str
    detail: str = ""


@dataclass(frozen=True)
class TemplateAudit:
    entryPoint: str
    templateVersion: str
    taxonomy: Taxonomy
    findings: tuple[TemplateFinding, ...]

    @property
    def problems(self) -> tuple[TemplateFinding, ...]:
        return tuple(f for f in self.findings if f.kind not in INFORMATIONAL_KINDS)


def auditTemplate(
    workbook: Workbook,
    *,
    taxonomy: Taxonomy | None = None,
    reference: Workbook | None = None,
) -> TemplateAudit:
    """Audit the workbook against the taxonomy its schemaRef names, or against
    ``taxonomy`` when given. Raises TaxonomyException when the schemaRef is
    unusable or names a taxonomy that isn't loaded."""
    # The reader's own warnings (damaged and unused names) become findings of
    # their own, so keep them apart from the binder's messages.
    reader = WorkbookReader(workbook, ConversionResultsBuilder(consoleOutput=False))
    entryPoint = reader.value(VSME_DEFAULTS["entryPoint"]).as_str_stripped()
    templateVersion = reader.value(
        "template_reporting_template_version"
    ).as_str_stripped()
    if taxonomy is None:
        taxonomy = getTaxonomy(entryPoint)

    bindResults = ConversionResultsBuilder(consoleOutput=False)
    bindings = WorkbookBinder(reader, taxonomy, bindResults).bind()

    findings: list[TemplateFinding] = []
    damaged = reader.damaged_defined_names
    findings.extend(
        TemplateFinding(FindingKind.damagedName, dn.name, dn.attr_text)
        for dn in damaged
    )
    findings.extend(
        TemplateFinding(FindingKind.nameWithoutConcept, dn.name, dn.attr_text)
        for dn in reader.unused_defined_names - damaged
        if dn.name not in UNHANDLED_NAMES_TO_IGNORE
    )
    findings.extend(_unnamedConcepts(taxonomy, bindings))
    findings.extend(
        TemplateFinding(
            FindingKind.bindingMessage, m.excelReference or "", m.messageText
        )
        for m in bindResults.getMessages(
            wantedMessageSeverities={Severity.WARNING, Severity.ERROR}
        )
    )
    findings.extend(
        TemplateFinding(FindingKind.aliasedName, name, f"bound as {target}")
        for name, target in TAXONOMY_NAME_ALIASES.items()
        if name in workbook.defined_names
    )
    if reference is not None:
        findings.extend(_layoutDifferences(workbook, reference))

    kinds = list(FindingKind)
    findings.sort(key=lambda f: (kinds.index(f.kind), f.name, f.detail))
    return TemplateAudit(entryPoint, templateVersion, taxonomy, tuple(findings))


def _unnamedConcepts(
    taxonomy: Taxonomy, bindings: WorkbookBindings
) -> list[TemplateFinding]:
    bound: set[Concept] = {crm.concept for crm in bindings.concept_map.values()}
    bound.update(bindings.unit_map)
    for table in bindings.tables:
        bound.update(crm.concept for crm in table.conceptRanges)

    findings = [
        TemplateFinding(FindingKind.conceptWithoutName, c.qname.localName)
        for c in taxonomy.concepts
        if c.isReportable
        and c not in bound
        and taxonomy.getPresentationGroupsForConcept(c)
    ]
    for hypercube in taxonomy.hypercubes - taxonomy.emptyHypercubes - bound:
        dimensions = sorted(
            d.qname.localName for d in taxonomy.getAllDimensionsForHypercube(hypercube)
        )
        findings.append(
            TemplateFinding(
                FindingKind.hypercubeWithoutName,
                hypercube.qname.localName,
                f"dimensions: {', '.join(dimensions)}",
            )
        )
    return findings


def _layoutDifferences(
    workbook: Workbook, reference: Workbook
) -> list[TemplateFinding]:
    """Compare the non-label named ranges of two workbooks by name and target."""
    ours, theirs = _conceptNames(workbook), _conceptNames(reference)
    findings = [
        TemplateFinding(FindingKind.onlyInWorkbook, name, ours[name])
        for name in ours.keys() - theirs.keys()
    ]
    findings.extend(
        TemplateFinding(FindingKind.onlyInReference, name, theirs[name])
        for name in theirs.keys() - ours.keys()
    )
    findings.extend(
        TemplateFinding(
            FindingKind.movedName, name, f"{ours[name]} (reference {theirs[name]})"
        )
        for name in ours.keys() & theirs.keys()
        if ours[name] != theirs[name]
    )
    return findings


def _conceptNames(workbook: Workbook) -> dict[str, str]:
    return {
        name: dn.attr_text
        for name, dn in workbook.defined_names.items()
        if not name.startswith(IGNORED_DEFINED_NAME_PREFIXES)
    }
