"""Unit tests for TypedDomainWrapperElement: the typed domain element of a
typed dimension, loaded from its own "typed_domain_wrapper_elements" section
of the taxonomy JSON (see mireport.arelle.taxonomy_extraction.
extractTypedDomainWrapperElement()) rather than "concepts", since it is a
plain xs:element and not an XBRL concept.

Built over hand-written taxonomy JSON via loadTaxonomyJSON(), as
test_taxonomy_dimensions.py does -- this needs no Arelle DTS at all.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest

import mireport.taxonomy as _taxonomy_module
from mireport.exceptions import TaxonomyException
from mireport.taxonomy import Taxonomy, TypedDomainWrapperElement, loadTaxonomyJSON

_NS = "https://example.com/vsme"
_STANDARD_LABEL = "http://www.xbrl.org/2003/role/label"


@pytest.fixture(autouse=True)
def _isolated_taxonomy_registry() -> Iterator[None]:
    """loadTaxonomyJSON() registers into the process-lifetime _TAXONOMIES
    registry with no way to unregister -- see the identical fixture in
    test_taxonomy_dimensions.py for why this cleanup matters."""
    before = set(_taxonomy_module._TAXONOMIES)
    yield
    for entryPoint in set(_taxonomy_module._TAXONOMIES) - before:
        del _taxonomy_module._TAXONOMIES[entryPoint]


def _concept(
    *,
    dimension: bool = False,
    other: dict[str, Any] | None = None,
) -> dict[str, Any]:
    jconcept: dict[str, Any] = {
        "labels": {"en": {_STANDARD_LABEL: "Label"}},
        "dataType": "xbrli:stringItemType",
        "baseDataType": "xbrli:stringItemType",
        "periodType": "duration",
    }
    if dimension:
        jconcept["abstract"] = True
        jconcept["dimension"] = True
    if other:
        jconcept["other"] = other
    return jconcept


def _wrapper_element(
    *,
    data_type: str = "xs:string",
    base_data_type: str = "xs:string",
    nillable: bool = False,
    labels: dict[str, dict[str, str]] | None = None,
) -> dict[str, Any]:
    jelement: dict[str, Any] = {
        "dataType": data_type,
        "baseDataType": base_data_type,
    }
    if nillable:
        jelement["nillable"] = True
    if labels is not None:
        jelement["labels"] = labels
    return jelement


def _build_taxonomy(
    entry_point: str,
    concepts: dict[str, dict[str, Any]],
    *,
    typed_domain_wrapper_elements: dict[str, dict[str, Any]] | None = None,
) -> Taxonomy:
    bits: dict[str, Any] = {
        "entryPoint": entry_point,
        "namespaces": {"vsme": _NS, "xs": "http://www.w3.org/2001/XMLSchema"},
        "concepts": concepts,
        "presentation": {},
        "dimensions": {},
    }
    if typed_domain_wrapper_elements is not None:
        bits["typed_domain_wrapper_elements"] = typed_domain_wrapper_elements
    return loadTaxonomyJSON(bits)


_BASE_CONCEPTS = {
    "vsme:TypedAxis": _concept(dimension=True, other={"typedElement": "vsme:TYP"}),
    "vsme:PlainConcept": _concept(),
}


class TestLoading:
    def test_typed_dimension_resolves_its_wrapper_element(self) -> None:
        taxonomy = _build_taxonomy(
            "test://typed-wrapper/resolves",
            _BASE_CONCEPTS,
            typed_domain_wrapper_elements={
                "vsme:TYP": _wrapper_element(
                    data_type="vsme:SiteIdentifierType", nillable=True
                )
            },
        )
        dimension = taxonomy.getConcept("vsme:TypedAxis")
        element = dimension.getTypedDomainWrapperElement()
        assert element is not None
        assert str(element.qname) == "vsme:TYP"
        assert str(element.dataType) == "vsme:SiteIdentifierType"
        assert str(element.baseDataType) == "xs:string"
        assert element.isNillable is True

    def test_non_typed_concept_has_no_wrapper_element(self) -> None:
        taxonomy = _build_taxonomy("test://typed-wrapper/non-typed", _BASE_CONCEPTS)
        assert (
            taxonomy.getConcept("vsme:PlainConcept").getTypedDomainWrapperElement()
            is None
        )

    def test_missing_section_is_tolerated_for_older_json(self) -> None:
        # Older or third-party baked JSON predates typed_domain_wrapper_elements
        # entirely; a typed dimension's typedElement then has no entry to
        # resolve, but that must not break loading the rest of the taxonomy.
        taxonomy = _build_taxonomy("test://typed-wrapper/legacy-json", _BASE_CONCEPTS)
        dimension = taxonomy.getConcept("vsme:TypedAxis")
        assert dimension.typedElement is not None
        assert dimension.getTypedDomainWrapperElement() is None

    def test_missing_data_type_raises(self) -> None:
        jelement = _wrapper_element()
        del jelement["dataType"]
        with pytest.raises(TaxonomyException):
            _build_taxonomy(
                "test://typed-wrapper/no-data-type",
                _BASE_CONCEPTS,
                typed_domain_wrapper_elements={"vsme:TYP": jelement},
            )

    def test_missing_base_data_type_raises(self) -> None:
        jelement = _wrapper_element()
        del jelement["baseDataType"]
        with pytest.raises(TaxonomyException):
            _build_taxonomy(
                "test://typed-wrapper/no-base-data-type",
                _BASE_CONCEPTS,
                typed_domain_wrapper_elements={"vsme:TYP": jelement},
            )

    def test_labels_default_to_empty(self) -> None:
        taxonomy = _build_taxonomy(
            "test://typed-wrapper/no-labels",
            _BASE_CONCEPTS,
            typed_domain_wrapper_elements={"vsme:TYP": _wrapper_element()},
        )
        element = taxonomy.getConcept("vsme:TypedAxis").getTypedDomainWrapperElement()
        assert element is not None
        assert element.labels == {}


class TestTaxonomyAccessor:
    def test_get_typed_domain_wrapper_element_by_qname_string(self) -> None:
        taxonomy = _build_taxonomy(
            "test://typed-wrapper/accessor-by-string",
            _BASE_CONCEPTS,
            typed_domain_wrapper_elements={"vsme:TYP": _wrapper_element()},
        )
        element = taxonomy.getTypedDomainWrapperElement("vsme:TYP")
        assert str(element.qname) == "vsme:TYP"

    def test_get_typed_domain_wrapper_element_by_qname_object(self) -> None:
        taxonomy = _build_taxonomy(
            "test://typed-wrapper/accessor-by-qname",
            _BASE_CONCEPTS,
            typed_domain_wrapper_elements={"vsme:TYP": _wrapper_element()},
        )
        byString = taxonomy.getTypedDomainWrapperElement("vsme:TYP")
        assert taxonomy.getTypedDomainWrapperElement(byString.qname) is byString

    def test_unknown_qname_raises_key_error(self) -> None:
        taxonomy = _build_taxonomy(
            "test://typed-wrapper/accessor-unknown", _BASE_CONCEPTS
        )
        with pytest.raises(KeyError):
            taxonomy.getTypedDomainWrapperElement("vsme:NoSuchElement")


class TestEquality:
    def test_equal_by_qname(self) -> None:
        taxonomy = _build_taxonomy(
            "test://typed-wrapper/equality",
            _BASE_CONCEPTS,
            typed_domain_wrapper_elements={"vsme:TYP": _wrapper_element()},
        )
        element = taxonomy.getTypedDomainWrapperElement("vsme:TYP")
        other = TypedDomainWrapperElement(
            taxonomy._qnameMaker, "vsme:TYP", _wrapper_element()
        )
        assert element == other
        assert hash(element) == hash(other)

    def test_not_equal_to_other_types(self) -> None:
        taxonomy = _build_taxonomy(
            "test://typed-wrapper/not-equal",
            _BASE_CONCEPTS,
            typed_domain_wrapper_elements={"vsme:TYP": _wrapper_element()},
        )
        element = taxonomy.getTypedDomainWrapperElement("vsme:TYP")
        assert element != "vsme:TYP"
