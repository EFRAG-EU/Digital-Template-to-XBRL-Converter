"""Unit tests for Taxonomy's dimensional model: HypercubeDeclaration (every
declared cube, modelled or not) and how open/negative cubes are handled,
built over hand-written taxonomy JSON via loadTaxonomyJSON() -- this needs no
Arelle DTS at all.

Taxonomy.__init__ mutates the `dimensions` dict it is given (several .pop()
calls), and _TAXONOMIES is a process-lifetime registry that rejects a
duplicate entryPoint, so every test builds its own fresh dicts and uses its
own unique entry point.
"""

from __future__ import annotations

import warnings
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import pytest

import mireport.taxonomy as _taxonomy_module
from mireport.exceptions import UnsupportedTaxonomyFeatureException
from mireport.taxonomy import (
    DimensionContainerType,
    Taxonomy,
    loadTaxonomyJSON,
)


@contextmanager
def _expect_unsupported_warning(match: str | None = None) -> Iterator[None]:
    """Taxonomy.__init__ warns via warnings.warn(UserWarning(TaxonomyException)).
    pytest.warns()'s __exit__ rejects that shape (it requires a warning's sole
    arg be a str or Warning, and TaxonomyException is neither), so assert on it
    the same way scripts/update-taxonomy.py's checkTaxonomyJson() does: record
    and inspect directly."""
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        yield
    assert caught, "expected a warning but none was emitted"
    if match is not None:
        assert any(match in str(w.message) for w in caught), caught


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
    context_element: str = "scenario",
) -> dict[str, Any]:
    cube: dict[str, Any] = {
        "primaryItems": [[i, q] for i, q in enumerate(primary_items)],
        "xbrldt:contextElement": context_element,
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


_BASE_CONCEPTS = {
    "vsme:Table": _concept(hypercube=True),
    "vsme:LineItems": _concept(abstract=True),
    "vsme:Axis": _concept(dimension=True),
    "vsme:MemberA": _concept(),
    "vsme:Item": _concept(),
}


class TestHypercubeDeclarations:
    def test_closed_positive_cube_is_declared_and_modelled(self) -> None:
        dimensions = {"role-1": {"vsme:Table": _cube(["vsme:Item"])}}
        taxonomy = _build_taxonomy(
            "test://dims/closed-positive", _BASE_CONCEPTS, dimensions=dimensions
        )
        [declaration] = taxonomy.hypercubeDeclarations
        assert declaration.modelled is True
        assert declaration.type.value == "positive"
        assert declaration.closed is True
        assert declaration.hypercube == taxonomy.getConcept("vsme:Table")
        assert declaration.primaryItems == {taxonomy.getConcept("vsme:Item")}
        [signature] = taxonomy.dimensionSignatures
        assert signature.hypercube == declaration.hypercube

    def test_open_cube_is_declared_but_not_modelled(self) -> None:
        dimensions = {"role-1": {"vsme:Table": _cube(["vsme:Item"], closed=False)}}
        with _expect_unsupported_warning():
            taxonomy = _build_taxonomy(
                "test://dims/open-not-modelled", _BASE_CONCEPTS, dimensions=dimensions
            )
        [declaration] = taxonomy.hypercubeDeclarations
        assert declaration.modelled is False
        assert declaration.closed is False
        assert taxonomy.dimensionSignatures == ()

    def test_open_cube_declaration_still_carries_primary_items(self) -> None:
        # The GRI regression test: every one of GRI's 129 hypercubes is open,
        # so today's dimensionSignatures is empty for it and the checker
        # cannot see a single primary item. hypercubeDeclarations must not
        # have the same blind spot.
        dimensions = {"role-1": {"vsme:Table": _cube(["vsme:Item"], closed=False)}}
        with _expect_unsupported_warning():
            taxonomy = _build_taxonomy(
                "test://dims/open-primary-items", _BASE_CONCEPTS, dimensions=dimensions
            )
        [declaration] = taxonomy.hypercubeDeclarations
        assert declaration.primaryItems == {taxonomy.getConcept("vsme:Item")}

    def test_open_cube_declaration_still_carries_explicit_dimensions(self) -> None:
        dimensions = {
            "role-1": {
                "vsme:Table": _cube(
                    ["vsme:Item"], {"vsme:Axis": ["vsme:MemberA"]}, closed=False
                )
            }
        }
        with _expect_unsupported_warning():
            taxonomy = _build_taxonomy(
                "test://dims/open-explicit-dims", _BASE_CONCEPTS, dimensions=dimensions
            )
        [declaration] = taxonomy.hypercubeDeclarations
        [eds] = declaration.explicitDimensions
        assert eds.dimension == taxonomy.getConcept("vsme:Axis")
        assert eds.domain == {taxonomy.getConcept("vsme:MemberA")}

    def test_negative_cube_is_declared_but_not_modelled(self) -> None:
        dimensions = {"role-1": {"vsme:Table": _cube(["vsme:Item"], negative=True)}}
        with _expect_unsupported_warning():
            taxonomy = _build_taxonomy(
                "test://dims/negative-not-modelled",
                _BASE_CONCEPTS,
                dimensions=dimensions,
            )
        [declaration] = taxonomy.hypercubeDeclarations
        assert declaration.type.value == "negative"
        assert declaration.modelled is False
        assert taxonomy.dimensionSignatures == ()

    def test_missing_type_key_means_positive(self) -> None:
        # Older baked JSON predates the "type" key entirely.
        cube = _cube(["vsme:Item"])
        assert "type" not in cube
        dimensions = {"role-1": {"vsme:Table": cube}}
        taxonomy = _build_taxonomy(
            "test://dims/legacy-json", _BASE_CONCEPTS, dimensions=dimensions
        )
        [declaration] = taxonomy.hypercubeDeclarations
        assert declaration.type.value == "positive"

    def test_declarations_are_ordered_by_role_then_hypercube(self) -> None:
        concepts = {
            **_BASE_CONCEPTS,
            "vsme:TableB": _concept(hypercube=True),
            "vsme:ItemB": _concept(),
        }
        dimensions = {
            "role-b": {"vsme:Table": _cube(["vsme:Item"])},
            "role-a": {
                "vsme:TableB": _cube(["vsme:ItemB"]),
                "vsme:Table": _cube(["vsme:Item"]),
            },
        }
        taxonomy = _build_taxonomy(
            "test://dims/ordering", concepts, dimensions=dimensions
        )
        locations = [
            (d.roleUri, str(d.hypercube.qname)) for d in taxonomy.hypercubeDeclarations
        ]
        assert locations == sorted(locations)
        assert len(locations) == 3

    def test_overlapping_primary_item_cube_is_still_modelled(self) -> None:
        # A primary item declared in two hypercubes of one base set is
        # rejected by _rejectUnsupported(), but the cube itself is still a
        # well-formed closed positive cube -- modelled is not the same thing
        # as usable.
        concepts = {
            **_BASE_CONCEPTS,
            "vsme:TableB": _concept(hypercube=True),
        }
        dimensions = {
            "role-1": {
                "vsme:Table": _cube(["vsme:Item"]),
                "vsme:TableB": _cube(["vsme:Item"]),
            }
        }
        with _expect_unsupported_warning():
            taxonomy = _build_taxonomy(
                "test://dims/overlapping-primary-item", concepts, dimensions=dimensions
            )
        assert all(d.modelled for d in taxonomy.hypercubeDeclarations)


class TestNegativeHypercubesAreUnsupported:
    def test_closed_negative_cube_builds_no_signature(self) -> None:
        dimensions = {"role-1": {"vsme:Table": _cube(["vsme:Item"], negative=True)}}
        with _expect_unsupported_warning():
            taxonomy = _build_taxonomy(
                "test://dims/negative-no-signature",
                _BASE_CONCEPTS,
                dimensions=dimensions,
            )
        assert taxonomy.dimensionSignatures == ()

    def test_closed_negative_cube_warns_at_load(self) -> None:
        dimensions = {"role-1": {"vsme:Table": _cube(["vsme:Item"], negative=True)}}
        with _expect_unsupported_warning("negative"):
            _build_taxonomy(
                "test://dims/negative-warns", _BASE_CONCEPTS, dimensions=dimensions
            )

    def test_primary_item_of_negative_cube_is_rejected(self) -> None:
        dimensions = {"role-1": {"vsme:Table": _cube(["vsme:Item"], negative=True)}}
        with _expect_unsupported_warning():
            taxonomy = _build_taxonomy(
                "test://dims/negative-primary-item-rejected",
                _BASE_CONCEPTS,
                dimensions=dimensions,
            )
        with pytest.raises(UnsupportedTaxonomyFeatureException):
            taxonomy.getValidDimensionsForPrimaryItem(taxonomy.getConcept("vsme:Item"))

    def test_hypercube_of_negative_cube_is_rejected(self) -> None:
        dimensions = {"role-1": {"vsme:Table": _cube(["vsme:Item"], negative=True)}}
        with _expect_unsupported_warning():
            taxonomy = _build_taxonomy(
                "test://dims/negative-hypercube-rejected",
                _BASE_CONCEPTS,
                dimensions=dimensions,
            )
        with pytest.raises(UnsupportedTaxonomyFeatureException):
            taxonomy.getValidDimensionsForHypercube(taxonomy.getConcept("vsme:Table"))

    def test_open_negative_cube_reports_negative_not_open(self) -> None:
        # Ordering in _unsupportedCubeReason: calling an open negative cube
        # "open" would report the one thing about it that is right.
        dimensions = {
            "role-1": {"vsme:Table": _cube(["vsme:Item"], closed=False, negative=True)}
        }
        with _expect_unsupported_warning("negative"):
            _build_taxonomy(
                "test://dims/open-negative-reason",
                _BASE_CONCEPTS,
                dimensions=dimensions,
            )

    def test_negative_cube_in_one_role_does_not_disturb_positive_cube_in_another(
        self,
    ) -> None:
        dimensions = {
            "role-1": {"vsme:Table": _cube(["vsme:Item"], negative=True)},
            "role-2": {"vsme:Table": _cube(["vsme:Item"])},
        }
        with _expect_unsupported_warning():
            taxonomy = _build_taxonomy(
                "test://dims/negative-and-positive-roles",
                _BASE_CONCEPTS,
                dimensions=dimensions,
            )
        [signature] = taxonomy.dimensionSignatures
        assert signature.roleUri == "role-2"


class TestUnmodelledCubesDoNotLeak:
    def test_open_cube_context_element_does_not_force_container_choice(self) -> None:
        # Today an unmodelled cube contributes to neither desired_containers
        # nor domainByDimension. An open segment cube alongside a closed
        # scenario cube must not raise "Multiple dimension containers".
        concepts = {
            **_BASE_CONCEPTS,
            "vsme:TableB": _concept(hypercube=True),
        }
        dimensions = {
            "role-1": {
                "vsme:Table": _cube(
                    ["vsme:Item"], closed=False, context_element="segment"
                ),
                "vsme:TableB": _cube(["vsme:Item"], context_element="scenario"),
            }
        }
        with _expect_unsupported_warning():
            taxonomy = _build_taxonomy(
                "test://dims/mixed-container", concepts, dimensions=dimensions
            )
        assert taxonomy.dimensionContainer == DimensionContainerType.Scenario

    def test_open_cube_domain_members_are_not_added_to_dimension_domain(self) -> None:
        dimensions = {
            "role-1": {
                "vsme:Table": _cube(
                    ["vsme:Item"], {"vsme:Axis": ["vsme:MemberA"]}, closed=False
                )
            }
        }
        with _expect_unsupported_warning():
            taxonomy = _build_taxonomy(
                "test://dims/open-domain-members-excluded",
                _BASE_CONCEPTS,
                dimensions=dimensions,
            )
        assert (
            taxonomy.getDomainMembersForExplicitDimension(
                taxonomy.getConcept("vsme:Axis")
            )
            == frozenset()
        )

    def test_negative_cube_domain_members_are_not_added_to_dimension_domain(
        self,
    ) -> None:
        dimensions = {
            "role-1": {
                "vsme:Table": _cube(
                    ["vsme:Item"], {"vsme:Axis": ["vsme:MemberA"]}, negative=True
                )
            }
        }
        with _expect_unsupported_warning():
            taxonomy = _build_taxonomy(
                "test://dims/negative-domain-members-excluded",
                _BASE_CONCEPTS,
                dimensions=dimensions,
            )
        assert (
            taxonomy.getDomainMembersForExplicitDimension(
                taxonomy.getConcept("vsme:Axis")
            )
            == frozenset()
        )
