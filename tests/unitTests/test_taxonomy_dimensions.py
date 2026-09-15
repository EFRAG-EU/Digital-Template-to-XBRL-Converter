"""Unit tests for Taxonomy's dimensional-validity model: HypercubeDeclaration
(one per declared cube, always modelled now) and EffectiveHypercube (the
per-(base set, primary item) AND/OR combinator implementing XBRL Dimensions
1.0 section 3.1.1/3.1.2/2.3.1), built over hand-written taxonomy JSON via
loadTaxonomyJSON() -- this needs no Arelle DTS at all.

Design notes baked into these tests (confirmed against the XBRL Dimensions
1.0 REC and WGN "Guidance on the use of dimensions"):

- HypercubeDeclaration.dimensionsAreValid() checks ONLY that hypercube's own
  declared dimensions (domain/default for explicit, presence for typed) --
  it never rejects a dimension it doesn't itself declare. "Extra dimension"
  rejection is not a per-hypercube concept.
- EffectiveHypercube.matches() ANDs dimensionsAreValid() across every
  hypercube declared for one (base set, primary item), negating notAll
  results (section 2.3.1/3.1.2).
- Closedness is evaluated once, at the EffectiveHypercube level, against the
  UNION of every *positive* conjunct's own declared dimensions -- never a
  negative conjunct's, since notAll only ever subtracts validity from the
  space positives define, it never grants a dimension permission to appear.
  With no positive conjunct at all, there is no closedness restriction
  (WGN section 3.3: "any combination of dimension values which is not
  explicitly excluded will be considered valid").
- Two closed positive hypercubes sharing a primary item in one base set are
  therefore not a contradiction: the primary item ends up needing valid
  values for the union of both hypercubes' dimensions, closed to that union.

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
    HypercubeType,
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


@contextmanager
def _expect_no_warning() -> Iterator[None]:
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        yield
    assert caught == [], caught


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
    typed_dimensions: list[str] | None = None,
) -> dict[str, Any]:
    cube: dict[str, Any] = {
        "primaryItems": [[i, q] for i, q in enumerate(primary_items)],
        "xbrldt:contextElement": context_element,
        "xbrldt:closed": closed,
        "explicitDimensions": explicit_dimensions or {},
    }
    if typed_dimensions:
        cube["typedDimensions"] = typed_dimensions
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
    "vsme:MemberB": _concept(),
    "vsme:OtherAxis": _concept(dimension=True),
    "vsme:OtherMember": _concept(),
    "vsme:TypedAxis": _concept(dimension=True, other={"typedElement": "vsme:TYP"}),
    "vsme:Item": _concept(),
}


class TestHypercubeDeclarations:
    """Every declared cube is now modelled -- open, closed, positive and
    negative alike. Only container mismatch remains genuinely unsupported
    (see TestContainerMismatchIsUnsupported)."""

    def test_closed_positive_cube_is_modelled(self) -> None:
        dimensions = {"role-1": {"vsme:Table": _cube(["vsme:Item"])}}
        with _expect_no_warning():
            taxonomy = _build_taxonomy(
                "test://dims/closed-positive", _BASE_CONCEPTS, dimensions=dimensions
            )
        [declaration] = taxonomy.hypercubeDeclarations
        assert declaration.modelled is True
        assert declaration.type is HypercubeType.Positive
        assert declaration.closed is True
        assert declaration.hypercube == taxonomy.getConcept("vsme:Table")
        assert declaration.primaryItems == {taxonomy.getConcept("vsme:Item")}

    def test_open_cube_is_now_modelled(self) -> None:
        dimensions = {"role-1": {"vsme:Table": _cube(["vsme:Item"], closed=False)}}
        with _expect_no_warning():
            taxonomy = _build_taxonomy(
                "test://dims/open-modelled", _BASE_CONCEPTS, dimensions=dimensions
            )
        [declaration] = taxonomy.hypercubeDeclarations
        assert declaration.modelled is True
        assert declaration.closed is False

    def test_negative_cube_is_now_modelled(self) -> None:
        dimensions = {"role-1": {"vsme:Table": _cube(["vsme:Item"], negative=True)}}
        with _expect_no_warning():
            taxonomy = _build_taxonomy(
                "test://dims/negative-modelled", _BASE_CONCEPTS, dimensions=dimensions
            )
        [declaration] = taxonomy.hypercubeDeclarations
        assert declaration.modelled is True
        assert declaration.type is HypercubeType.Negative

    def test_missing_type_key_means_positive(self) -> None:
        # Older baked JSON predates the "type" key entirely.
        cube = _cube(["vsme:Item"])
        assert "type" not in cube
        dimensions = {"role-1": {"vsme:Table": cube}}
        taxonomy = _build_taxonomy(
            "test://dims/legacy-json", _BASE_CONCEPTS, dimensions=dimensions
        )
        [declaration] = taxonomy.hypercubeDeclarations
        assert declaration.type is HypercubeType.Positive

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

    def test_overlapping_primary_item_cubes_are_both_modelled(self) -> None:
        # A primary item declared in two hypercubes of one base set used to
        # be rejected outright (_overlappingPrimaryItems); XDT section 3.1.2
        # says to AND them, which EffectiveHypercube now does.
        concepts = {**_BASE_CONCEPTS, "vsme:TableB": _concept(hypercube=True)}
        dimensions = {
            "role-1": {
                "vsme:Table": _cube(["vsme:Item"]),
                "vsme:TableB": _cube(["vsme:Item"]),
            }
        }
        with _expect_no_warning():
            taxonomy = _build_taxonomy(
                "test://dims/overlapping-primary-item", concepts, dimensions=dimensions
            )
        assert all(d.modelled for d in taxonomy.hypercubeDeclarations)


class TestHypercubeDeclarationDimensionsAreValid:
    """HypercubeDeclaration.dimensionsAreValid() checks only that
    hypercube's own declared dimensions -- domain/default for explicit,
    presence for typed. It never rejects a dimension it doesn't declare;
    that is EffectiveHypercube's closedness concern, not this one's."""

    def _declaration(
        self,
        name: str,
        *,
        explicit_dimensions: dict[str, list[str]] | None = None,
        typed_dimensions: list[str] | None = None,
        defaults: dict[str, str] | None = None,
    ) -> tuple[Taxonomy, Any]:
        dimensions: dict[str, Any] = {
            "role-1": {
                "vsme:Table": _cube(
                    ["vsme:Item"],
                    explicit_dimensions,
                    typed_dimensions=typed_dimensions,
                )
            }
        }
        if defaults:
            dimensions["_defaults"] = defaults
        taxonomy = _build_taxonomy(
            f"test://dims/valid/{name}", _BASE_CONCEPTS, dimensions=dimensions
        )
        [declaration] = taxonomy.hypercubeDeclarations
        return taxonomy, declaration

    def test_value_in_domain_is_valid(self) -> None:
        taxonomy, declaration = self._declaration(
            "in-domain", explicit_dimensions={"vsme:Axis": ["vsme:MemberA"]}
        )
        axis, memberA = (
            taxonomy.getConcept("vsme:Axis"),
            taxonomy.getConcept("vsme:MemberA"),
        )
        assert declaration.dimensionsAreValid({axis: memberA}, {}) is True

    def test_value_out_of_domain_is_invalid(self) -> None:
        taxonomy, declaration = self._declaration(
            "out-of-domain", explicit_dimensions={"vsme:Axis": ["vsme:MemberA"]}
        )
        axis, memberB = (
            taxonomy.getConcept("vsme:Axis"),
            taxonomy.getConcept("vsme:MemberB"),
        )
        assert declaration.dimensionsAreValid({axis: memberB}, {}) is False

    def test_missing_dimension_without_default_is_invalid(self) -> None:
        _, declaration = self._declaration(
            "missing-no-default", explicit_dimensions={"vsme:Axis": ["vsme:MemberA"]}
        )
        assert declaration.dimensionsAreValid({}, {}) is False

    def test_missing_dimension_with_default_is_valid(self) -> None:
        _, declaration = self._declaration(
            "missing-with-default",
            explicit_dimensions={"vsme:Axis": ["vsme:MemberA"]},
            defaults={"vsme:Axis": "vsme:MemberA"},
        )
        assert declaration.dimensionsAreValid({}, {}) is True

    def test_undeclared_extra_explicit_dimension_does_not_make_this_invalid(
        self,
    ) -> None:
        # Not this hypercube's concern -- a dimension it never declared is
        # simply irrelevant to it.
        taxonomy, declaration = self._declaration("declares-nothing")
        other, otherMember = (
            taxonomy.getConcept("vsme:OtherAxis"),
            taxonomy.getConcept("vsme:OtherMember"),
        )
        assert declaration.dimensionsAreValid({other: otherMember}, {}) is True

    def test_declared_typed_dimension_must_be_present(self) -> None:
        taxonomy, declaration = self._declaration(
            "typed-required", typed_dimensions=["vsme:TypedAxis"]
        )
        typedAxis = taxonomy.getConcept("vsme:TypedAxis")
        assert declaration.dimensionsAreValid({}, {typedAxis: "value"}) is True
        assert declaration.dimensionsAreValid({}, {}) is False

    def test_undeclared_extra_typed_dimension_does_not_make_this_invalid(self) -> None:
        taxonomy, declaration = self._declaration(
            "typed-extra-irrelevant", typed_dimensions=["vsme:TypedAxis"]
        )
        typedAxis = taxonomy.getConcept("vsme:TypedAxis")
        other = taxonomy.getConcept("vsme:OtherAxis")
        assert (
            declaration.dimensionsAreValid({}, {typedAxis: "value", other: "extra"})
            is True
        )


class TestEffectiveHypercube:
    """EffectiveHypercube.matches(): AND of dimensionsAreValid() across every
    hypercube declared for one (base set, primary item), notAll negated
    (section 2.3.1/3.1.2), plus closedness evaluated once against the union
    of every *positive* conjunct's own declared dimensions."""

    def test_single_positive_matches_when_satisfied(self) -> None:
        dimensions = {"role-1": {"vsme:Table": _cube(["vsme:Item"])}}
        taxonomy = _build_taxonomy(
            "test://dims/eh-single-positive", _BASE_CONCEPTS, dimensions=dimensions
        )
        [effective] = taxonomy.getEffectiveHypercubesForPrimaryItem(
            taxonomy.getConcept("vsme:Item")
        )
        assert effective.matches({}, {}) is True

    def test_single_closed_positive_rejects_extra_dimension(self) -> None:
        dimensions = {
            "role-1": {
                "vsme:Table": _cube(["vsme:Item"], {"vsme:Axis": ["vsme:MemberA"]})
            }
        }
        taxonomy = _build_taxonomy(
            "test://dims/eh-single-closed-rejects-extra",
            _BASE_CONCEPTS,
            dimensions=dimensions,
        )
        [effective] = taxonomy.getEffectiveHypercubesForPrimaryItem(
            taxonomy.getConcept("vsme:Item")
        )
        axis, memberA = (
            taxonomy.getConcept("vsme:Axis"),
            taxonomy.getConcept("vsme:MemberA"),
        )
        other, otherMember = (
            taxonomy.getConcept("vsme:OtherAxis"),
            taxonomy.getConcept("vsme:OtherMember"),
        )
        assert effective.matches({axis: memberA}, {}) is True
        assert effective.matches({axis: memberA, other: otherMember}, {}) is False

    def test_single_open_positive_admits_extra_dimension(self) -> None:
        dimensions = {"role-1": {"vsme:Table": _cube(["vsme:Item"], closed=False)}}
        taxonomy = _build_taxonomy(
            "test://dims/eh-single-open-admits-extra",
            _BASE_CONCEPTS,
            dimensions=dimensions,
        )
        [effective] = taxonomy.getEffectiveHypercubesForPrimaryItem(
            taxonomy.getConcept("vsme:Item")
        )
        other, otherMember = (
            taxonomy.getConcept("vsme:OtherAxis"),
            taxonomy.getConcept("vsme:OtherMember"),
        )
        assert effective.matches({other: otherMember}, {}) is True

    def test_negative_alone_excludes_its_declared_region(self) -> None:
        dimensions = {
            "role-1": {
                "vsme:Table": _cube(
                    ["vsme:Item"], {"vsme:Axis": ["vsme:MemberA"]}, negative=True
                )
            }
        }
        taxonomy = _build_taxonomy(
            "test://dims/eh-negative-excludes", _BASE_CONCEPTS, dimensions=dimensions
        )
        [effective] = taxonomy.getEffectiveHypercubesForPrimaryItem(
            taxonomy.getConcept("vsme:Item")
        )
        axis, memberA = (
            taxonomy.getConcept("vsme:Axis"),
            taxonomy.getConcept("vsme:MemberA"),
        )
        # Inside the excluded region -> dimensionsAreValid True -> negated -> invalid.
        assert effective.matches({axis: memberA}, {}) is False

    def test_negative_alone_has_no_closedness_restriction(self) -> None:
        # WGN section 3.3: with no positive conjunct, "any combination of
        # dimension values which is not explicitly excluded will be
        # considered valid" -- even though the lone negative cube here is
        # declared closed=True (which WGN itself says has no agreed meaning
        # for a notAll hypercube).
        dimensions = {
            "role-1": {
                "vsme:Table": _cube(
                    ["vsme:Item"], {"vsme:Axis": ["vsme:MemberA"]}, negative=True
                )
            }
        }
        taxonomy = _build_taxonomy(
            "test://dims/eh-negative-no-closedness",
            _BASE_CONCEPTS,
            dimensions=dimensions,
        )
        [effective] = taxonomy.getEffectiveHypercubesForPrimaryItem(
            taxonomy.getConcept("vsme:Item")
        )
        axis, memberB = (
            taxonomy.getConcept("vsme:Axis"),
            taxonomy.getConcept("vsme:MemberB"),
        )
        # memberB is outside the negative cube's own domain -> not excluded.
        assert effective.matches({axis: memberB}, {}) is True
        # An entirely unrelated extra dimension is also fine -- no positive
        # conjunct exists to impose closedness.
        other, otherMember = (
            taxonomy.getConcept("vsme:OtherAxis"),
            taxonomy.getConcept("vsme:OtherMember"),
        )
        assert effective.matches({other: otherMember}, {}) is True

    def test_positive_and_negative_pair_on_the_same_dimension(self) -> None:
        # The WGN-recommended way to use notAll at all: pair it with a
        # positive hypercube in the same base set to carve out a sub-region.
        concepts = {**_BASE_CONCEPTS, "vsme:TableB": _concept(hypercube=True)}
        dimensions = {
            "role-1": {
                "vsme:Table": _cube(
                    ["vsme:Item"], {"vsme:Axis": ["vsme:MemberA", "vsme:MemberB"]}
                ),
                "vsme:TableB": _cube(
                    ["vsme:Item"], {"vsme:Axis": ["vsme:MemberA"]}, negative=True
                ),
            }
        }
        taxonomy = _build_taxonomy(
            "test://dims/eh-positive-negative-pair", concepts, dimensions=dimensions
        )
        [effective] = taxonomy.getEffectiveHypercubesForPrimaryItem(
            taxonomy.getConcept("vsme:Item")
        )
        axis = taxonomy.getConcept("vsme:Axis")
        memberA, memberB = (
            taxonomy.getConcept("vsme:MemberA"),
            taxonomy.getConcept("vsme:MemberB"),
        )
        # MemberA is admitted by the positive cube but excluded by the negative one.
        assert effective.matches({axis: memberA}, {}) is False
        # MemberB is admitted by the positive cube and not excluded.
        assert effective.matches({axis: memberB}, {}) is True

    def test_negative_conjuncts_dimensions_never_grant_closedness_permission(
        self,
    ) -> None:
        # The positive cube declares Axis only; the negative cube declares a
        # *different* dimension (OtherAxis). OtherAxis is not part of what
        # the positive conjunct(s) permit, so setting it at all is a
        # closedness violation -- regardless of whether that value happens
        # to be inside the negative cube's own exclusion domain.
        concepts = {**_BASE_CONCEPTS, "vsme:TableB": _concept(hypercube=True)}
        dimensions = {
            "role-1": {
                "vsme:Table": _cube(["vsme:Item"], {"vsme:Axis": ["vsme:MemberA"]}),
                "vsme:TableB": _cube(
                    ["vsme:Item"],
                    {"vsme:OtherAxis": ["vsme:OtherMember"]},
                    negative=True,
                ),
            }
        }
        taxonomy = _build_taxonomy(
            "test://dims/eh-negative-dims-no-permission",
            concepts,
            dimensions=dimensions,
        )
        [effective] = taxonomy.getEffectiveHypercubesForPrimaryItem(
            taxonomy.getConcept("vsme:Item")
        )
        axis, memberA = (
            taxonomy.getConcept("vsme:Axis"),
            taxonomy.getConcept("vsme:MemberA"),
        )
        other, otherMember = (
            taxonomy.getConcept("vsme:OtherAxis"),
            taxonomy.getConcept("vsme:OtherMember"),
        )
        # Axis alone: valid (negative cube's own dimension is entirely
        # absent, so its exclusion does not apply).
        assert effective.matches({axis: memberA}, {}) is True
        # Axis plus OtherAxis: closedness rejects OtherAxis outright, since
        # only Table (positive) contributes to what's permitted.
        assert effective.matches({axis: memberA, other: otherMember}, {}) is False

    def test_two_closed_positive_hypercubes_on_different_dimensions_both_required(
        self,
    ) -> None:
        # The core correction: overlapping primary items across two closed
        # positive hypercubes means the effective hypercube requires BOTH
        # dimensions together, closed to their union -- not "unsatisfiable".
        concepts = {**_BASE_CONCEPTS, "vsme:TableB": _concept(hypercube=True)}
        dimensions = {
            "role-1": {
                "vsme:Table": _cube(["vsme:Item"], {"vsme:Axis": ["vsme:MemberA"]}),
                "vsme:TableB": _cube(
                    ["vsme:Item"], {"vsme:OtherAxis": ["vsme:OtherMember"]}
                ),
            }
        }
        taxonomy = _build_taxonomy(
            "test://dims/eh-two-closed-different-dims-required",
            concepts,
            dimensions=dimensions,
        )
        [effective] = taxonomy.getEffectiveHypercubesForPrimaryItem(
            taxonomy.getConcept("vsme:Item")
        )
        axis, memberA = (
            taxonomy.getConcept("vsme:Axis"),
            taxonomy.getConcept("vsme:MemberA"),
        )
        other, otherMember = (
            taxonomy.getConcept("vsme:OtherAxis"),
            taxonomy.getConcept("vsme:OtherMember"),
        )
        # Both dimensions together: valid.
        assert effective.matches({axis: memberA, other: otherMember}, {}) is True
        # Either alone: the other hypercube's required dimension is missing.
        assert effective.matches({axis: memberA}, {}) is False
        assert effective.matches({other: otherMember}, {}) is False
        # A third, wholly undeclared dimension: rejected by closedness.
        thirdMember = taxonomy.getConcept("vsme:MemberB")
        assert (
            effective.matches(
                {axis: memberA, other: otherMember, thirdMember: memberA}, {}
            )
            is False
        )

    def test_two_positive_hypercubes_on_the_same_dimension_narrow_to_intersection(
        self,
    ) -> None:
        concepts = {**_BASE_CONCEPTS, "vsme:TableB": _concept(hypercube=True)}
        dimensions = {
            "role-1": {
                "vsme:Table": _cube(
                    ["vsme:Item"], {"vsme:Axis": ["vsme:MemberA", "vsme:MemberB"]}
                ),
                "vsme:TableB": _cube(["vsme:Item"], {"vsme:Axis": ["vsme:MemberA"]}),
            }
        }
        taxonomy = _build_taxonomy(
            "test://dims/eh-two-positive-intersection", concepts, dimensions=dimensions
        )
        [effective] = taxonomy.getEffectiveHypercubesForPrimaryItem(
            taxonomy.getConcept("vsme:Item")
        )
        axis = taxonomy.getConcept("vsme:Axis")
        memberA, memberB = (
            taxonomy.getConcept("vsme:MemberA"),
            taxonomy.getConcept("vsme:MemberB"),
        )
        assert effective.matches({axis: memberA}, {}) is True  # in both domains
        assert effective.matches({axis: memberB}, {}) is False  # only in Table's

    def test_open_and_closed_positive_together_close_to_their_union(self) -> None:
        # "Closed wins": one closed conjunct is enough to restrict the whole
        # combination to the union of every positive conjunct's own
        # dimensions, including the open one's.
        concepts = {**_BASE_CONCEPTS, "vsme:TableB": _concept(hypercube=True)}
        dimensions = {
            "role-1": {
                "vsme:Table": _cube(["vsme:Item"], {"vsme:Axis": ["vsme:MemberA"]}),
                "vsme:TableB": _cube(
                    ["vsme:Item"],
                    {"vsme:OtherAxis": ["vsme:OtherMember"]},
                    closed=False,
                ),
            }
        }
        taxonomy = _build_taxonomy(
            "test://dims/eh-mixed-open-closed-union", concepts, dimensions=dimensions
        )
        [effective] = taxonomy.getEffectiveHypercubesForPrimaryItem(
            taxonomy.getConcept("vsme:Item")
        )
        axis, memberA = (
            taxonomy.getConcept("vsme:Axis"),
            taxonomy.getConcept("vsme:MemberA"),
        )
        other, otherMember = (
            taxonomy.getConcept("vsme:OtherAxis"),
            taxonomy.getConcept("vsme:OtherMember"),
        )
        assert effective.matches({axis: memberA, other: otherMember}, {}) is True
        thirdMember = taxonomy.getConcept("vsme:MemberB")
        assert (
            effective.matches(
                {axis: memberA, other: otherMember, thirdMember: memberA}, {}
            )
            is False
        )

    def test_all_open_positives_have_no_closedness_restriction(self) -> None:
        concepts = {**_BASE_CONCEPTS, "vsme:TableB": _concept(hypercube=True)}
        dimensions = {
            "role-1": {
                "vsme:Table": _cube(
                    ["vsme:Item"], {"vsme:Axis": ["vsme:MemberA"]}, closed=False
                ),
                "vsme:TableB": _cube(
                    ["vsme:Item"],
                    {"vsme:OtherAxis": ["vsme:OtherMember"]},
                    closed=False,
                ),
            }
        }
        taxonomy = _build_taxonomy(
            "test://dims/eh-all-open-no-restriction", concepts, dimensions=dimensions
        )
        [effective] = taxonomy.getEffectiveHypercubesForPrimaryItem(
            taxonomy.getConcept("vsme:Item")
        )
        axis, memberA = (
            taxonomy.getConcept("vsme:Axis"),
            taxonomy.getConcept("vsme:MemberA"),
        )
        other, otherMember = (
            taxonomy.getConcept("vsme:OtherAxis"),
            taxonomy.getConcept("vsme:OtherMember"),
        )
        thirdMember = taxonomy.getConcept("vsme:MemberB")
        assert (
            effective.matches(
                {axis: memberA, other: otherMember, thirdMember: memberA}, {}
            )
            is True
        )

    def test_or_across_base_sets(self) -> None:
        # A fact only has to satisfy one base set's EffectiveHypercube.
        dimensions = {
            "role-1": {
                "vsme:Table": _cube(["vsme:Item"], {"vsme:Axis": ["vsme:MemberA"]})
            },
            "role-2": {
                "vsme:Table": _cube(
                    ["vsme:Item"], {"vsme:OtherAxis": ["vsme:OtherMember"]}
                )
            },
        }
        taxonomy = _build_taxonomy(
            "test://dims/eh-or-across-roles", _BASE_CONCEPTS, dimensions=dimensions
        )
        effectives = taxonomy.getEffectiveHypercubesForPrimaryItem(
            taxonomy.getConcept("vsme:Item")
        )
        assert len(effectives) == 2
        axis, memberA = (
            taxonomy.getConcept("vsme:Axis"),
            taxonomy.getConcept("vsme:MemberA"),
        )
        assert any(eh.matches({axis: memberA}, {}) for eh in effectives)
        other, otherMember = (
            taxonomy.getConcept("vsme:OtherAxis"),
            taxonomy.getConcept("vsme:OtherMember"),
        )
        assert any(eh.matches({other: otherMember}, {}) for eh in effectives)
        # Neither role's dimension set alone satisfies the *other* role.
        assert not all(eh.matches({axis: memberA}, {}) for eh in effectives)


class TestContainerMismatchIsUnsupported:
    """The one remaining unsupported reason: a cube declaring a context
    element other than the taxonomy's chosen one. Chosen by majority (ties
    toward Scenario), decided before any cube is modelled, so it no longer
    depends on which cubes happen to be modellable."""

    def test_majority_scenario_one_segment_cube_is_unsupported(self) -> None:
        concepts = {**_BASE_CONCEPTS, "vsme:TableB": _concept(hypercube=True)}
        dimensions = {
            "role-1": {
                "vsme:Table": _cube(["vsme:Item"], context_element="scenario"),
            },
            "role-2": {
                "vsme:TableB": _cube(["vsme:Item"], context_element="segment"),
            },
        }
        with _expect_unsupported_warning():
            taxonomy = _build_taxonomy(
                "test://dims/container-majority", concepts, dimensions=dimensions
            )
        assert taxonomy.dimensionContainer == DimensionContainerType.Scenario
        with pytest.raises(UnsupportedTaxonomyFeatureException):
            taxonomy.getDeclarationsForHypercube(taxonomy.getConcept("vsme:TableB"))

    def test_taxonomy_still_loads_and_matching_container_cube_is_usable(self) -> None:
        # A different primary item per role, so TableB's container mismatch
        # (and the blanket _rejectUnsupported() it triggers for its own
        # primary items) does not also poison Table's unrelated item.
        concepts = {
            **_BASE_CONCEPTS,
            "vsme:TableB": _concept(hypercube=True),
            "vsme:ItemB": _concept(),
        }
        dimensions = {
            "role-1": {"vsme:Table": _cube(["vsme:Item"], context_element="scenario")},
            "role-2": {"vsme:TableB": _cube(["vsme:ItemB"], context_element="segment")},
        }
        with _expect_unsupported_warning():
            taxonomy = _build_taxonomy(
                "test://dims/container-still-loads", concepts, dimensions=dimensions
            )
        [effective] = taxonomy.getEffectiveHypercubesForPrimaryItem(
            taxonomy.getConcept("vsme:Item")
        )
        assert effective.roleUri == "role-1"
        with pytest.raises(UnsupportedTaxonomyFeatureException):
            taxonomy.getEffectiveHypercubesForPrimaryItem(
                taxonomy.getConcept("vsme:ItemB")
            )

    def test_all_cubes_agreeing_on_segment_is_fine(self) -> None:
        dimensions = {
            "role-1": {"vsme:Table": _cube(["vsme:Item"], context_element="segment")}
        }
        with _expect_no_warning():
            taxonomy = _build_taxonomy(
                "test://dims/container-all-segment",
                _BASE_CONCEPTS,
                dimensions=dimensions,
            )
        assert taxonomy.dimensionContainer == DimensionContainerType.Segment

    def test_tie_breaks_toward_scenario(self) -> None:
        concepts = {**_BASE_CONCEPTS, "vsme:TableB": _concept(hypercube=True)}
        dimensions = {
            "role-1": {"vsme:Table": _cube(["vsme:Item"], context_element="segment")},
            "role-2": {"vsme:TableB": _cube(["vsme:Item"], context_element="scenario")},
        }
        with _expect_unsupported_warning():
            taxonomy = _build_taxonomy(
                "test://dims/container-tie", concepts, dimensions=dimensions
            )
        assert taxonomy.dimensionContainer == DimensionContainerType.Scenario


class TestDomainMembersAggregateAcrossEveryDeclaredCube:
    """Every declared cube (open or closed, positive or negative) is now
    modelled, so its explicit-dimension domain members feed
    getDomainMembersForExplicitDimension() -- unlike before, when an
    unmodelled cube's members were silently dropped."""

    def test_open_cube_domain_members_are_included(self) -> None:
        dimensions = {
            "role-1": {
                "vsme:Table": _cube(
                    ["vsme:Item"], {"vsme:Axis": ["vsme:MemberA"]}, closed=False
                )
            }
        }
        taxonomy = _build_taxonomy(
            "test://dims/open-domain-members-included",
            _BASE_CONCEPTS,
            dimensions=dimensions,
        )
        assert taxonomy.getDomainMembersForExplicitDimension(
            taxonomy.getConcept("vsme:Axis")
        ) == {taxonomy.getConcept("vsme:MemberA")}

    def test_negative_cube_domain_members_are_included(self) -> None:
        dimensions = {
            "role-1": {
                "vsme:Table": _cube(
                    ["vsme:Item"], {"vsme:Axis": ["vsme:MemberA"]}, negative=True
                )
            }
        }
        taxonomy = _build_taxonomy(
            "test://dims/negative-domain-members-included",
            _BASE_CONCEPTS,
            dimensions=dimensions,
        )
        assert taxonomy.getDomainMembersForExplicitDimension(
            taxonomy.getConcept("vsme:Axis")
        ) == {taxonomy.getConcept("vsme:MemberA")}
