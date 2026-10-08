"""A taxonomy's entry point is an (unordered) set of documents that together
form one DTS: passed around as a frozenset, keyed on it in the registry, and
serialised as a sorted "entryPointSet" list in the baked JSON (the legacy
single "entryPoint" is still accepted)."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest
from babel import Locale

import mireport.taxonomy as _taxonomy_module
from mireport.entrypoints import EntryPointSet, entryPointSetOf
from mireport.exceptions import TaxonomyException, UnknownTaxonomyException
from mireport.report.inlinereport import InlineReport
from mireport.taxonomy import getTaxonomy, listTaxonomies, loadTaxonomyJSON

CORE = "https://example.com/eps/core.xsd"
LABELS = "https://example.com/eps/labels.xml"
DOCS = "https://example.com/eps/docs.xml"


@pytest.fixture(autouse=True)
def _isolated_taxonomy_registry() -> Iterator[None]:
    before = set(_taxonomy_module._TAXONOMIES)
    yield
    for entryPoint in set(_taxonomy_module._TAXONOMIES) - before:
        del _taxonomy_module._TAXONOMIES[entryPoint]


def taxonomyJson(**entryPointKeys: Any) -> dict[str, Any]:
    return {
        **entryPointKeys,
        "namespaces": {"vsme": "https://example.com/vsme"},
        "concepts": {},
        "presentation": {},
        "dimensions": {},
    }


class TestEntryPointSetOf:
    def test_is_a_frozenset_of_the_documents(self) -> None:
        assert entryPointSetOf([CORE, LABELS, DOCS]) == frozenset({CORE, LABELS, DOCS})

    def test_duplicates_collapse(self) -> None:
        assert entryPointSetOf([LABELS, CORE, LABELS]) == frozenset({CORE, LABELS})

    def test_a_single_document_given_as_a_str(self) -> None:
        assert entryPointSetOf(CORE) == frozenset({CORE})

    @pytest.mark.parametrize(
        "documents",
        [[], [""], ["  "], [CORE, ""], [42], [CORE, None], [[CORE]]],
        ids=["empty", "blank", "whitespace", "blank-extra", "int", "none", "list"],
    )
    def test_unusable_documents_raise(self, documents: list[Any]) -> None:
        with pytest.raises(TaxonomyException):
            entryPointSetOf(documents)

    @pytest.mark.parametrize(
        "document",
        [
            "https://xbrl.efrag.org/taxonomy/vsme/2026-05-01/vsme-all.xsd",
            "file:///C:/taxonomies/vsme-all.xsd",
            "urn:example:taxonomy",
            "test://checker/entry",
        ],
    )
    def test_absolute_uris_are_accepted(self, document: str) -> None:
        assert entryPointSetOf(document) == frozenset({document})

    @pytest.mark.parametrize(
        "document",
        [
            "C:\\taxonomies\\vsme-all.xsd",
            "c:/taxonomies/vsme-all.xsd",
            "vsme-all.xsd",
            "/taxonomies/vsme-all.xsd",
            "https://example.com/a b.xsd",
            "https:",
            "http ://example.com/a.xsd",
            "1http://example.com/a.xsd",
        ],
        ids=[
            "windows-path",
            "drive-letter-uri-lookalike",
            "relative",
            "absolute-path",
            "whitespace",
            "scheme-only",
            "space-in-scheme",
            "scheme-starts-with-digit",
        ],
    )
    def test_non_uris_raise_naming_them(self, document: str) -> None:
        with pytest.raises(TaxonomyException, match="not an absolute URI"):
            entryPointSetOf([CORE, document])

    def test_is_an_entry_point_set(self) -> None:
        assert isinstance(entryPointSetOf([CORE]), EntryPointSet)

    def test_an_entry_point_set_passes_straight_through(self) -> None:
        entryPointSet = entryPointSetOf([CORE, LABELS])

        assert entryPointSetOf(entryPointSet) is entryPointSet

    def test_a_plain_frozenset_is_still_validated(self) -> None:
        with pytest.raises(TaxonomyException):
            entryPointSetOf(frozenset({CORE, ""}))

    def test_direct_construction_is_validated_too(self) -> None:
        with pytest.raises(TaxonomyException):
            EntryPointSet([CORE, ""])

    def test_equal_to_and_hashed_as_the_plain_frozenset(self) -> None:
        entryPointSet = entryPointSetOf([CORE, LABELS])
        plain = frozenset({CORE, LABELS})

        assert entryPointSet == plain
        assert {plain: "found"}[entryPointSet] == "found"


class TestLoading:
    def test_entry_point_set_is_loaded(self) -> None:
        taxonomy = loadTaxonomyJSON(taxonomyJson(entryPointSet=[LABELS, CORE, DOCS]))

        assert taxonomy.entryPointSet == frozenset({CORE, LABELS, DOCS})

    def test_found_by_its_set_in_any_order(self) -> None:
        taxonomy = loadTaxonomyJSON(taxonomyJson(entryPointSet=[CORE, LABELS]))

        assert getTaxonomy([LABELS, CORE]) is taxonomy
        assert getTaxonomy(frozenset({CORE, LABELS})) is taxonomy

    def test_one_document_of_a_larger_set_does_not_find_it(self) -> None:
        loadTaxonomyJSON(taxonomyJson(entryPointSet=[CORE, LABELS]))

        with pytest.raises(UnknownTaxonomyException):
            getTaxonomy(CORE)

    def test_a_single_document_taxonomy_is_found_by_its_str(self) -> None:
        taxonomy = loadTaxonomyJSON(taxonomyJson(entryPointSet=[CORE]))

        assert getTaxonomy(CORE) is taxonomy

    def test_listed_by_its_set(self) -> None:
        loadTaxonomyJSON(taxonomyJson(entryPointSet=[CORE, LABELS]))

        assert frozenset({CORE, LABELS}) in listTaxonomies()

    def test_the_same_set_in_another_order_is_already_loaded(self) -> None:
        loadTaxonomyJSON(taxonomyJson(entryPointSet=[CORE, LABELS]))

        with pytest.raises(TaxonomyException, match="Already loaded"):
            loadTaxonomyJSON(taxonomyJson(entryPointSet=[LABELS, CORE]))

    def test_legacy_single_entry_point_still_loads(self) -> None:
        taxonomy = loadTaxonomyJSON(taxonomyJson(entryPoint=CORE))

        assert taxonomy.entryPointSet == frozenset({CORE})
        assert getTaxonomy(CORE) is taxonomy

    @pytest.mark.parametrize(
        "entryPointKeys",
        [{}, {"entryPointSet": []}, {"entryPointSet": CORE}, {"entryPoint": ""}],
        ids=["neither", "empty-set", "set-not-a-list", "blank-legacy"],
    )
    def test_unusable_entry_point_raises(self, entryPointKeys: dict[str, Any]) -> None:
        with pytest.raises(TaxonomyException):
            loadTaxonomyJSON(taxonomyJson(**entryPointKeys))


def test_default_schema_refs_name_every_document_sorted() -> None:
    taxonomy = loadTaxonomyJSON(taxonomyJson(entryPointSet=[LABELS, CORE]))
    report = InlineReport(taxonomy, Locale("en"))

    assert report.getSchemaRefForAoix().splitlines() == [
        f'{{{{ schema-ref "{CORE}" }}}}',
        f'{{{{ schema-ref "{LABELS}" }}}}',
    ]
