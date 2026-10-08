"""Unit tests for TaxonomyChecker, built over hand-written taxonomy JSON via
loadTaxonomyJSON() -- this needs no Arelle DTS at all.

Taxonomy.__init__ mutates the `dimensions` dict it is given (several .pop()
calls), and _TAXONOMIES is a process-lifetime registry that rejects a
duplicate entryPoint, so every test builds its own fresh dicts and uses its
own unique entry point.
"""

from __future__ import annotations

import warnings
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
    if abstract or hypercube or dimension:
        # XDT requires hypercube and dimension items to be declared abstract.
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
    *,
    closed: bool = True,
    negative: bool = False,
) -> dict[str, Any]:
    cube: dict[str, Any] = {
        "primaryItems": [[i, q] for i, q in enumerate(primary_items)],
        "xbrldt:contextElement": "scenario",
        "xbrldt:closed": closed,
        "explicitDimensions": explicit_dimensions or {},
    }
    if negative:
        cube["type"] = "negative"
    return cube


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


def _build_taxonomy_quietly(
    entry_point: str,
    concepts: dict[str, dict[str, Any]],
    *,
    dimensions: dict[str, Any] | None = None,
    presentation: dict[str, Any] | None = None,
) -> Taxonomy:
    """Like _build_taxonomy(), but swallows the UserWarning Taxonomy.__init__
    emits for an open or negative cube -- these tests are about the checker's
    findings, not about that load-time warning."""
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        return _build_taxonomy(
            entry_point, concepts, dimensions=dimensions, presentation=presentation
        )


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


_HYPERCUBE_CONCEPTS = {
    "vsme:Table": _concept(hypercube=True),
    "vsme:LineItems": _concept(abstract=True),
    "vsme:Axis": _concept(dimension=True),
    # Domain members are normally abstract under XDT; kept abstract here so
    # this shared fixture doesn't itself violate FDV's rec 3 by accident --
    # the non-abstract case has its own dedicated concepts below.
    "vsme:MemberA": _concept(abstract=True),
    "vsme:Item": _concept(),
    "vsme:OtherItem": _concept(),
}


class TestOpenPositiveHypercubes:
    def test_closed_hypercube_is_silent(self) -> None:
        dimensions = {"role-1": {"vsme:Table": _cube(["vsme:Item"])}}
        taxonomy = _build_taxonomy(
            "test://checker/closed-positive-silent",
            _HYPERCUBE_CONCEPTS,
            dimensions=dimensions,
        )
        assert TaxonomyChecker(taxonomy).reportOpenPositiveHypercubes() == []

    def test_open_hypercube_warns(self) -> None:
        dimensions = {"role-1": {"vsme:Table": _cube(["vsme:Item"], closed=False)}}
        taxonomy = _build_taxonomy_quietly(
            "test://checker/open-positive-warns",
            _HYPERCUBE_CONCEPTS,
            dimensions=dimensions,
        )
        [finding] = TaxonomyChecker(taxonomy).reportOpenPositiveHypercubes()
        assert finding.concepts == (taxonomy.getConcept("vsme:Table").qname,)
        assert "open" in finding.text

    def test_two_open_hypercubes_are_one_finding(self) -> None:
        concepts = {**_HYPERCUBE_CONCEPTS, "vsme:TableB": _concept(hypercube=True)}
        dimensions = {
            "role-1": {"vsme:Table": _cube(["vsme:Item"], closed=False)},
            "role-2": {"vsme:TableB": _cube(["vsme:OtherItem"], closed=False)},
        }
        taxonomy = _build_taxonomy_quietly(
            "test://checker/two-open-positive", concepts, dimensions=dimensions
        )
        findings = TaxonomyChecker(taxonomy).reportOpenPositiveHypercubes()
        assert len(findings) == 1
        finding = findings[0]
        assert "2" in finding.text
        locations = finding.details["locations"]
        assert any("role-1" in loc and "Table" in loc for loc in locations)
        assert any("role-2" in loc and "TableB" in loc for loc in locations)

    def test_open_negative_hypercube_is_not_reported_here(self) -> None:
        # This check is about positive cubes only; an open negative cube is
        # exactly the WGN-conformant shape (see TestClosedNegativeHypercubes).
        dimensions = {
            "role-1": {"vsme:Table": _cube(["vsme:Item"], closed=False, negative=True)}
        }
        taxonomy = _build_taxonomy_quietly(
            "test://checker/open-negative-not-rec1",
            _HYPERCUBE_CONCEPTS,
            dimensions=dimensions,
        )
        assert TaxonomyChecker(taxonomy).reportOpenPositiveHypercubes() == []

    def test_hint_cites_the_guidance(self) -> None:
        dimensions = {"role-1": {"vsme:Table": _cube(["vsme:Item"], closed=False)}}
        taxonomy = _build_taxonomy_quietly(
            "test://checker/open-positive-hint",
            _HYPERCUBE_CONCEPTS,
            dimensions=dimensions,
        )
        [finding] = TaxonomyChecker(taxonomy).reportOpenPositiveHypercubes()
        assert finding.hint is not None
        assert "3.5" in finding.hint

    def test_taxonomy_without_dimensions_is_silent(self) -> None:
        taxonomy = _build_taxonomy(
            "test://checker/no-dimensions-rec1", {"vsme:Plain": _concept()}
        )
        assert TaxonomyChecker(taxonomy).reportOpenPositiveHypercubes() == []


class TestClosedNegativeHypercubes:
    def test_open_negative_is_silent(self) -> None:
        dimensions = {
            "role-1": {"vsme:Table": _cube(["vsme:Item"], closed=False, negative=True)}
        }
        taxonomy = _build_taxonomy_quietly(
            "test://checker/open-negative-silent",
            _HYPERCUBE_CONCEPTS,
            dimensions=dimensions,
        )
        assert TaxonomyChecker(taxonomy).reportClosedNegativeHypercubes() == []

    def test_closed_negative_warns(self) -> None:
        dimensions = {"role-1": {"vsme:Table": _cube(["vsme:Item"], negative=True)}}
        taxonomy = _build_taxonomy_quietly(
            "test://checker/closed-negative-warns",
            _HYPERCUBE_CONCEPTS,
            dimensions=dimensions,
        )
        [finding] = TaxonomyChecker(taxonomy).reportClosedNegativeHypercubes()
        assert finding.concepts == (taxonomy.getConcept("vsme:Table").qname,)
        assert "closed" in finding.text

    def test_closed_positive_is_silent(self) -> None:
        dimensions = {"role-1": {"vsme:Table": _cube(["vsme:Item"])}}
        taxonomy = _build_taxonomy(
            "test://checker/closed-positive-rec4-silent",
            _HYPERCUBE_CONCEPTS,
            dimensions=dimensions,
        )
        assert TaxonomyChecker(taxonomy).reportClosedNegativeHypercubes() == []

    def test_legacy_json_without_type_key_is_silent(self) -> None:
        cube = _cube(["vsme:Item"])
        assert "type" not in cube
        dimensions = {"role-1": {"vsme:Table": cube}}
        taxonomy = _build_taxonomy(
            "test://checker/legacy-json-rec4-silent",
            _HYPERCUBE_CONCEPTS,
            dimensions=dimensions,
        )
        assert TaxonomyChecker(taxonomy).reportClosedNegativeHypercubes() == []


class TestNegativeHypercubesWithoutPositive:
    def test_negative_alone_in_base_set_warns(self) -> None:
        dimensions = {
            "role-1": {"vsme:Table": _cube(["vsme:Item"], closed=False, negative=True)}
        }
        taxonomy = _build_taxonomy_quietly(
            "test://checker/negative-alone-warns",
            _HYPERCUBE_CONCEPTS,
            dimensions=dimensions,
        )
        [finding] = TaxonomyChecker(taxonomy).reportNegativeHypercubesWithoutPositive()
        assert finding.concepts == (taxonomy.getConcept("vsme:Table").qname,)

    def test_negative_with_positive_in_same_base_set_is_silent(self) -> None:
        concepts = {**_HYPERCUBE_CONCEPTS, "vsme:TableB": _concept(hypercube=True)}
        dimensions = {
            "role-1": {
                "vsme:Table": _cube(["vsme:Item"]),
                "vsme:TableB": _cube(["vsme:OtherItem"], closed=False, negative=True),
            }
        }
        taxonomy = _build_taxonomy_quietly(
            "test://checker/negative-with-positive-same-role",
            concepts,
            dimensions=dimensions,
        )
        assert TaxonomyChecker(taxonomy).reportNegativeHypercubesWithoutPositive() == []

    def test_negative_with_positive_in_a_different_base_set_warns(self) -> None:
        concepts = {**_HYPERCUBE_CONCEPTS, "vsme:TableB": _concept(hypercube=True)}
        dimensions = {
            "role-1": {"vsme:Table": _cube(["vsme:Item"], closed=False, negative=True)},
            "role-2": {"vsme:TableB": _cube(["vsme:OtherItem"])},
        }
        taxonomy = _build_taxonomy_quietly(
            "test://checker/negative-positive-different-roles",
            concepts,
            dimensions=dimensions,
        )
        findings = TaxonomyChecker(taxonomy).reportNegativeHypercubesWithoutPositive()
        assert len(findings) == 1
        assert findings[0].concepts == (taxonomy.getConcept("vsme:Table").qname,)

    def test_two_negatives_alone_are_one_finding(self) -> None:
        concepts = {**_HYPERCUBE_CONCEPTS, "vsme:TableB": _concept(hypercube=True)}
        dimensions = {
            "role-1": {"vsme:Table": _cube(["vsme:Item"], closed=False, negative=True)},
            "role-2": {
                "vsme:TableB": _cube(["vsme:OtherItem"], closed=False, negative=True)
            },
        }
        taxonomy = _build_taxonomy_quietly(
            "test://checker/two-negatives-alone", concepts, dimensions=dimensions
        )
        findings = TaxonomyChecker(taxonomy).reportNegativeHypercubesWithoutPositive()
        assert len(findings) == 1
        assert "2" in findings[0].text

    def test_legacy_json_without_type_key_is_silent(self) -> None:
        cube = _cube(["vsme:Item"])
        assert "type" not in cube
        dimensions = {"role-1": {"vsme:Table": cube}}
        taxonomy = _build_taxonomy(
            "test://checker/legacy-json-rec2-silent",
            _HYPERCUBE_CONCEPTS,
            dimensions=dimensions,
        )
        assert TaxonomyChecker(taxonomy).reportNegativeHypercubesWithoutPositive() == []


class TestConceptsWithoutHypercube:
    def test_taxonomy_with_no_hypercubes_is_silent(self) -> None:
        taxonomy = _build_taxonomy(
            "test://checker/no-hypercubes-rec3", {"vsme:Plain": _concept()}
        )
        assert TaxonomyChecker(taxonomy).reportConceptsWithoutHypercube() == []

    def test_concept_in_a_closed_hypercube_is_silent(self) -> None:
        dimensions = {"role-1": {"vsme:Table": _cube(["vsme:Item"])}}
        concepts = {**_HYPERCUBE_CONCEPTS}
        taxonomy = _build_taxonomy(
            "test://checker/covered-concept-silent", concepts, dimensions=dimensions
        )
        findings = TaxonomyChecker(taxonomy).reportConceptsWithoutHypercube()
        orphaned = {q for f in findings for q in f.concepts}
        assert taxonomy.getConcept("vsme:Item").qname not in orphaned

    def test_concept_outside_every_hypercube_warns(self) -> None:
        # vsme:OtherItem is reportable but never a primary item anywhere.
        dimensions = {"role-1": {"vsme:Table": _cube(["vsme:Item"])}}
        taxonomy = _build_taxonomy(
            "test://checker/orphan-concept-warns",
            _HYPERCUBE_CONCEPTS,
            dimensions=dimensions,
        )
        [finding] = TaxonomyChecker(taxonomy).reportConceptsWithoutHypercube()
        assert finding.concepts == (taxonomy.getConcept("vsme:OtherItem").qname,)
        assert "1" in finding.text

    def test_concept_in_a_dimensionless_hypercube_is_silent(self) -> None:
        # The WGN section 3.4 remedy -- a closed hypercube with no
        # dimensions -- must satisfy this check.
        dimensions = {"role-1": {"vsme:Table": _cube(["vsme:Item", "vsme:OtherItem"])}}
        taxonomy = _build_taxonomy(
            "test://checker/dimensionless-remedy-silent",
            _HYPERCUBE_CONCEPTS,
            dimensions=dimensions,
        )
        assert TaxonomyChecker(taxonomy).reportConceptsWithoutHypercube() == []

    def test_concept_in_an_open_hypercube_is_silent(self) -> None:
        # The GRI case: an open cube builds no DimensionSignature, but its
        # primary items are still associated via hypercubeDeclarations.
        dimensions = {
            "role-1": {
                "vsme:Table": _cube(["vsme:Item", "vsme:OtherItem"], closed=False)
            }
        }
        taxonomy = _build_taxonomy_quietly(
            "test://checker/open-cube-rec3-silent",
            _HYPERCUBE_CONCEPTS,
            dimensions=dimensions,
        )
        assert TaxonomyChecker(taxonomy).reportConceptsWithoutHypercube() == []

    def test_concept_in_a_negative_hypercube_is_silent(self) -> None:
        dimensions = {
            "role-1": {
                "vsme:Table": _cube(
                    ["vsme:Item", "vsme:OtherItem"], closed=False, negative=True
                )
            }
        }
        taxonomy = _build_taxonomy_quietly(
            "test://checker/negative-cube-rec3-silent",
            _HYPERCUBE_CONCEPTS,
            dimensions=dimensions,
        )
        assert TaxonomyChecker(taxonomy).reportConceptsWithoutHypercube() == []

    def test_abstract_concept_is_excluded(self) -> None:
        dimensions = {"role-1": {"vsme:Table": _cube(["vsme:Item"])}}
        taxonomy = _build_taxonomy(
            "test://checker/abstract-excluded",
            _HYPERCUBE_CONCEPTS,
            dimensions=dimensions,
        )
        orphaned = {
            q
            for f in TaxonomyChecker(taxonomy).reportConceptsWithoutHypercube()
            for q in f.concepts
        }
        assert taxonomy.getConcept("vsme:LineItems").qname not in orphaned

    def test_hypercube_concept_is_excluded(self) -> None:
        dimensions = {"role-1": {"vsme:Table": _cube(["vsme:Item"])}}
        taxonomy = _build_taxonomy(
            "test://checker/hypercube-excluded",
            _HYPERCUBE_CONCEPTS,
            dimensions=dimensions,
        )
        orphaned = {
            q
            for f in TaxonomyChecker(taxonomy).reportConceptsWithoutHypercube()
            for q in f.concepts
        }
        assert taxonomy.getConcept("vsme:Table").qname not in orphaned

    def test_non_abstract_domain_member_that_is_a_primary_item_is_silent(
        self,
    ) -> None:
        # A non-abstract explicit-dimension domain member is unusual but
        # allowed under XDT. Here it also happens to be a primary item of
        # Table, so it is not an orphan.
        concepts = {**_HYPERCUBE_CONCEPTS, "vsme:MemberA": _concept()}
        dimensions = {
            "role-1": {
                "vsme:Table": _cube(
                    ["vsme:Item", "vsme:MemberA"], {"vsme:Axis": ["vsme:MemberA"]}
                )
            }
        }
        taxonomy = _build_taxonomy(
            "test://checker/domain-member-primary-item-silent",
            concepts,
            dimensions=dimensions,
        )
        orphaned = {
            q
            for f in TaxonomyChecker(taxonomy).reportConceptsWithoutHypercube()
            for q in f.concepts
        }
        assert taxonomy.getConcept("vsme:MemberA").qname not in orphaned

    def test_non_abstract_domain_member_that_is_no_primary_item_warns(self) -> None:
        # The correction this design turns on: a reportable domain member
        # gets no free pass for being a domain member elsewhere -- it is
        # judged solely by whether it is a primary item somewhere.
        concepts = {**_HYPERCUBE_CONCEPTS, "vsme:MemberA": _concept()}
        dimensions = {
            "role-1": {
                "vsme:Table": _cube(["vsme:Item"], {"vsme:Axis": ["vsme:MemberA"]})
            }
        }
        taxonomy = _build_taxonomy(
            "test://checker/domain-member-no-primary-item-warns",
            concepts,
            dimensions=dimensions,
        )
        orphaned = {
            q
            for f in TaxonomyChecker(taxonomy).reportConceptsWithoutHypercube()
            for q in f.concepts
        }
        assert taxonomy.getConcept("vsme:MemberA").qname in orphaned

    def test_orphans_are_reported_in_qname_order(self) -> None:
        concepts = {**_HYPERCUBE_CONCEPTS, "vsme:AaaItem": _concept()}
        dimensions = {"role-1": {"vsme:Table": _cube(["vsme:Item"])}}
        taxonomy = _build_taxonomy(
            "test://checker/orphans-ordered", concepts, dimensions=dimensions
        )
        [finding] = TaxonomyChecker(taxonomy).reportConceptsWithoutHypercube()
        assert list(finding.concepts) == sorted(finding.concepts)

    def test_one_finding_lists_every_orphan(self) -> None:
        concepts = {**_HYPERCUBE_CONCEPTS, "vsme:ThirdItem": _concept()}
        dimensions = {"role-1": {"vsme:Table": _cube(["vsme:Item"])}}
        taxonomy = _build_taxonomy(
            "test://checker/orphans-all-in-one", concepts, dimensions=dimensions
        )
        findings = TaxonomyChecker(taxonomy).reportConceptsWithoutHypercube()
        assert len(findings) == 1
        assert len(findings[0].concepts) == 2


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

    def test_aggregates_full_dimensional_validity_checks(self) -> None:
        concepts = {
            "vsme:Table": _concept(hypercube=True),
            "vsme:Item": _concept(),
            "vsme:OtherItem": _concept(),
        }
        dimensions = {"role-1": {"vsme:Table": _cube(["vsme:Item"], closed=False)}}
        taxonomy = _build_taxonomy_quietly(
            "test://checker/aggregate-fdv", concepts, dimensions=dimensions
        )
        texts = {f.text for f in TaxonomyChecker(taxonomy).reportIssues()}
        assert any("open" in text for text in texts)
        assert any("not a primary item of any hypercube" in text for text in texts)
