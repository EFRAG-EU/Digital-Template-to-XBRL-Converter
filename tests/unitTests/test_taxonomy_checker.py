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
from typing import Any, ClassVar

import pytest

import mireport.taxonomy as _taxonomy_module
from mireport.taxonomy import Taxonomy, loadTaxonomyJSON
from mireport.taxonomy_checker import TaxonomyChecker

_NS = "https://example.com/vsme"
_OTHER_NS = "https://example.com/other"
_REF_NS = "http://www.xbrl.org/2006/ref"
_STANDARD_LABEL = "http://www.xbrl.org/2003/role/label"
_TERSE_LABEL = "http://www.xbrl.org/2003/role/terseLabel"
_MEASUREMENT_GUIDANCE_LABEL = "http://www.xbrl.org/2003/role/measurementGuidance"


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
    base_data_type: str | None = None,
    period_type: str = "duration",
    abstract: bool = False,
    hypercube: bool = False,
    dimension: bool = False,
    numeric: bool = False,
    references: list[dict[str, Any]] | None = None,
    other: dict[str, Any] | None = None,
) -> dict[str, Any]:
    jconcept: dict[str, Any] = {
        "labels": labels if labels is not None else {"en": {_STANDARD_LABEL: "Label"}},
        "dataType": data_type,
        "baseDataType": base_data_type if base_data_type is not None else data_type,
        "periodType": period_type,
    }
    if abstract or hypercube or dimension:
        # XDT requires hypercube and dimension items to be declared abstract.
        jconcept["abstract"] = True
    if hypercube:
        jconcept["hypercube"] = True
    if dimension:
        jconcept["dimension"] = True
    if numeric:
        jconcept["numeric"] = True
    if references is not None:
        # Legacy, pre-top-level-references shape: _createTaxonomyFromJSON()
        # folds this into the native shape when "references" is absent from
        # the top-level bits -- see test_taxonomy_references.py.
        jconcept["references"] = references
    if other:
        jconcept["other"] = other
    return jconcept


def _reference(*, parts: list[list[str]] | None = None) -> dict[str, Any]:
    return {
        "role": "http://www.xbrl.org/2003/role/reference",
        "parts": parts if parts is not None else [["ref:Name", "Some Standard"]],
    }


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
    extra_namespaces: dict[str, str] | None = None,
) -> Taxonomy:
    bits = {
        "entryPoint": entry_point,
        "namespaces": {"vsme": _NS, "ref": _REF_NS, **(extra_namespaces or {})},
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


class TestConceptsWithoutStandardLabel:
    def test_terse_label_only_is_flagged(self) -> None:
        concepts = {
            "vsme:NoStandard": _concept(labels={"en": {_TERSE_LABEL: "Terse"}}),
        }
        taxonomy = _build_taxonomy("test://checker/no-standard-label", concepts)
        [finding] = TaxonomyChecker(taxonomy).reportConceptsWithoutStandardLabel()
        assert finding.concepts == (taxonomy.getConcept("vsme:NoStandard").qname,)

    def test_label_in_other_language_only_is_flagged(self) -> None:
        # An "any language" standard label is not enough -- English is
        # required specifically, regardless of what else is present.
        concepts = {
            "vsme:French": _concept(labels={"fr": {_STANDARD_LABEL: "Libellé"}}),
        }
        taxonomy = _build_taxonomy("test://checker/standard-label-fr", concepts)
        [finding] = TaxonomyChecker(taxonomy).reportConceptsWithoutStandardLabel()
        assert finding.concepts == (taxonomy.getConcept("vsme:French").qname,)

    def test_regional_english_variant_counts_as_english(self) -> None:
        # Concept.getStandardLabel() does BCP-47 base-language matching, so
        # "en-GB" satisfies the English requirement.
        concepts = {
            "vsme:BritishOnly": _concept(labels={"en-GB": {_STANDARD_LABEL: "Colour"}}),
        }
        taxonomy = _build_taxonomy("test://checker/standard-label-en-gb", concepts)
        assert TaxonomyChecker(taxonomy).reportConceptsWithoutStandardLabel() == []

    def test_abstract_concept_is_still_checked(self) -> None:
        concepts = {
            "vsme:Heading": _concept(abstract=True, labels={"en": {_TERSE_LABEL: "x"}}),
        }
        taxonomy = _build_taxonomy(
            "test://checker/no-standard-label-abstract", concepts
        )
        [finding] = TaxonomyChecker(taxonomy).reportConceptsWithoutStandardLabel()
        assert finding.concepts == (taxonomy.getConcept("vsme:Heading").qname,)


class TestConceptsMissingExpectedLanguageLabels:
    _EN_FR: ClassVar = {
        "en": {_STANDARD_LABEL: "Label"},
        "fr": {_STANDARD_LABEL: "Étiquette"},
    }
    _EN_ONLY: ClassVar = {"en": {_STANDARD_LABEL: "Label"}}

    def test_majority_language_gap_is_flagged(self) -> None:
        concepts = {
            "vsme:HasFrenchA": _concept(labels=self._EN_FR),
            "vsme:HasFrenchB": _concept(labels=self._EN_FR),
            "vsme:MissingFrench": _concept(labels=self._EN_ONLY),
        }
        taxonomy = _build_taxonomy("test://checker/lang-majority-gap", concepts)
        [finding] = TaxonomyChecker(
            taxonomy
        ).reportConceptsMissingExpectedLanguageLabels()
        assert finding.concepts == (taxonomy.getConcept("vsme:MissingFrench").qname,)
        assert "'fr'" in finding.text

    def test_minority_language_is_not_flagged(self) -> None:
        concepts = {
            "vsme:HasFrench": _concept(labels=self._EN_FR),
            "vsme:NoFrenchA": _concept(labels=self._EN_ONLY),
            "vsme:NoFrenchB": _concept(labels=self._EN_ONLY),
        }
        taxonomy = _build_taxonomy("test://checker/lang-minority", concepts)
        assert (
            TaxonomyChecker(taxonomy).reportConceptsMissingExpectedLanguageLabels()
            == []
        )

    def test_exactly_half_coverage_is_not_a_majority(self) -> None:
        concepts = {
            "vsme:HasFrench": _concept(labels=self._EN_FR),
            "vsme:NoFrench": _concept(labels=self._EN_ONLY),
        }
        taxonomy = _build_taxonomy("test://checker/lang-exactly-half", concepts)
        assert (
            TaxonomyChecker(taxonomy).reportConceptsMissingExpectedLanguageLabels()
            == []
        )

    def test_full_coverage_is_silent(self) -> None:
        concepts = {
            "vsme:HasFrenchA": _concept(labels=self._EN_FR),
            "vsme:HasFrenchB": _concept(labels=self._EN_FR),
        }
        taxonomy = _build_taxonomy("test://checker/lang-full-coverage", concepts)
        assert (
            TaxonomyChecker(taxonomy).reportConceptsMissingExpectedLanguageLabels()
            == []
        )

    def test_prefixes_are_scoped_independently(self) -> None:
        # "vsme:" has a real fr-majority gap; "other:" has no fr coverage at
        # all (0%, not a majority there), so its untranslated concept must
        # not be treated as a gap.
        concepts = {
            "vsme:HasFrenchA": _concept(labels=self._EN_FR),
            "vsme:HasFrenchB": _concept(labels=self._EN_FR),
            "vsme:MissingFrench": _concept(labels=self._EN_ONLY),
            "other:NoFrenchAnywhere": _concept(labels=self._EN_ONLY),
        }
        taxonomy = _build_taxonomy(
            "test://checker/lang-scoped-prefixes",
            concepts,
            extra_namespaces={"other": _OTHER_NS},
        )
        [finding] = TaxonomyChecker(
            taxonomy
        ).reportConceptsMissingExpectedLanguageLabels()
        assert finding.concepts == (taxonomy.getConcept("vsme:MissingFrench").qname,)

    def test_english_is_never_a_candidate_language(self) -> None:
        # vsme:NoEnglish has no English label at all -- that's
        # reportConceptsWithoutStandardLabel's job. It does have French, so
        # it counts towards (not against) the 'fr' majority here; this check
        # must still never itself produce an 'en' finding for anyone.
        concepts = {
            "vsme:HasBothA": _concept(labels=self._EN_FR),
            "vsme:HasBothB": _concept(labels=self._EN_FR),
            "vsme:MissingFrench": _concept(labels=self._EN_ONLY),
            "vsme:NoEnglish": _concept(labels={"fr": {_STANDARD_LABEL: "Étiquette"}}),
        }
        taxonomy = _build_taxonomy("test://checker/lang-english-excluded", concepts)
        findings = TaxonomyChecker(
            taxonomy
        ).reportConceptsMissingExpectedLanguageLabels()
        [finding] = findings
        assert finding.concepts == (taxonomy.getConcept("vsme:MissingFrench").qname,)
        assert all("'en'" not in f.text for f in findings)


class TestConceptsWithoutReferences:
    def test_reportable_concept_without_references_is_flagged(self) -> None:
        concepts = {"vsme:Item": _concept()}
        taxonomy = _build_taxonomy("test://checker/no-references", concepts)
        [finding] = TaxonomyChecker(taxonomy).reportConceptsWithoutReferences()
        assert finding.concepts == (taxonomy.getConcept("vsme:Item").qname,)

    def test_concept_with_a_reference_is_silent(self) -> None:
        concepts = {"vsme:Item": _concept(references=[_reference()])}
        taxonomy = _build_taxonomy("test://checker/has-reference", concepts)
        assert TaxonomyChecker(taxonomy).reportConceptsWithoutReferences() == []

    def test_abstract_concept_without_references_is_silent(self) -> None:
        concepts = {"vsme:Heading": _concept(abstract=True)}
        taxonomy = _build_taxonomy(
            "test://checker/no-references-abstract-silent", concepts
        )
        assert TaxonomyChecker(taxonomy).reportConceptsWithoutReferences() == []

    def test_several_offenders_make_one_finding(self) -> None:
        concepts = {"vsme:First": _concept(), "vsme:Second": _concept()}
        taxonomy = _build_taxonomy("test://checker/no-references-multi", concepts)
        findings = TaxonomyChecker(taxonomy).reportConceptsWithoutReferences()
        assert len(findings) == 1
        assert list(findings[0].concepts) == sorted(findings[0].concepts)
        assert len(findings[0].concepts) == 2


class TestNumericConceptsWithoutMeasurementGuidance:
    # xbrli:pureItemType has three UTR candidate units (pure, Rate,
    # Monetary_per_Monetary) -- a genuinely ambiguous data type. Real UTR
    # data is used here (loadTaxonomyJSON() always loads the bundled UTR),
    # so these data types are picked for their known candidate counts.
    def test_ambiguous_data_type_without_guidance_is_flagged(self) -> None:
        concepts = {
            "vsme:RatioNoGuidance": _concept(
                data_type="xbrli:pureItemType", numeric=True
            )
        }
        taxonomy = _build_taxonomy("test://checker/numeric-no-mg", concepts)
        findings = TaxonomyChecker(
            taxonomy
        ).reportNumericConceptsWithoutMeasurementGuidance()
        [finding] = findings
        assert finding.concepts == (taxonomy.getConcept("vsme:RatioNoGuidance").qname,)

    def test_ambiguous_data_type_with_guidance_is_silent(self) -> None:
        concepts = {
            "vsme:RatioWithGuidance": _concept(
                data_type="xbrli:pureItemType",
                numeric=True,
                labels={
                    "en": {
                        _STANDARD_LABEL: "Ratio",
                        _MEASUREMENT_GUIDANCE_LABEL: "pure",
                    }
                },
            )
        }
        taxonomy = _build_taxonomy("test://checker/numeric-with-mg", concepts)
        assert (
            TaxonomyChecker(taxonomy).reportNumericConceptsWithoutMeasurementGuidance()
            == []
        )

    def test_single_utr_candidate_is_still_flagged_and_named(self) -> None:
        # xbrli:sharesItemType has exactly one UTR candidate (shares).
        # UnitResolver already resolves that automatically (see
        # UnitResolver.unitFor), but this is still flagged, forward-looking
        # to a UTR that later adds a second candidate for the same data
        # type -- and the finding names the concrete unit to write.
        concepts = {
            "vsme:Shares": _concept(data_type="xbrli:sharesItemType", numeric=True)
        }
        taxonomy = _build_taxonomy("test://checker/single-candidate-named", concepts)
        [finding] = TaxonomyChecker(
            taxonomy
        ).reportNumericConceptsWithoutMeasurementGuidance()
        assert finding.concepts == (taxonomy.getConcept("vsme:Shares").qname,)
        assert finding.details["candidateUnits"] == ["xbrli:shares"]

    def test_data_type_with_no_utr_candidates_is_silent(self) -> None:
        # xbrli:decimalItemType (bare, no dtr-types wrapper) has no UTR
        # candidates at all -- nothing a label could disambiguate.
        concepts = {
            "vsme:Count": _concept(data_type="xbrli:decimalItemType", numeric=True)
        }
        taxonomy = _build_taxonomy("test://checker/no-candidates-silent", concepts)
        assert (
            TaxonomyChecker(taxonomy).reportNumericConceptsWithoutMeasurementGuidance()
            == []
        )

    def test_monetary_concept_without_guidance_is_silent(self) -> None:
        concepts = {
            "vsme:Amount": _concept(data_type="xbrli:monetaryItemType", numeric=True)
        }
        taxonomy = _build_taxonomy("test://checker/monetary-no-mg-silent", concepts)
        assert (
            TaxonomyChecker(taxonomy).reportNumericConceptsWithoutMeasurementGuidance()
            == []
        )

    def test_abstract_numeric_concept_is_ignored(self) -> None:
        # Ambiguous data type (as in test_ambiguous_data_type_without_guidance_is_flagged)
        # so this proves the abstract exclusion, not the UTR-candidates gate.
        concepts = {
            "vsme:AbstractNumeric": _concept(
                data_type="xbrli:pureItemType", numeric=True, abstract=True
            )
        }
        taxonomy = _build_taxonomy(
            "test://checker/abstract-numeric-no-mg-silent", concepts
        )
        assert (
            TaxonomyChecker(taxonomy).reportNumericConceptsWithoutMeasurementGuidance()
            == []
        )


class TestMeasurementGuidanceNotResolvingToAUnit:
    # xbrli:pureItemType's UTR candidates are pure, Rate and
    # Monetary_per_Monetary (see TestNumericConceptsWithoutMeasurementGuidance).
    def test_unresolvable_guidance_text_is_flagged(self) -> None:
        concepts = {
            "vsme:RatioUnresolvableGuidance": _concept(
                data_type="xbrli:pureItemType",
                numeric=True,
                labels={
                    "en": {
                        _STANDARD_LABEL: "Ratio",
                        _MEASUREMENT_GUIDANCE_LABEL: "not-a-real-unit",
                    }
                },
            )
        }
        taxonomy = _build_taxonomy("test://checker/mg-unresolvable", concepts)
        [finding] = TaxonomyChecker(
            taxonomy
        ).reportMeasurementGuidanceNotResolvingToAUnit()
        assert finding.concepts == (
            taxonomy.getConcept("vsme:RatioUnresolvableGuidance").qname,
        )

    def test_resolvable_guidance_text_is_silent(self) -> None:
        concepts = {
            "vsme:RatioResolvableGuidance": _concept(
                data_type="xbrli:pureItemType",
                numeric=True,
                labels={
                    "en": {
                        _STANDARD_LABEL: "Ratio",
                        _MEASUREMENT_GUIDANCE_LABEL: "pure",
                    }
                },
            )
        }
        taxonomy = _build_taxonomy("test://checker/mg-resolvable-silent", concepts)
        assert (
            TaxonomyChecker(taxonomy).reportMeasurementGuidanceNotResolvingToAUnit()
            == []
        )

    def test_no_guidance_label_is_silent(self) -> None:
        # This checker's job, not this one's -- see
        # TestNumericConceptsWithoutMeasurementGuidance.
        concepts = {
            "vsme:RatioNoGuidanceLabel": _concept(
                data_type="xbrli:pureItemType", numeric=True
            )
        }
        taxonomy = _build_taxonomy("test://checker/mg-absent-silent", concepts)
        assert (
            TaxonomyChecker(taxonomy).reportMeasurementGuidanceNotResolvingToAUnit()
            == []
        )

    def test_data_type_with_no_utr_candidates_is_silent(self) -> None:
        # Nothing in the UTR for this data type could match anyway, so an
        # unresolvable label here is not this check's concern.
        concepts = {
            "vsme:Count": _concept(
                data_type="xbrli:decimalItemType",
                numeric=True,
                labels={
                    "en": {
                        _STANDARD_LABEL: "Count",
                        _MEASUREMENT_GUIDANCE_LABEL: "not-a-real-unit",
                    }
                },
            )
        }
        taxonomy = _build_taxonomy("test://checker/mg-no-candidates-silent", concepts)
        assert (
            TaxonomyChecker(taxonomy).reportMeasurementGuidanceNotResolvingToAUnit()
            == []
        )


class TestMeasurementGuidanceOnNonNumericConcepts:
    def test_string_concept_with_guidance_is_flagged(self) -> None:
        concepts = {
            "vsme:Text": _concept(
                labels={
                    "en": {
                        _STANDARD_LABEL: "Text",
                        _MEASUREMENT_GUIDANCE_LABEL: "n/a",
                    }
                }
            )
        }
        taxonomy = _build_taxonomy("test://checker/mg-on-non-numeric", concepts)
        [finding] = TaxonomyChecker(
            taxonomy
        ).reportMeasurementGuidanceOnNonNumericConcepts()
        assert finding.concepts == (taxonomy.getConcept("vsme:Text").qname,)

    def test_numeric_concept_with_guidance_is_silent(self) -> None:
        concepts = {
            "vsme:Hours": _concept(
                data_type="xbrli:decimalItemType",
                numeric=True,
                labels={
                    "en": {
                        _STANDARD_LABEL: "Hours",
                        _MEASUREMENT_GUIDANCE_LABEL: "hours",
                    }
                },
            )
        }
        taxonomy = _build_taxonomy("test://checker/mg-on-numeric-silent", concepts)
        assert (
            TaxonomyChecker(taxonomy).reportMeasurementGuidanceOnNonNumericConcepts()
            == []
        )


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
        assert any("have no references" in text for text in texts)

    def test_aggregates_label_and_measurement_guidance_checks(self) -> None:
        concepts = {
            "vsme:NoStandard": _concept(labels={"en": {_TERSE_LABEL: "Terse"}}),
            "vsme:RatioAggregate": _concept(
                data_type="xbrli:pureItemType", numeric=True
            ),
            "vsme:Unresolvable": _concept(
                data_type="xbrli:pureItemType",
                numeric=True,
                labels={
                    "en": {
                        _STANDARD_LABEL: "Unresolvable",
                        _MEASUREMENT_GUIDANCE_LABEL: "not-a-real-unit",
                    }
                },
            ),
            "vsme:Text": _concept(
                labels={
                    "en": {
                        _STANDARD_LABEL: "Text",
                        _MEASUREMENT_GUIDANCE_LABEL: "n/a",
                    }
                }
            ),
            # A separate namespace so its fr-majority doesn't have to
            # outweigh the other, en-only "vsme:" concepts above.
            "other:HasFrenchA": _concept(
                labels={
                    "en": {_STANDARD_LABEL: "A"},
                    "fr": {_STANDARD_LABEL: "A-fr"},
                }
            ),
            "other:HasFrenchB": _concept(
                labels={
                    "en": {_STANDARD_LABEL: "B"},
                    "fr": {_STANDARD_LABEL: "B-fr"},
                }
            ),
            "other:MissingFrench": _concept(labels={"en": {_STANDARD_LABEL: "C"}}),
        }
        taxonomy = _build_taxonomy(
            "test://checker/aggregate-labels-refs-mg",
            concepts,
            extra_namespaces={"other": _OTHER_NS},
        )
        texts = {f.text for f in TaxonomyChecker(taxonomy).reportIssues()}
        assert any("no English standard label" in text for text in texts)
        assert any("have no references" in text for text in texts)
        assert any("no measurementGuidance label" in text for text in texts)
        assert any("does not resolve to a valid unit" in text for text in texts)
        assert any("missing a standard label in" in text for text in texts)
        assert any("have a measurementGuidance label" in text for text in texts)

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
