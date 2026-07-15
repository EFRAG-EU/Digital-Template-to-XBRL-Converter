"""Unit tests for the taxonomy-info extraction plugin.

Success paths that require genuine Arelle lxml-backed objects are covered
end-to-end by tests/integrationTests/test_taxonomy_info_regeneration.py;
these tests cover extraction logic using lightweight stubs (see
test_model_access.py for the same approach).
"""

from types import SimpleNamespace
from typing import Any, cast

from arelle import XbrlConst
from arelle.Cntlr import Cntlr
from arelle.ModelDtsObject import ModelConcept
from arelle.ModelValue import QName
from arelle.ModelXbrl import ModelXbrl
from arelle.RuntimeOptions import RuntimeOptions

from mireport.arelle.diagnostics import Diagnostic, DiagnosticCollector
from mireport.arelle.model_access import ResourceRelationship, ValidatedModel
from mireport.arelle.taxonomy_info import (
    TaxonomyInfoExtractor,
    TaxonomyInfoPluginData,
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


class StubValidatedModel:
    """Stands in for ValidatedModel; serves canned ResourceRelationships."""

    def __init__(self, labelRels: list[ResourceRelationship]) -> None:
        self._labelRels = labelRels

    def resourceRelationshipsFrom(
        self, source: Any, arcrole: str
    ) -> list[ResourceRelationship]:
        assert arcrole == XbrlConst.conceptLabel
        return self._labelRels


def labelRel(resource: StubLabelResource) -> ResourceRelationship:
    return ResourceRelationship(
        resource=cast(Any, resource), role=resource.role, order=1.0
    )


def makeExtractor(
    labelRels: list[ResourceRelationship],
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
    extractor.model = cast(ValidatedModel, StubValidatedModel(labelRels))
    return extractor, token


def collectedDiagnostics(token: str) -> list[Diagnostic]:
    return DiagnosticCollector.close(token)


class TestAddLabels:
    def addLabels(
        self, labelRels: list[ResourceRelationship]
    ) -> tuple[dict, list[Diagnostic]]:
        extractor, token = makeExtractor(labelRels)
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


class TestTaxonomyInfoPluginData:
    def test_instances_have_independent_taxonomy_dicts(self) -> None:
        first = TaxonomyInfoPluginData("first")
        second = TaxonomyInfoPluginData("second")
        first.Taxonomy["concepts"] = {"vsme:Thing": {}}
        assert second.Taxonomy == {}
        assert first.Taxonomy is not second.Taxonomy

    def test_instances_have_independent_utr_dicts(self) -> None:
        first = TaxonomyInfoPluginData("first")
        second = TaxonomyInfoPluginData("second")
        first.UTR["utr"] = [{"unitId": "kg"}]
        assert second.UTR == {}
        assert first.UTR is not second.UTR
