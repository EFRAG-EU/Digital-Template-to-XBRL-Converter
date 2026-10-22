"""Audit a Digital Template's named ranges against its taxonomy.

Checks the workbook's schemaRef and template version against the disclosure
system config, binds the workbook exactly as conversion does, then reports where
the template and taxonomy disagree: names that bind to nothing, reportable
concepts and hypercubes with no named range, and (given a reference template)
names added, removed or moved.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING

from mireport.conversionresults import ConversionResultsBuilder, Severity
from mireport.data.disclosures import (
    VSME_DEFAULTS,
    getDisclosureConfig,
    getTemplateVersionSetting,
)
from mireport.entrypoints import entryPointSetOf
from mireport.exceptions import TaxonomyException
from mireport.taxonomy import getTaxonomy
from mireport.version import VersionHolder
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
    unknownEntryPoint = "SchemaRef not in any disclosure system"
    oldEntryPoint = "SchemaRef is an old entry point"
    templateVersionMismatch = "Template version differs from the disclosure system's"
    nameWithoutConcept = "Named ranges that bind to no taxonomy concept"
    damagedName = "Damaged named ranges"
    conceptWithoutName = "Reportable concepts with no named range"
    hypercubeWithoutName = "Hypercubes with no table named range"
    bindingMessage = "Problems binding named ranges"
    onlyInWorkbook = "Named ranges only in this workbook"
    onlyInReference = "Named ranges only in the reference template"
    movedName = "Named ranges pointing at different cells from the reference"
    aliasedName = "Named ranges bound through a name alias"


# Old templates and workarounds still in use rather than problems to fix.
INFORMATIONAL_KINDS = frozenset({FindingKind.oldEntryPoint, FindingKind.aliasedName})


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

    findings = _disclosureSystemFindings(entryPoint, templateVersion)
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


def pinnedTemplateVersion(entryPoint: str) -> VersionHolder | None:
    """The template version the disclosure system config pins for this entry
    point; None when it leaves it to the migration tool ("auto"), turns
    migration off ("none") or doesn't list the entry point."""
    try:
        setting = getTemplateVersionSetting(entryPointSetOf(entryPoint))
    except TaxonomyException:
        return None
    return VersionHolder.parse_safe(setting or "")


def _disclosureSystemFindings(
    entryPoint: str, templateVersion: str
) -> list[TemplateFinding]:
    """Whether a disclosure system config lists the schemaRef, as its supported
    entry point or an old one, and pins the template version it has."""
    try:
        entryPointSet = entryPointSetOf(entryPoint)
    except TaxonomyException as e:
        return [TemplateFinding(FindingKind.unknownEntryPoint, entryPoint, str(e))]
    if (config := getDisclosureConfig(entryPointSet)) is None:
        return [TemplateFinding(FindingKind.unknownEntryPoint, entryPoint)]

    findings = []
    supported = config["taxonomyEntryPoints"]["supportedEntryPoint"]
    if entryPointSet != entryPointSetOf(supported):
        findings.append(
            TemplateFinding(
                FindingKind.oldEntryPoint, entryPoint, f"supported is {supported}"
            )
        )
    if (
        expected := pinnedTemplateVersion(entryPoint)
    ) is not None and VersionHolder.parse_or_invalid(templateVersion) != expected:
        findings.append(
            TemplateFinding(
                FindingKind.templateVersionMismatch,
                templateVersion or "(none)",
                f"{entryPoint} pins {expected}",
            )
        )
    return findings


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
