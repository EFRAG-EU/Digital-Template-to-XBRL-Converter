"""Unit tests for the taxonomy extraction logic.

Success paths that require genuine Arelle lxml-backed objects are covered
end-to-end by tests/integrationTests/test_taxonomy_info_regeneration.py;
these tests cover extraction logic using lightweight stubs (see
test_model_access.py for the same approach).
"""

from __future__ import annotations

import json
import logging
from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest
from arelle import XbrlConst
from arelle.Cntlr import Cntlr
from arelle.ModelDtsObject import ModelConcept
from arelle.ModelValue import QName
from arelle.ModelXbrl import ModelXbrl
from arelle.RuntimeOptions import RuntimeOptions

from mireport.arelle.diagnostics import ArelleDiagnostic, DiagnosticCollector
from mireport.arelle.model_access import (
    ConceptRelationship,
    ConceptRelationshipSet,
    ResourceRelationship,
    ValidatedModel,
)
from mireport.arelle.support import ArelleModelInconsistency
from mireport.arelle.taxonomy_extraction import (
    DefinitionRow,
    PresentationRow,
    TaxonomyInfoExtractor,
    writeDataFile,
)


def qn(local: str = "Thing", ns: str = "https://example.com/vsme") -> QName:
    return QName("vsme", ns, local)


class StubCntlr:
    def __init__(self) -> None:
        self.logMessages: list[str] = []

    def addToLog(self, message: str, **kwargs: Any) -> None:
        self.logMessages.append(message)


class StubConcept:
    def __init__(
        self,
        qname: QName,
        *,
        isEnumeration2Item: bool = False,
        enumLinkrole: str | None = None,
        enumDomainQname: QName | None = None,
        isExplicitDimension: bool = False,
        isHypercubeItem: bool = False,
    ) -> None:
        self.qname = qname
        self.isEnumeration2Item = isEnumeration2Item
        self.enumLinkrole = enumLinkrole
        self.enumDomainQname = enumDomainQname
        self.isExplicitDimension = isExplicitDimension
        self.isHypercubeItem = isHypercubeItem


class StubLabelResource:
    def __init__(self, role: str | None, lang: str | None, value: str) -> None:
        self.role = role
        self.xmlLang = lang
        self.stringValue = value


class StubRoleType:
    def __init__(self, roleURI: str = "https://example.com/role") -> None:
        self.roleURI = roleURI
        self.definition = "A role"


class StubValidatedModel:
    """Stands in for ValidatedModel; serves canned ResourceRelationships."""

    def __init__(
        self,
        relsByArcrole: dict[str, list[ResourceRelationship]],
        conceptRelSets: dict[Any, Any] | None = None,
        linkrolesByArcrole: dict[str, list[str]] | None = None,
        baseSets: list[tuple[str, str]] | None = None,
        items: list[tuple[QName, Any]] | None = None,
    ) -> None:
        self._relsByArcrole = relsByArcrole
        self._conceptRelSets = conceptRelSets or {}
        self._linkrolesByArcrole = linkrolesByArcrole or {}
        self._baseSets = baseSets or []
        self._items = items or []
        self._conceptsByQName = dict(self._items)

    def resourceRelationshipsFrom(
        self, source: Any, arcrole: str
    ) -> list[ResourceRelationship]:
        return self._relsByArcrole[arcrole]

    def conceptRelationshipSet(self, arcroles: Any, linkrole: str) -> Any:
        # New tests key conceptRelSets by (arcroles, linkrole) to disambiguate
        # several arcroles sharing one linkrole; older tests key by linkrole
        # alone since they only ever look up one arcrole per linkrole.
        key = (arcroles, linkrole)
        if key in self._conceptRelSets:
            return self._conceptRelSets[key]
        return self._conceptRelSets[linkrole]

    def linkrolesFor(self, *arcroles: str) -> list[str]:
        return [
            linkrole
            for arcrole in arcroles
            for linkrole in self._linkrolesByArcrole.get(arcrole, [])
        ]

    def baseSetsInDTS(self) -> list[tuple[str, str]]:
        return list(self._baseSets)

    def itemConcepts(self) -> Iterator[tuple[QName, Any]]:
        yield from self._items

    def concept(self, qname: QName) -> Any:
        return self._conceptsByQName[qname]


def labelRel(resource: StubLabelResource) -> ResourceRelationship:
    return ResourceRelationship(
        resource=cast(Any, resource), role=resource.role, order=1.0
    )


def makeExtractor(
    relsByArcrole: dict[str, list[ResourceRelationship]],
    conceptRelSets: dict[Any, Any] | None = None,
    linkrolesByArcrole: dict[str, list[str]] | None = None,
    baseSets: list[tuple[str, str]] | None = None,
    items: list[tuple[QName, Any]] | None = None,
) -> tuple[TaxonomyInfoExtractor, str]:
    """Build an extractor over stubs, with a diagnostics collector attached."""
    token = DiagnosticCollector.open()
    stubModel = SimpleNamespace(qnameConcepts={}, qnameTypes={})
    options = SimpleNamespace(diagnosticsToken=token)
    extractor = TaxonomyInfoExtractor(
        cast(Cntlr, StubCntlr()),
        cast(RuntimeOptions, options),
        cast(ModelXbrl, stubModel),
    )
    extractor.model = cast(
        ValidatedModel,
        StubValidatedModel(
            relsByArcrole, conceptRelSets, linkrolesByArcrole, baseSets, items
        ),
    )
    return extractor, token


def collectedDiagnostics(token: str) -> list[ArelleDiagnostic]:
    return DiagnosticCollector.close(token)


class TestWriteDataFile:
    def write(self, tmp_path: Path, data: dict, **kwargs: Any) -> str:
        jsonPath = tmp_path / "out.json"
        writeDataFile(cast(Cntlr, StubCntlr()), jsonPath, "Test", data, **kwargs)
        return jsonPath.read_text(encoding="UTF-8")

    def test_str_path_also_accepted(self, tmp_path: Path) -> None:
        # The plugin receives the path as a str via Arelle RuntimeOptions
        # (RuntimeOptionValue does not admit Path).
        jsonPath = tmp_path / "out.json"
        writeDataFile(cast(Cntlr, StubCntlr()), str(jsonPath), "Test", {"a": 1})
        assert jsonPath.exists()

    def test_output_is_pretty_printed_with_sorted_keys(self, tmp_path: Path) -> None:
        data = {"b": [1, 2], "a": {"y": 1, "x": 2}}
        written = self.write(tmp_path, data)
        assert written == json.dumps(data, indent=2, sort_keys=True)

    def test_arelle_qname_value_serialises_to_string(self, tmp_path: Path) -> None:
        written = self.write(tmp_path, {"concept": qn("Thing")})
        assert json.loads(written) == {"concept": "vsme:Thing"}

    def test_qname_key_raises(self, tmp_path: Path) -> None:
        # The Taxonomy payload has its QName keys stringified by
        # convertRecursive before it gets here; anything else slipping
        # through is a bug and must fail loudly, not be silently tidied.
        with pytest.raises(TypeError):
            self.write(tmp_path, {qn("Thing"): "value"})

    def test_empty_data_writes_no_file(self, tmp_path: Path) -> None:
        jsonPath = tmp_path / "out.json"
        cntlr = StubCntlr()
        writeDataFile(cast(Cntlr, cntlr), jsonPath, "Test", {})
        assert not jsonPath.exists()
        assert cntlr.logMessages == ["No Test data to write"]


class TestAddLabels:
    def addLabels(
        self, labelRels: list[ResourceRelationship]
    ) -> tuple[dict, list[ArelleDiagnostic]]:
        extractor, token = makeExtractor({XbrlConst.conceptLabel: labelRels})
        jconcept: dict[str, Any] = {}
        extractor.addLabels(cast(ModelConcept, StubConcept(qn())), jconcept)
        return jconcept, collectedDiagnostics(token)

    def test_label_with_role_is_stored_under_that_role(self) -> None:
        role = "http://www.xbrl.org/2003/role/terseLabel"
        jconcept, diagnostics = self.addLabels(
            [labelRel(StubLabelResource(role, "en", "Terse"))]
        )
        assert jconcept["labels"]["en"] == {role: "Terse"}
        assert diagnostics == []

    def test_label_without_role_falls_back_to_standard_label_role(self) -> None:
        jconcept, diagnostics = self.addLabels(
            [labelRel(StubLabelResource(None, "en", "Assets"))]
        )
        assert jconcept["labels"]["en"] == {XbrlConst.standardLabel: "Assets"}
        assert diagnostics == []

    def test_lang_is_normalised_to_lower_case(self) -> None:
        jconcept, _ = self.addLabels(
            [labelRel(StubLabelResource(XbrlConst.standardLabel, "en-GB", "Assets"))]
        )
        assert list(jconcept["labels"].keys()) == ["en-gb"]

    def test_label_value_is_stripped(self) -> None:
        jconcept, _ = self.addLabels(
            [labelRel(StubLabelResource(XbrlConst.standardLabel, "en", "  Assets\n"))]
        )
        assert jconcept["labels"]["en"] == {XbrlConst.standardLabel: "Assets"}

    def test_label_without_lang_is_ignored_with_diagnostic(self) -> None:
        jconcept, diagnostics = self.addLabels(
            [labelRel(StubLabelResource(XbrlConst.standardLabel, None, "Assets"))]
        )
        assert not jconcept["labels"]
        assert len(diagnostics) == 1
        assert "no xml:lang" in diagnostics[0].text

    def test_inconsistent_duplicate_labels_keep_longer_with_diagnostic(self) -> None:
        role = XbrlConst.standardLabel
        jconcept, diagnostics = self.addLabels(
            [
                labelRel(StubLabelResource(role, "en", "Assets, total")),
                labelRel(StubLabelResource(role, "en", "Assets")),
            ]
        )
        assert jconcept["labels"]["en"] == {role: "Assets, total"}
        assert len(diagnostics) == 1
        assert "duplicate labels" in diagnostics[0].text

    def test_consistent_duplicate_labels_are_silent(self) -> None:
        role = XbrlConst.standardLabel
        jconcept, diagnostics = self.addLabels(
            [
                labelRel(StubLabelResource(role, "en", "Assets")),
                labelRel(StubLabelResource(role, "en", "Assets")),
            ]
        )
        assert jconcept["labels"]["en"] == {role: "Assets"}
        assert diagnostics == []


def conceptRel(
    target: StubConcept,
    *,
    isUsable: bool = True,
    preferredLabel: str | None = None,
) -> ConceptRelationship:
    assert target.qname is not None
    return ConceptRelationship(
        target=cast(Any, target),
        targetQName=target.qname,
        consecutiveLinkrole="https://example.com/elr",
        isUsable=isUsable,
        preferredLabel=preferredLabel,
        contextElement=None,
        isClosed=False,
    )


class StubConceptRelationshipSet:
    """Serves canned ConceptRelationships; consecutiveSet stays in this set."""

    def __init__(self, relsFrom: dict[int, list[ConceptRelationship]]) -> None:
        self._relsFrom = relsFrom

    def relationshipsFrom(self, concept: Any) -> list[ConceptRelationship]:
        return self._relsFrom.get(id(concept), [])

    def consecutiveSet(self, rel: ConceptRelationship) -> StubConceptRelationshipSet:
        return self


class StubDomainMemberRelSet:
    """Serves canned domain-member relationships; consecutiveSet stays in
    this set, and hasRelationshipsFrom/To are exposed like the real
    ConceptRelationshipSet."""

    def __init__(
        self,
        relsFrom: dict[int, list[ConceptRelationship]],
        targets: set[int] | None = None,
    ) -> None:
        self._relsFrom = relsFrom
        self._targets = targets or set()

    def hasRelationshipsFrom(self, concept: Any) -> bool:
        return bool(self._relsFrom.get(id(concept)))

    def hasRelationshipsTo(self, concept: Any) -> bool:
        return id(concept) in self._targets

    def relationshipsFrom(self, concept: Any) -> list[ConceptRelationship]:
        return self._relsFrom.get(id(concept), [])

    def consecutiveSet(self, rel: ConceptRelationship) -> StubDomainMemberRelSet:
        return self


class TestTreeWalks:
    def makeWalker(self) -> TaxonomyInfoExtractor:
        extractor, token = makeExtractor({})
        collectedDiagnostics(token)
        return extractor

    def test_definition_walk_is_depth_first_with_usability(self) -> None:
        a = StubConcept(qn("A"))
        b = StubConcept(qn("B"))
        c = StubConcept(qn("C"))
        d = StubConcept(qn("D"))
        relSet = StubConceptRelationshipSet(
            {
                id(a): [conceptRel(b), conceptRel(d, isUsable=False)],
                id(b): [conceptRel(c)],
            }
        )
        rows = list(
            self.makeWalker().walkDefinitionChildren(
                cast(ModelConcept, a), cast(ConceptRelationshipSet, relSet), 1
            )
        )
        assert rows == [
            DefinitionRow(1, qn("B"), True),
            DefinitionRow(2, qn("C"), True),
            DefinitionRow(1, qn("D"), False),
        ]

    def test_definition_walk_of_leaf_is_empty(self) -> None:
        leaf = StubConcept(qn("Leaf"))
        relSet = StubConceptRelationshipSet({})
        rows = list(
            self.makeWalker().walkDefinitionChildren(
                cast(ModelConcept, leaf), cast(ConceptRelationshipSet, relSet), 1
            )
        )
        assert rows == []

    def test_presentation_walk_carries_preferred_labels(self) -> None:
        root = StubConcept(qn("Root"))
        child = StubConcept(qn("Child"))
        grandchild = StubConcept(qn("Grandchild"))
        terse = "http://www.xbrl.org/2003/role/terseLabel"
        relSet = StubConceptRelationshipSet(
            {
                id(root): [conceptRel(child, preferredLabel=terse)],
                id(child): [conceptRel(grandchild)],
            }
        )
        rows = list(
            self.makeWalker().walkPresentationChildren(
                cast(ModelConcept, root), cast(ConceptRelationshipSet, relSet), 1
            )
        )
        assert rows == [
            PresentationRow(1, qn("Child"), terse),
            PresentationRow(2, qn("Grandchild"), None),
        ]


class TestGetDomainMembersForEnumeration:
    ELR = "https://example.com/elr"
    ENUM_CONCEPT = qn("Choice")

    def getDomainMembers(
        self,
        headUsable: bool,
        domainHeadConcept: StubConcept,
        relSet: StubDomainMemberRelSet,
        *,
        linkroleHasDomainMember: bool = True,
    ) -> tuple[list[QName], list[ArelleDiagnostic]]:
        linkrolesByArcrole = (
            {XbrlConst.domainMember: [self.ELR]} if linkroleHasDomainMember else {}
        )
        extractor, token = makeExtractor(
            {}, {self.ELR: relSet}, linkrolesByArcrole=linkrolesByArcrole
        )
        result = extractor.getDomainMembersForEnumeration(
            self.ELR,
            headUsable,
            cast(ModelConcept, domainHeadConcept),
            self.ENUM_CONCEPT,
        )
        return result, collectedDiagnostics(token)

    def test_undeclared_linkrole_warns_and_resolves_no_members(self) -> None:
        head = StubConcept(qn("Domain"))
        relSet = StubDomainMemberRelSet({})
        result, diagnostics = self.getDomainMembers(
            False, head, relSet, linkroleHasDomainMember=False
        )
        assert result == []
        assert len(diagnostics) == 2
        assert "no domain-member relationships" in diagnostics[0].text
        assert "no usable domain members" in diagnostics[1].text

    def test_head_with_no_outgoing_relationships_warns(self) -> None:
        head = StubConcept(qn("Domain"))
        relSet = StubDomainMemberRelSet({})
        result, diagnostics = self.getDomainMembers(True, head, relSet)
        assert result == [qn("Domain")]
        assert len(diagnostics) == 1
        assert diagnostics[0].level == logging.WARNING
        assert "no outgoing domain-member relationships" in diagnostics[0].text

    def test_head_not_a_root_is_informational(self) -> None:
        # Unlike having no outgoing relationships, this doesn't stop the
        # domain from resolving -- it's a curiosity, not a defect.
        head = StubConcept(qn("Domain"))
        member = StubConcept(qn("Member"))
        relSet = StubDomainMemberRelSet(
            {id(head): [conceptRel(member)]}, targets={id(head)}
        )
        result, diagnostics = self.getDomainMembers(True, head, relSet)
        assert result == [qn("Domain"), qn("Member")]
        assert len(diagnostics) == 1
        assert diagnostics[0].level == logging.INFO
        assert "not a root" in diagnostics[0].text

    def test_well_formed_domain_is_silent(self) -> None:
        head = StubConcept(qn("Domain"))
        member = StubConcept(qn("Member"))
        relSet = StubDomainMemberRelSet({id(head): [conceptRel(member)]})
        result, diagnostics = self.getDomainMembers(False, head, relSet)
        assert result == [qn("Member")]
        assert diagnostics == []

    def test_no_usable_members_warns(self) -> None:
        head = StubConcept(qn("Domain"))
        member = StubConcept(qn("Member"))
        relSet = StubDomainMemberRelSet(
            {id(head): [conceptRel(member, isUsable=False)]}
        )
        result, diagnostics = self.getDomainMembers(False, head, relSet)
        assert result == []
        assert len(diagnostics) == 1
        assert diagnostics[0].level == logging.WARNING
        assert "no usable domain members" in diagnostics[0].text


class TestGetLabelsForRoleType:
    def getLabels(
        self, labelRels: list[ResourceRelationship]
    ) -> tuple[dict[str, str], list[ArelleDiagnostic]]:
        extractor, token = makeExtractor({XbrlConst.elementLabel: labelRels})
        labels = extractor.getLabelsForRoleType(cast(Any, StubRoleType()))
        return labels, collectedDiagnostics(token)

    def test_labels_keyed_by_lower_cased_lang(self) -> None:
        labels, diagnostics = self.getLabels(
            [
                labelRel(StubLabelResource(XbrlConst.standardLabel, "en-GB", "Energy")),
                labelRel(StubLabelResource(XbrlConst.standardLabel, "fr", "Énergie")),
            ]
        )
        assert labels == {"en-gb": "Energy", "fr": "Énergie"}
        assert diagnostics == []

    def test_label_without_lang_is_skipped(self) -> None:
        labels, _diagnostics = self.getLabels(
            [labelRel(StubLabelResource(XbrlConst.standardLabel, None, "Energy"))]
        )
        assert labels == {}

    def test_inconsistent_duplicate_labels_keep_longer_with_diagnostic(self) -> None:
        labels, diagnostics = self.getLabels(
            [
                labelRel(StubLabelResource(XbrlConst.standardLabel, "en", "Energy")),
                labelRel(
                    StubLabelResource(XbrlConst.standardLabel, "en", "Energy usage")
                ),
            ]
        )
        assert labels == {"en": "Energy usage"}
        assert len(diagnostics) == 1
        assert "duplicate labels" in diagnostics[0].text


class StubHypercubeDimensionRelSet:
    """Serves canned roots/relationships for the hypercube-dimension arcrole."""

    def __init__(
        self,
        roots: list[StubConcept],
        relsFrom: dict[int, list[ConceptRelationship]],
        targets: set[int] | None = None,
    ) -> None:
        self._roots = roots
        self._relsFrom = relsFrom
        self._targets = targets or set()

    def rootConcepts(self) -> list[StubConcept]:
        return self._roots

    def hasRelationshipsFrom(self, concept: Any) -> bool:
        return bool(self._relsFrom.get(id(concept)))

    def hasRelationshipsTo(self, concept: Any) -> bool:
        return id(concept) in self._targets

    def relationshipsFrom(self, concept: Any) -> list[ConceptRelationship]:
        return self._relsFrom.get(id(concept), [])


class TestGetDimensions:
    ELR = "https://example.com/elr"

    def getDimensions(
        self,
        hypercube: StubConcept,
        hypercubeIsClosed: bool,
        relSet: StubHypercubeDimensionRelSet,
    ) -> tuple[list[ConceptRelationship], list[ArelleDiagnostic]]:
        extractor, token = makeExtractor({}, {self.ELR: relSet})
        result = extractor.getDimensions(
            self.ELR, cast(ModelConcept, hypercube), hypercubeIsClosed
        )
        return result, collectedDiagnostics(token)

    def test_hypercube_with_no_dimensions_returns_empty(self) -> None:
        # A table with zero dimensions is unusual but valid: it just has no
        # outgoing hypercube-dimension relationships, and so is absent from
        # rootConcepts() entirely (whether or not other, dimensioned,
        # hypercubes share the same ELR).
        table = StubConcept(qn("EmptyTable"))
        other = StubConcept(qn("OtherTable"))
        dimension = StubConcept(qn("SomeDimension"))
        relSet = StubHypercubeDimensionRelSet(
            roots=[other],
            relsFrom={id(other): [conceptRel(dimension)]},
        )
        result, diagnostics = self.getDimensions(table, False, relSet)
        assert result == []
        assert diagnostics == []

    def test_closed_hypercube_with_no_dimensions_warns(self) -> None:
        table = StubConcept(qn("EmptyTable"))
        relSet = StubHypercubeDimensionRelSet(roots=[], relsFrom={})
        result, diagnostics = self.getDimensions(table, True, relSet)
        assert result == []
        assert len(diagnostics) == 1
        assert "no dimensions" in diagnostics[0].text

    def test_hypercube_with_dimensions_returns_relationships(self) -> None:
        # A second hypercube-dimension root sharing the ELR does not, by
        # itself, cause getDimensions() to emit anything: reporting on an
        # ELR having multiple hypercubes is TaxonomyInfoExtractor's job (see
        # TestReportHypercubesForLinkrole), not this method's.
        table = StubConcept(qn("Table"))
        other = StubConcept(qn("OtherTable"))
        dimension = StubConcept(qn("Dimension"))
        rel = conceptRel(dimension)
        relSet = StubHypercubeDimensionRelSet(
            roots=[table, other], relsFrom={id(table): [rel]}
        )
        result, diagnostics = self.getDimensions(table, True, relSet)
        assert result == [rel]
        assert diagnostics == []

    def test_hypercube_not_a_root_but_with_relationships_is_inconsistent(self) -> None:
        # A concept that has outgoing hypercube-dimension relationships but
        # is also the target of one (i.e. used as a dimension itself)
        # indicates real model corruption, distinct from simply having no
        # dimensions.
        table = StubConcept(qn("Table"))
        rel = conceptRel(StubConcept(qn("Dimension")))
        relSet = StubHypercubeDimensionRelSet(
            roots=[],
            relsFrom={id(table): [rel]},
            targets={id(table)},
        )
        with pytest.raises(ArelleModelInconsistency):
            self.getDimensions(table, True, relSet)


class TestReportHypercubesForLinkrole:
    ELR = "https://example.com/elr"

    def report(
        self, primaryItemsByHypercube: dict[QName, set[QName]]
    ) -> list[ArelleDiagnostic]:
        extractor, token = makeExtractor({})
        extractor.reportHypercubesForLinkrole(self.ELR, primaryItemsByHypercube)
        return collectedDiagnostics(token)

    def test_single_hypercube_is_silent(self) -> None:
        diagnostics = self.report({qn("Table"): {qn("Item")}})
        assert diagnostics == []

    def test_no_hypercubes_is_silent(self) -> None:
        diagnostics = self.report({})
        assert diagnostics == []

    def test_disjoint_primary_items_is_informational(self) -> None:
        diagnostics = self.report(
            {
                qn("TableA"): {qn("ItemA")},
                qn("TableB"): {qn("ItemB")},
            }
        )
        assert len(diagnostics) == 1
        [diagnostic] = diagnostics
        assert diagnostic.level == logging.INFO
        assert "2 hypercubes" in diagnostic.text
        assert diagnostic.concepts == (qn("TableA"), qn("TableB"))
        assert "primaryItems" not in diagnostic.details

    def test_shared_primary_item_warns(self) -> None:
        diagnostics = self.report(
            {
                qn("TableA"): {qn("Item"), qn("ItemA")},
                qn("TableB"): {qn("Item"), qn("ItemB")},
            }
        )
        assert len(diagnostics) == 1
        [diagnostic] = diagnostics
        assert diagnostic.level == logging.WARNING
        assert "sharing primary items" in diagnostic.text
        assert diagnostic.concepts == (qn("TableA"), qn("TableB"))
        assert diagnostic.details["primaryItems"] == [qn("Item")]

    def test_only_overlapping_item_is_named_among_three_hypercubes(self) -> None:
        diagnostics = self.report(
            {
                qn("TableA"): {qn("Shared"), qn("ItemA")},
                qn("TableB"): {qn("Shared"), qn("ItemB")},
                qn("TableC"): {qn("ItemC")},
            }
        )
        assert len(diagnostics) == 1
        [diagnostic] = diagnostics
        assert diagnostic.level == logging.WARNING
        assert diagnostic.concepts == (qn("TableA"), qn("TableB"), qn("TableC"))
        assert diagnostic.details["primaryItems"] == [qn("Shared")]

    def test_within_hypercube_repeats_do_not_count_as_overlap(self) -> None:
        # A primary item appearing at multiple depths within the *same*
        # hypercube's tree is not the conjoined-hypercubes problem: the caller
        # already de-duplicates into a set per hypercube, but this pins that
        # collapsing a single hypercube's own repeats never trips the warning.
        diagnostics = self.report(
            {
                qn("TableA"): {qn("Item"), qn("ItemA")},
                qn("TableB"): {qn("ItemB")},
            }
        )
        assert len(diagnostics) == 1
        [diagnostic] = diagnostics
        assert diagnostic.level == logging.INFO


class TestExtractDimensionDefinitionsHypercubeCollision:
    """Two different root primary items targeting the same hypercube in one
    ELR is a shape mireport doesn't understand (which root's primary items
    apply?) and would otherwise silently overwrite the first root's cube
    entry -- extractDimensionDefinitions() must give up rather than guess."""

    ELR = "https://example.com/elr"

    def test_two_roots_targeting_same_hypercube_is_inconsistent(self) -> None:
        rootA = StubConcept(qn("RootA"))
        rootB = StubConcept(qn("RootB"))
        table = StubConcept(qn("Table"), isHypercubeItem=True)
        relA = conceptRel(table)
        relB = conceptRel(table)
        allNotAllRelSet = StubHypercubeDimensionRelSet(
            roots=[rootA, rootB],
            relsFrom={id(rootA): [relA], id(rootB): [relB]},
        )
        extractor, token = makeExtractor(
            {},
            {
                ((XbrlConst.all, XbrlConst.notAll), self.ELR): allNotAllRelSet,
                (XbrlConst.domainMember, self.ELR): StubDomainMemberRelSet({}),
                (XbrlConst.hypercubeDimension, self.ELR): (
                    StubHypercubeDimensionRelSet(roots=[], relsFrom={})
                ),
            },
            linkrolesByArcrole={XbrlConst.all: [self.ELR]},
        )
        try:
            with pytest.raises(ArelleModelInconsistency):
                extractor.extractDimensionDefinitions()
        finally:
            collectedDiagnostics(token)


class TestReportDomainMemberOnlyLinkroleRoots:
    ELR = "https://example.com/elr"

    def report(
        self, baseSets: list[tuple[str, str]], conceptRelSets: dict[Any, Any]
    ) -> list[ArelleDiagnostic]:
        extractor, token = makeExtractor({}, conceptRelSets, baseSets=baseSets)
        extractor.reportDomainMemberOnlyLinkroleRoots()
        return collectedDiagnostics(token)

    def test_single_root_is_silent(self) -> None:
        relSet = StubHypercubeDimensionRelSet(
            roots=[StubConcept(qn("Root"))], relsFrom={}
        )
        diagnostics = self.report(
            [(XbrlConst.domainMember, self.ELR)], {self.ELR: relSet}
        )
        assert diagnostics == []

    def test_multiple_roots_warns(self) -> None:
        roots = [StubConcept(qn("RootA")), StubConcept(qn("RootB"))]
        relSet = StubHypercubeDimensionRelSet(roots=roots, relsFrom={})
        diagnostics = self.report(
            [(XbrlConst.domainMember, self.ELR)], {self.ELR: relSet}
        )
        assert len(diagnostics) == 1
        [diagnostic] = diagnostics
        assert "Domain-member-only" in diagnostic.text
        assert "multiple (2) roots" in diagnostic.text
        assert diagnostic.elr == self.ELR
        assert diagnostic.concepts == (qn("RootA"), qn("RootB"))

    def test_elr_with_dimensional_arcrole_is_skipped(self) -> None:
        # A domain-member set that also holds a dimension-domain arc (i.e.
        # it's the domain of an explicit dimension, not a standalone
        # hierarchy) is not this check's concern.
        roots = [StubConcept(qn("RootA")), StubConcept(qn("RootB"))]
        relSet = StubHypercubeDimensionRelSet(roots=roots, relsFrom={})
        diagnostics = self.report(
            [
                (XbrlConst.domainMember, self.ELR),
                (XbrlConst.dimensionDomain, self.ELR),
            ],
            {self.ELR: relSet},
        )
        assert diagnostics == []

    def test_elr_without_domain_member_is_skipped(self) -> None:
        diagnostics = self.report([(XbrlConst.parentChild, self.ELR)], {})
        assert diagnostics == []


class TestPresentedDimensionalDomainMembers:
    ELR = "https://example.com/elr"

    def excluded(
        self,
        items: list[tuple[QName, Any]],
        conceptRelSets: dict[Any, Any],
        presented: set[QName],
        linkrolesByArcrole: dict[str, list[str]] | None = None,
    ) -> frozenset[QName]:
        extractor, token = makeExtractor(
            {}, conceptRelSets, linkrolesByArcrole=linkrolesByArcrole, items=items
        )
        result = extractor._presentedDimensionalDomainMembers(presented)
        collectedDiagnostics(token)
        return result

    def test_enum2_domain_excluded_when_presented(self) -> None:
        enumConcept = StubConcept(
            qn("Choice"),
            isEnumeration2Item=True,
            enumLinkrole=self.ELR,
            enumDomainQname=qn("Domain"),
        )
        domainHead = StubConcept(qn("Domain"))
        member = StubConcept(qn("Member"))
        domainMemberRelSet = StubDomainMemberRelSet(
            {id(domainHead): [conceptRel(member)]}
        )
        result = self.excluded(
            items=[
                (qn("Choice"), enumConcept),
                (qn("Domain"), domainHead),
                (qn("Member"), member),
            ],
            conceptRelSets={(XbrlConst.domainMember, self.ELR): domainMemberRelSet},
            presented={qn("Choice")},
        )
        assert result == frozenset({qn("Domain"), qn("Member")})

    def test_enum2_domain_not_excluded_when_not_presented(self) -> None:
        enumConcept = StubConcept(
            qn("Choice"),
            isEnumeration2Item=True,
            enumLinkrole=self.ELR,
            enumDomainQname=qn("Domain"),
        )
        domainHead = StubConcept(qn("Domain"))
        domainMemberRelSet = StubDomainMemberRelSet({})
        result = self.excluded(
            items=[(qn("Choice"), enumConcept), (qn("Domain"), domainHead)],
            conceptRelSets={(XbrlConst.domainMember, self.ELR): domainMemberRelSet},
            presented=set(),
        )
        assert result == frozenset()

    def test_explicit_dimension_domain_excluded_when_presented(self) -> None:
        dimension = StubConcept(qn("Axis"), isExplicitDimension=True)
        domainHead = StubConcept(qn("Domain"))
        member = StubConcept(qn("Member"))
        dimDomainRel = conceptRel(domainHead)
        dimensionDomainRelSet = StubDomainMemberRelSet({id(dimension): [dimDomainRel]})
        domainMemberRelSet = StubDomainMemberRelSet(
            {id(domainHead): [conceptRel(member)]}
        )
        result = self.excluded(
            items=[
                (qn("Axis"), dimension),
                (qn("Domain"), domainHead),
                (qn("Member"), member),
            ],
            conceptRelSets={
                (XbrlConst.dimensionDomain, self.ELR): dimensionDomainRelSet,
                (
                    XbrlConst.domainMember,
                    dimDomainRel.consecutiveLinkrole,
                ): domainMemberRelSet,
            },
            presented={qn("Axis")},
            linkrolesByArcrole={XbrlConst.dimensionDomain: [self.ELR]},
        )
        assert result == frozenset({qn("Domain"), qn("Member")})

    def test_explicit_dimension_domain_not_excluded_when_not_presented(self) -> None:
        dimension = StubConcept(qn("Axis"), isExplicitDimension=True)
        domainHead = StubConcept(qn("Domain"))
        dimDomainRel = conceptRel(domainHead)
        dimensionDomainRelSet = StubDomainMemberRelSet({id(dimension): [dimDomainRel]})
        result = self.excluded(
            items=[(qn("Axis"), dimension), (qn("Domain"), domainHead)],
            conceptRelSets={
                (XbrlConst.dimensionDomain, self.ELR): dimensionDomainRelSet,
            },
            presented=set(),
            linkrolesByArcrole={XbrlConst.dimensionDomain: [self.ELR]},
        )
        assert result == frozenset()


class TestReportIsolatedConcepts:
    ELR = "https://example.com/elr"

    def report(
        self,
        items: list[tuple[QName, Any]],
        baseSets: list[tuple[str, str]],
        conceptRelSets: dict[Any, Any],
        linkrolesByArcrole: dict[str, list[str]] | None = None,
    ) -> list[ArelleDiagnostic]:
        extractor, token = makeExtractor(
            {},
            conceptRelSets,
            linkrolesByArcrole=linkrolesByArcrole,
            baseSets=baseSets,
            items=items,
        )
        extractor.reportIsolatedConcepts()
        return collectedDiagnostics(token)

    def test_fully_isolated_concept_is_reported(self) -> None:
        orphan = StubConcept(qn("Orphan"))
        relSet = StubDomainMemberRelSet({})
        diagnostics = self.report(
            items=[(qn("Orphan"), orphan)],
            baseSets=[(XbrlConst.parentChild, self.ELR)],
            conceptRelSets={(XbrlConst.parentChild, self.ELR): relSet},
        )
        assert len(diagnostics) == 1
        assert "no relationship in any linkbase" in diagnostics[0].text
        assert diagnostics[0].concepts == (qn("Orphan"),)

    def test_documentation_only_concept_is_reported(self) -> None:
        concept = StubConcept(qn("Documented"))
        labelRelSet = StubDomainMemberRelSet(
            {id(concept): [conceptRel(StubConcept(qn("Resource")))]}
        )
        diagnostics = self.report(
            items=[(qn("Documented"), concept)],
            baseSets=[(XbrlConst.conceptLabel, self.ELR)],
            conceptRelSets={(XbrlConst.conceptLabel, self.ELR): labelRelSet},
        )
        assert len(diagnostics) == 1
        assert "only label/reference relationships" in diagnostics[0].text
        assert diagnostics[0].concepts == (qn("Documented"),)

    def test_not_presented_concept_is_reported(self) -> None:
        concept = StubConcept(qn("NotPresented"))
        relSet = StubDomainMemberRelSet(
            {id(concept): [conceptRel(StubConcept(qn("Other")))]}
        )
        diagnostics = self.report(
            items=[(qn("NotPresented"), concept)],
            baseSets=[(XbrlConst.dimensionDefault, self.ELR)],
            conceptRelSets={(XbrlConst.dimensionDefault, self.ELR): relSet},
        )
        assert len(diagnostics) == 1
        assert "absent from the presentation linkbase" in diagnostics[0].text
        assert diagnostics[0].concepts == (qn("NotPresented"),)

    def test_presented_concept_is_silent(self) -> None:
        concept = StubConcept(qn("Presented"))
        relSet = StubDomainMemberRelSet(
            {id(concept): [conceptRel(StubConcept(qn("Child")))]}
        )
        diagnostics = self.report(
            items=[(qn("Presented"), concept)],
            baseSets=[(XbrlConst.parentChild, self.ELR)],
            conceptRelSets={(XbrlConst.parentChild, self.ELR): relSet},
        )
        assert diagnostics == []

    def test_presented_dimensions_domain_members_are_excluded(self) -> None:
        # A presented explicit dimension's domain head and members are not
        # normally presented directly; they must not show up as "not
        # presented" noise.
        dimension = StubConcept(qn("Axis"), isExplicitDimension=True)
        domainHead = StubConcept(qn("Domain"))
        member = StubConcept(qn("Member"))
        presentationRelSet = StubDomainMemberRelSet(
            {id(dimension): [conceptRel(StubConcept(qn("Child")))]}
        )
        dimensionDomainRelSet = StubDomainMemberRelSet(
            {id(dimension): [conceptRel(domainHead)]}, targets={id(domainHead)}
        )
        domainMemberRelSet = StubDomainMemberRelSet(
            {id(domainHead): [conceptRel(member)]}, targets={id(member)}
        )
        diagnostics = self.report(
            items=[
                (qn("Axis"), dimension),
                (qn("Domain"), domainHead),
                (qn("Member"), member),
            ],
            baseSets=[
                (XbrlConst.parentChild, self.ELR),
                (XbrlConst.dimensionDomain, self.ELR),
                (XbrlConst.domainMember, self.ELR),
            ],
            conceptRelSets={
                (XbrlConst.parentChild, self.ELR): presentationRelSet,
                (XbrlConst.dimensionDomain, self.ELR): dimensionDomainRelSet,
                (XbrlConst.domainMember, self.ELR): domainMemberRelSet,
            },
            linkrolesByArcrole={XbrlConst.dimensionDomain: [self.ELR]},
        )
        assert diagnostics == []
