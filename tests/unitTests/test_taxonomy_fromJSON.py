"""Unit tests for Taxonomy.fromJSON(): building a Taxonomy from JSON without
registering it in the process-lifetime _TAXONOMIES registry, and without
modifying the JSON it is given -- so the same entry point, or the very same
dict, can be built any number of times. Also checks that the registering
loaders (loadTaxonomyJSON(), loadBuiltInTaxonomyJSON()) behave as before.

Built over hand-written taxonomy JSON (see test_taxonomy_dimensions.py for
the same approach), plus one built-in taxonomy file for a real-world shape.
"""

from __future__ import annotations

import copy
import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

import mireport.taxonomy as _taxonomy_module
from mireport.data import taxonomies
from mireport.entrypoints import entryPointSetOf
from mireport.exceptions import TaxonomyException, UnknownTaxonomyException
from mireport.json import getJsonFiles, getObject
from mireport.taxonomy import (
    Taxonomy,
    getTaxonomy,
    listTaxonomies,
    loadBuiltInTaxonomyJSON,
    loadTaxonomyJSON,
)

_NS = "https://example.com/vsme"
_STANDARD_LABEL = "http://www.xbrl.org/2003/role/label"
_ROLE = "https://example.com/role/table"


@pytest.fixture(autouse=True)
def _isolated_taxonomy_registry() -> Iterator[None]:
    """loadTaxonomyJSON() registers into the process-lifetime _TAXONOMIES
    registry with no way to unregister; remove whatever this test added so it
    leaves no trace for later tests (see test_taxonomy_dimensions.py)."""
    before = set(_taxonomy_module._TAXONOMIES)
    yield
    for entryPoint in set(_taxonomy_module._TAXONOMIES) - before:
        del _taxonomy_module._TAXONOMIES[entryPoint]


def _concept(**flags: Any) -> dict[str, Any]:
    jconcept: dict[str, Any] = {
        "labels": {"en": {_STANDARD_LABEL: "Label"}},
        "dataType": "xbrli:stringItemType",
        "baseDataType": "xbrli:stringItemType",
        "periodType": "duration",
    }
    jconcept.update(flags)
    return jconcept


def _bits(entry_point: str) -> dict[str, Any]:
    """Taxonomy JSON exercising every section Taxonomy.__init__ used to
    modify in place: dimension defaults and a cube with explicit and typed
    dimensions (plus presentation and references, which it only ever read)."""
    return {
        "entryPointSet": [entry_point],
        "namespaces": {
            "vsme": _NS,
            "xs": "http://www.w3.org/2001/XMLSchema",
            "ref": "http://www.xbrl.org/2006/ref",
        },
        "concepts": {
            "vsme:Table": _concept(abstract=True, hypercube=True),
            "vsme:Axis": _concept(abstract=True, dimension=True),
            "vsme:Default": _concept(),
            "vsme:Member": _concept(),
            "vsme:TypedAxis": _concept(
                abstract=True, dimension=True, other={"typedElement": "vsme:TYP"}
            ),
            "vsme:Item": _concept(),
        },
        "xs_elements": {
            "vsme:TYP": {"dataType": "xs:string", "baseDataType": "xs:string"}
        },
        "presentation": {
            _ROLE: {
                "definition": "Table",
                "rows": [[0, "vsme:Table"], [1, "vsme:Axis"], [1, "vsme:Item"]],
            }
        },
        "dimensions": {
            "_defaults": {"vsme:Axis": "vsme:Default"},
            _ROLE: {
                "vsme:Table": {
                    "primaryItems": [[0, "vsme:Item"]],
                    "xbrldt:contextElement": "scenario",
                    "xbrldt:closed": True,
                    "explicitDimensions": {
                        "vsme:Axis": ["vsme:Default", "vsme:Member"]
                    },
                    "typedDimensions": ["vsme:TypedAxis"],
                }
            },
        },
        "references": [
            {
                "role": "http://www.xbrl.org/2003/role/reference",
                "parts": [["ref:Name", "ISO"]],
                "concepts": ["vsme:Item"],
            }
        ],
    }


def _model(taxonomy: Taxonomy) -> tuple[Any, ...]:
    """What a taxonomy was built into, comparable across Taxonomy objects
    (Concepts compare by qname, so this does not depend on object identity)."""
    axis = taxonomy.getConcept("vsme:Axis")
    return (
        taxonomy.entryPointSet,
        taxonomy.concepts,
        taxonomy.getDimensionDefault(axis),
        tuple(
            (
                d.roleUri,
                d.hypercube,
                d.type,
                d.closed,
                d.contextElement,
                d.primaryItems,
                d.explicitDimensions,
                d.typedDimensions,
                d.modelled,
            )
            for d in taxonomy.hypercubeDeclarations
        ),
        tuple((g.roleUri, g.relationships) for g in taxonomy.presentation),
        taxonomy.references,
    )


class TestFromJSONDoesNotRegister:
    def test_result_is_not_registered(self) -> None:
        before = listTaxonomies()
        taxonomy = Taxonomy.fromJSON(_bits("test://fromJSON/unregistered"))
        assert taxonomy.entryPointSet == entryPointSetOf("test://fromJSON/unregistered")
        assert listTaxonomies() == before
        with pytest.raises(UnknownTaxonomyException):
            getTaxonomy("test://fromJSON/unregistered")

    def test_same_entry_point_twice(self) -> None:
        first = Taxonomy.fromJSON(_bits("test://fromJSON/twice"))
        second = Taxonomy.fromJSON(_bits("test://fromJSON/twice"))
        assert first is not second
        assert _model(first) == _model(second)
        # Independent objects all the way down, not shared Concepts.
        assert first.getConcept("vsme:Item") is not second.getConcept("vsme:Item")
        assert (
            first.getConcept("vsme:Item").references[0]
            is not (second.getConcept("vsme:Item").references[0])
        )

    def test_entry_point_already_registered(self) -> None:
        registered = loadTaxonomyJSON(_bits("test://fromJSON/registered"))
        unregistered = Taxonomy.fromJSON(_bits("test://fromJSON/registered"))
        assert unregistered is not registered
        assert _model(unregistered) == _model(registered)
        assert getTaxonomy("test://fromJSON/registered") is registered

    def test_from_path(self, tmp_path: Path) -> None:
        path = tmp_path / "taxonomy.json"
        path.write_text(json.dumps(_bits("test://fromJSON/path")), encoding="utf-8")
        fromPath = Taxonomy.fromJSON(path)
        fromDict = Taxonomy.fromJSON(_bits("test://fromJSON/path"))
        assert _model(fromPath) == _model(fromDict)
        assert entryPointSetOf("test://fromJSON/path") not in listTaxonomies()


class TestSourceIsNotModified:
    def test_same_dict_twice(self) -> None:
        bits = _bits("test://fromJSON/same-dict")
        pristine = copy.deepcopy(bits)
        first = Taxonomy.fromJSON(bits)
        assert bits == pristine
        second = Taxonomy.fromJSON(bits)
        assert bits == pristine
        assert _model(first) == _model(second)
        # The defaults and the cube's contents, specifically, survived: these
        # are what used to be popped out of the dict by the first build.
        axis = second.getConcept("vsme:Axis")
        assert second.getDimensionDefault(axis) == second.getConcept("vsme:Default")
        [declaration] = second.hypercubeDeclarations
        assert declaration.closed is True
        assert declaration.primaryItems == {second.getConcept("vsme:Item")}
        assert declaration.typedDimensions == {second.getConcept("vsme:TypedAxis")}

    def test_registered_load_does_not_modify_source(self) -> None:
        bits = _bits("test://fromJSON/registered-same-dict")
        pristine = copy.deepcopy(bits)
        registered = loadTaxonomyJSON(bits)
        assert bits == pristine
        assert _model(Taxonomy.fromJSON(bits)) == _model(registered)


class TestRegisteredLoadingUnchanged:
    def test_loadTaxonomyJSON_registers(self) -> None:
        taxonomy = loadTaxonomyJSON(_bits("test://fromJSON/load"))
        assert getTaxonomy("test://fromJSON/load") is taxonomy
        assert entryPointSetOf("test://fromJSON/load") in listTaxonomies()

    def test_loadTaxonomyJSON_still_rejects_duplicate(self) -> None:
        loadTaxonomyJSON(_bits("test://fromJSON/duplicate"))
        with pytest.raises(TaxonomyException, match="Already loaded taxonomy"):
            loadTaxonomyJSON(_bits("test://fromJSON/duplicate"))

    def test_built_in_taxonomy(self) -> None:
        path = next(
            f for f in getJsonFiles(taxonomies) if f.name == "vsme-2026-05-01.json"
        )
        bits = getObject(path)
        # Legacy JSON records a single "entryPoint" in place of the set.
        entryPoint = entryPointSetOf(bits.get("entryPointSet") or [bits["entryPoint"]])
        if entryPoint not in listTaxonomies():
            loadBuiltInTaxonomyJSON()
        registered = getTaxonomy(entryPoint)
        before = listTaxonomies()

        unregistered = Taxonomy.fromJSON(bits)

        assert unregistered is not registered
        assert listTaxonomies() == before
        assert getTaxonomy(entryPoint) is registered
        assert unregistered.concepts == registered.concepts
        assert registered.defaultedDimensions  # a real-world check, not vacuous
        assert unregistered.defaultedDimensions == registered.defaultedDimensions
        assert len(unregistered.hypercubeDeclarations) == len(
            registered.hypercubeDeclarations
        )
        assert len(unregistered.presentation) == len(registered.presentation)
