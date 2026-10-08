"""Unit tests for Taxonomy/Concept reference support (mireport.taxonomy.Reference),
built over hand-written taxonomy JSON via loadTaxonomyJSON() -- this needs no
Arelle DTS at all. See test_taxonomy_dimensions.py for the same approach.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest

import mireport.taxonomy as _taxonomy_module
from mireport.exceptions import TaxonomyException
from mireport.taxonomy import Taxonomy, loadTaxonomyJSON

_NS = "https://example.com/vsme"
_REF_NS = "http://www.xbrl.org/2006/ref"
_STANDARD_LABEL = "http://www.xbrl.org/2003/role/label"
_REFERENCE_ROLE = "http://www.xbrl.org/2003/role/reference"


@pytest.fixture(autouse=True)
def _isolated_taxonomy_registry() -> Iterator[None]:
    """loadTaxonomyJSON() registers into the process-lifetime _TAXONOMIES
    registry with no way to unregister; remove whatever this test added so it
    leaves no trace for later tests (see test_taxonomy_dimensions.py)."""
    before = set(_taxonomy_module._TAXONOMIES)
    yield
    for entryPoint in set(_taxonomy_module._TAXONOMIES) - before:
        del _taxonomy_module._TAXONOMIES[entryPoint]


def _concept(
    *,
    labels: dict[str, dict[str, str]] | None = None,
    references: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    jconcept: dict[str, Any] = {
        "labels": labels if labels is not None else {"en": {_STANDARD_LABEL: "Label"}},
        "dataType": "xbrli:stringItemType",
        "baseDataType": "xbrli:stringItemType",
        "periodType": "duration",
    }
    if references is not None:
        # The legacy, pre-top-level-references shape: a "references" list
        # embedded in the concept itself.
        jconcept["references"] = references
    return jconcept


def _reference(
    *,
    role: str = _REFERENCE_ROLE,
    parts: list[list[str]] | None = None,
    concepts: list[str],
    orders: dict[str, float] | None = None,
) -> dict[str, Any]:
    jref: dict[str, Any] = {
        "role": role,
        "parts": parts if parts is not None else [["ref:Name", "ISO"]],
        "concepts": concepts,
    }
    if orders is not None:
        jref["orders"] = orders
    return jref


def _build_taxonomy(
    entry_point: str,
    concepts: dict[str, dict[str, Any]],
    *,
    references: list[dict[str, Any]] | None = None,
) -> Taxonomy:
    bits: dict[str, Any] = {
        "entryPoint": entry_point,
        "namespaces": {
            "vsme": _NS,
            "xs": "http://www.w3.org/2001/XMLSchema",
            "ref": _REF_NS,
        },
        "concepts": concepts,
        "presentation": {},
        "dimensions": {},
    }
    if references is not None:
        # references=None means "not given at all" -- exercises the legacy
        # per-concept fold in _createTaxonomyFromJSON(); references=[] means
        # "given, but empty" -- exercises the native, top-level shape.
        bits["references"] = references
    return loadTaxonomyJSON(bits)


_BASE_CONCEPTS = {
    "vsme:A": _concept(),
    "vsme:B": _concept(),
}


class TestSharedReference:
    def test_same_object_reachable_from_both_concepts(self) -> None:
        taxonomy = _build_taxonomy(
            "test://refs/shared",
            _BASE_CONCEPTS,
            references=[_reference(concepts=["vsme:A", "vsme:B"])],
        )
        a, b = taxonomy.getConcept("vsme:A"), taxonomy.getConcept("vsme:B")
        assert len(taxonomy.references) == 1
        [refA] = a.references
        [refB] = b.references
        assert refA is refB
        assert refA.concepts == {a, b}

    def test_concept_with_no_references_is_empty(self) -> None:
        taxonomy = _build_taxonomy("test://refs/none", _BASE_CONCEPTS, references=[])
        assert taxonomy.getConcept("vsme:A").references == ()
        assert taxonomy.references == ()


class TestPerConceptOrder:
    def test_order_used_for_that_concept_only(self) -> None:
        taxonomy = _build_taxonomy(
            "test://refs/order",
            _BASE_CONCEPTS,
            references=[
                _reference(concepts=["vsme:A", "vsme:B"], orders={"vsme:B": 2}),
            ],
        )
        a, b = taxonomy.getConcept("vsme:A"), taxonomy.getConcept("vsme:B")
        [refA] = a.references
        [refB] = b.references
        assert refA is refB
        assert refA.getOrder(a) == 1.0
        assert refA.getOrder(b) == 2.0

    def test_several_references_on_one_concept_sort_by_order_first(self) -> None:
        taxonomy = _build_taxonomy(
            "test://refs/multi-order",
            _BASE_CONCEPTS,
            references=[
                _reference(
                    parts=[["ref:Name", "Second"]],
                    concepts=["vsme:A"],
                    orders={"vsme:A": 2},
                ),
                _reference(parts=[["ref:Name", "First"]], concepts=["vsme:A"]),
            ],
        )
        a = taxonomy.getConcept("vsme:A")
        names = [ref.getPart("ref:Name") for ref in a.references]
        assert names == ["First", "Second"]


class TestGetPart:
    def test_returns_the_value(self) -> None:
        taxonomy = _build_taxonomy(
            "test://refs/getpart",
            _BASE_CONCEPTS,
            references=[
                _reference(
                    parts=[["ref:Name", "ISO"], ["ref:Number", "3166-1"]],
                    concepts=["vsme:A"],
                )
            ],
        )
        [ref] = taxonomy.getConcept("vsme:A").references
        assert ref.getPart("ref:Name") == "ISO"
        assert ref.getPart("ref:Number") == "3166-1"

    def test_missing_part_returns_fallback(self) -> None:
        taxonomy = _build_taxonomy(
            "test://refs/getpart-missing",
            _BASE_CONCEPTS,
            references=[_reference(concepts=["vsme:A"])],
        )
        [ref] = taxonomy.getConcept("vsme:A").references
        assert ref.getPart("ref:URI") is None
        assert ref.getPart("ref:URI", fallback="none") == "none"

    def test_repeated_part_name_returns_all_values_in_document_order(self) -> None:
        taxonomy = _build_taxonomy(
            "test://refs/getpart-repeated",
            _BASE_CONCEPTS,
            references=[
                _reference(
                    parts=[["ref:Section", "1"], ["ref:Section", "2"]],
                    concepts=["vsme:A"],
                )
            ],
        )
        [ref] = taxonomy.getConcept("vsme:A").references
        assert ref.getPart("ref:Section") == "1"
        assert ref.partValues("ref:Section") == ("1", "2")


class TestLegacyConceptReferencesFold:
    def test_legacy_shape_folds_to_the_same_result(self) -> None:
        legacyParts = [["ref:Name", "ISO"], ["ref:Number", "3166-1"]]
        concepts = {
            "vsme:A": _concept(
                references=[{"role": _REFERENCE_ROLE, "parts": legacyParts}]
            ),
            "vsme:B": _concept(
                references=[{"role": _REFERENCE_ROLE, "parts": legacyParts}]
            ),
        }
        taxonomy = _build_taxonomy("test://refs/legacy", concepts)

        a, b = taxonomy.getConcept("vsme:A"), taxonomy.getConcept("vsme:B")
        assert len(taxonomy.references) == 1
        [refA] = a.references
        [refB] = b.references
        assert refA is refB
        assert refA.concepts == {a, b}
        assert refA.getPart("ref:Name") == "ISO"

    def test_legacy_concept_without_references_key_has_no_references(self) -> None:
        concepts = {"vsme:A": _concept()}
        taxonomy = _build_taxonomy("test://refs/legacy-none", concepts)
        assert taxonomy.getConcept("vsme:A").references == ()
        assert taxonomy.references == ()


class TestUnknownConcept:
    def test_unknown_concept_qname_raises(self) -> None:
        with pytest.raises(TaxonomyException, match="unknown concept"):
            _build_taxonomy(
                "test://refs/unknown-concept",
                _BASE_CONCEPTS,
                references=[_reference(concepts=["vsme:DoesNotExist"])],
            )


class TestMalformedReference:
    def test_missing_role_raises(self) -> None:
        with pytest.raises(TaxonomyException, match="role"):
            _build_taxonomy(
                "test://refs/no-role",
                _BASE_CONCEPTS,
                references=[{"parts": [["ref:Name", "ISO"]], "concepts": ["vsme:A"]}],
            )

    def test_missing_parts_raises(self) -> None:
        with pytest.raises(TaxonomyException, match="parts"):
            _build_taxonomy(
                "test://refs/no-parts",
                _BASE_CONCEPTS,
                references=[{"role": _REFERENCE_ROLE, "concepts": ["vsme:A"]}],
            )
