"""Unit tests for TaxonomyChecker, built over hand-written taxonomy JSON via
loadTaxonomyJSON() -- this needs no Arelle DTS at all.

Taxonomy.__init__ mutates the `dimensions` dict it is given (several .pop()
calls), and _TAXONOMIES is a process-lifetime registry that rejects a
duplicate entryPoint, so every test builds its own fresh dicts and uses its
own unique entry point.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest

import mireport.taxonomy as _taxonomy_module
from mireport.taxonomy import Taxonomy, loadTaxonomyJSON
from mireport.taxonomy_checker import TaxonomyChecker

_NS = "https://example.com/vsme"
_STANDARD_LABEL = "http://www.xbrl.org/2003/role/label"


@pytest.fixture(autouse=True)
def _isolated_taxonomy_registry() -> Iterator[None]:
    """loadTaxonomyJSON() registers into the process-lifetime _TAXONOMIES
    registry with no way to unregister; other tests' fixtures (e.g.
    test_taxonomy_resolveConcept.py) assume that registry starts empty (or
    already holds a built-in taxonomy) before loadBuiltInTaxonomyJSON() runs.
    Remove whatever this test added so it leaves no trace for later tests."""
    before = set(_taxonomy_module._TAXONOMIES)
    yield
    for entryPoint in set(_taxonomy_module._TAXONOMIES) - before:
        del _taxonomy_module._TAXONOMIES[entryPoint]


def _concept(
    *,
    labels: dict[str, dict[str, str]] | None = None,
    data_type: str = "xbrli:stringItemType",
    period_type: str = "duration",
    abstract: bool = False,
    hypercube: bool = False,
    dimension: bool = False,
    other: dict[str, Any] | None = None,
) -> dict[str, Any]:
    jconcept: dict[str, Any] = {
        "labels": labels if labels is not None else {"en": {_STANDARD_LABEL: "Label"}},
        "dataType": data_type,
        "baseDataType": data_type,
        "periodType": period_type,
    }
    if abstract:
        jconcept["abstract"] = True
    if hypercube:
        jconcept["hypercube"] = True
    if dimension:
        jconcept["dimension"] = True
    if other:
        jconcept["other"] = other
    return jconcept


def _cube(
    primary_items: list[str],
    explicit_dimensions: dict[str, list[str]] | None = None,
) -> dict[str, Any]:
    return {
        "primaryItems": [[i, q] for i, q in enumerate(primary_items)],
        "xbrldt:contextElement": "scenario",
        "xbrldt:closed": True,
        "explicitDimensions": explicit_dimensions or {},
    }


def _build_taxonomy(
    entry_point: str,
    concepts: dict[str, dict[str, Any]],
    *,
    dimensions: dict[str, Any] | None = None,
    presentation: dict[str, Any] | None = None,
) -> Taxonomy:
    bits = {
        "entryPoint": entry_point,
        "namespaces": {"vsme": _NS},
        "concepts": concepts,
        "presentation": presentation or {},
        "dimensions": dimensions or {},
    }
    return loadTaxonomyJSON(bits)


class TestInconsistentDimensionDomains:
    def test_same_domain_across_roles_is_silent(self) -> None:
        concepts = {
            "vsme:Table": _concept(hypercube=True),
            "vsme:LineItems": _concept(abstract=True),
            "vsme:Axis": _concept(dimension=True),
            "vsme:MemberA": _concept(),
        }
        dimensions = {
            "role-1": {
                "vsme:Table": _cube(["vsme:LineItems"], {"vsme:Axis": ["vsme:MemberA"]})
            },
            "role-2": {
                "vsme:Table": _cube(["vsme:LineItems"], {"vsme:Axis": ["vsme:MemberA"]})
            },
        }
        taxonomy = _build_taxonomy(
            "test://checker/same-domain", concepts, dimensions=dimensions
        )
        assert TaxonomyChecker(taxonomy).reportInconsistentDimensionDomains() == []

    def test_disjoint_domain_across_roles_warns(self) -> None:
        concepts = {
            "vsme:Table": _concept(hypercube=True),
            "vsme:LineItems": _concept(abstract=True),
            "vsme:Axis": _concept(dimension=True),
            "vsme:MemberA": _concept(),
            "vsme:MemberB": _concept(),
        }
        dimensions = {
            "role-1": {
                "vsme:Table": _cube(["vsme:LineItems"], {"vsme:Axis": ["vsme:MemberA"]})
            },
            "role-2": {
                "vsme:Table": _cube(["vsme:LineItems"], {"vsme:Axis": ["vsme:MemberB"]})
            },
        }
        taxonomy = _build_taxonomy(
            "test://checker/disjoint-domain", concepts, dimensions=dimensions
        )
        findings = TaxonomyChecker(taxonomy).reportInconsistentDimensionDomains()
        assert len(findings) == 1
        finding = findings[0]
        assert finding.concepts == (taxonomy.getConcept("vsme:Axis").qname,)
        domainLines = finding.details["domains"]
        assert any("role-1" in line for line in domainLines)
        assert any("role-2" in line for line in domainLines)

    def test_three_roles_one_odd_names_all_three(self) -> None:
        concepts = {
            "vsme:Table": _concept(hypercube=True),
            "vsme:LineItems": _concept(abstract=True),
            "vsme:Axis": _concept(dimension=True),
            "vsme:MemberA": _concept(),
            "vsme:MemberB": _concept(),
        }
        dimensions = {
            "role-1": {
                "vsme:Table": _cube(["vsme:LineItems"], {"vsme:Axis": ["vsme:MemberA"]})
            },
            "role-2": {
                "vsme:Table": _cube(["vsme:LineItems"], {"vsme:Axis": ["vsme:MemberA"]})
            },
            "role-3": {
                "vsme:Table": _cube(["vsme:LineItems"], {"vsme:Axis": ["vsme:MemberB"]})
            },
        }
        taxonomy = _build_taxonomy(
            "test://checker/three-roles", concepts, dimensions=dimensions
        )
        findings = TaxonomyChecker(taxonomy).reportInconsistentDimensionDomains()
        assert len(findings) == 1
        assert len(findings[0].details["domains"]) == 3


class TestUnresolvedEnumerationDomains:
    def test_empty_domain_warns(self) -> None:
        concepts = {
            "vsme:Choice": _concept(
                data_type="enum2:enumerationItemType",
                other={"ee20DomainMembers": []},
            ),
        }
        taxonomy = _build_taxonomy("test://checker/empty-enum-domain", concepts)
        findings = TaxonomyChecker(taxonomy).reportUnresolvedEnumerationDomains()
        assert len(findings) == 1
        assert findings[0].concepts == (taxonomy.getConcept("vsme:Choice").qname,)

    def test_non_empty_domain_is_silent(self) -> None:
        concepts = {
            "vsme:Choice": _concept(
                data_type="enum2:enumerationItemType",
                other={"ee20DomainMembers": ["vsme:MemberA"]},
            ),
            "vsme:MemberA": _concept(),
        }
        taxonomy = _build_taxonomy("test://checker/nonempty-enum-domain", concepts)
        assert TaxonomyChecker(taxonomy).reportUnresolvedEnumerationDomains() == []

    def test_non_enumeration_concept_is_ignored(self) -> None:
        concepts = {"vsme:Plain": _concept()}
        taxonomy = _build_taxonomy("test://checker/plain-concept", concepts)
        assert TaxonomyChecker(taxonomy).reportUnresolvedEnumerationDomains() == []


class TestLabelCollisions:
    def test_shared_standard_label_warns(self) -> None:
        concepts = {
            "vsme:First": _concept(labels={"en": {_STANDARD_LABEL: "Shared Label"}}),
            "vsme:Second": _concept(labels={"en": {_STANDARD_LABEL: "Shared Label"}}),
        }
        taxonomy = _build_taxonomy("test://checker/label-collision", concepts)
        findings = TaxonomyChecker(taxonomy).reportLabelCollisions()
        expected = {
            taxonomy.getConcept("vsme:First").qname,
            taxonomy.getConcept("vsme:Second").qname,
        }
        assert findings
        assert any(set(f.concepts) == expected for f in findings)

    def test_unique_labels_are_silent(self) -> None:
        concepts = {
            "vsme:First": _concept(labels={"en": {_STANDARD_LABEL: "Alpha"}}),
            "vsme:Second": _concept(labels={"en": {_STANDARD_LABEL: "Beta"}}),
        }
        taxonomy = _build_taxonomy("test://checker/label-unique", concepts)
        assert TaxonomyChecker(taxonomy).reportLabelCollisions() == []


class TestReportIssues:
    def test_aggregates_all_checks(self) -> None:
        concepts = {
            "vsme:Table": _concept(hypercube=True),
            "vsme:LineItems": _concept(abstract=True),
            "vsme:Axis": _concept(dimension=True),
            "vsme:MemberA": _concept(),
            "vsme:MemberB": _concept(),
            "vsme:Choice": _concept(
                data_type="enum2:enumerationItemType",
                other={"ee20DomainMembers": []},
            ),
        }
        dimensions = {
            "role-1": {
                "vsme:Table": _cube(["vsme:LineItems"], {"vsme:Axis": ["vsme:MemberA"]})
            },
            "role-2": {
                "vsme:Table": _cube(["vsme:LineItems"], {"vsme:Axis": ["vsme:MemberB"]})
            },
        }
        taxonomy = _build_taxonomy(
            "test://checker/aggregate", concepts, dimensions=dimensions
        )
        texts = {f.text for f in TaxonomyChecker(taxonomy).reportIssues()}
        assert (
            "Explicit dimension is given different domains in different hypercubes"
            in texts
        )
        assert "Extensible enumeration concept has no resolved domain members" in texts
