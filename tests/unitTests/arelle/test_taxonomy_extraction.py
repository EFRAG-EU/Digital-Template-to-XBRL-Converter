"""Unit tests for the taxonomy extraction logic.

Success paths that require genuine Arelle lxml-backed objects are covered
end-to-end by tests/integrationTests/test_taxonomy_info_regeneration.py;
these tests cover extraction logic using lightweight stubs (see
test_model_access.py for the same approach).
"""

from __future__ import annotations

import json
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

from mireport.arelle.diagnostics import Diagnostic, DiagnosticCollector
from mireport.arelle.model_access import (
    ConceptRelationship,
    ConceptRelationshipSet,
    ResourceRelationship,
    ValidatedModel,
)
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
    def __init__(self, qname: QName) -> None:
        self.qname = qname


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

    def __init__(self, relsByArcrole: dict[str, list[ResourceRelationship]]) -> None:
        self._relsByArcrole = relsByArcrole

    def resourceRelationshipsFrom(
        self, source: Any, arcrole: str
    ) -> list[ResourceRelationship]:
        return self._relsByArcrole[arcrole]


def labelRel(resource: StubLabelResource) -> ResourceRelationship:
    return ResourceRelationship(
        resource=cast(Any, resource), role=resource.role, order=1.0
    )


def makeExtractor(
    relsByArcrole: dict[str, list[ResourceRelationship]],
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
    extractor.model = cast(ValidatedModel, StubValidatedModel(relsByArcrole))
    return extractor, token


def collectedDiagnostics(token: str) -> list[Diagnostic]:
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
    ) -> tuple[dict, list[Diagnostic]]:
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


class TestGetLabelsForRoleType:
    def getLabels(
        self, labelRels: list[ResourceRelationship]
    ) -> tuple[dict[str, str], list[Diagnostic]]:
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
