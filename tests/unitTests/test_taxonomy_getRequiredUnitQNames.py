"""Unit tests for Concept.getRequiredUnitQNames(), built over hand-written
taxonomy JSON via loadTaxonomyJSON() -- this needs no Arelle DTS at all. See
test_taxonomy_references.py for the same approach.

Regression coverage for a real bug: the underlying resolution used to be
cached keyed on the Concept itself, but Concept.__eq__/__hash__ compare by
qname only -- so two Concepts from two different Taxonomy instances sharing a
qname collided in that cache. These tests build exactly that: two taxonomies
sharing one namespace and concept qname.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest

import mireport.taxonomy as _taxonomy_module
from mireport.taxonomy import Taxonomy, loadTaxonomyJSON

_NS = "https://example.com/shared-namespace"
_STANDARD_LABEL = "http://www.xbrl.org/2003/role/label"
_MEASUREMENT_GUIDANCE_LABEL = "http://www.xbrl.org/2003/role/measurementGuidance"


@pytest.fixture(autouse=True)
def _isolated_taxonomy_registry() -> Iterator[None]:
    """loadTaxonomyJSON() registers into the process-lifetime _TAXONOMIES
    registry with no way to unregister; remove whatever this test added so it
    leaves no trace for later tests."""
    before = set(_taxonomy_module._TAXONOMIES)
    yield
    for entryPoint in set(_taxonomy_module._TAXONOMIES) - before:
        del _taxonomy_module._TAXONOMIES[entryPoint]


def _concept(*, measurement_guidance: str) -> dict[str, Any]:
    return {
        "labels": {
            "en": {
                _STANDARD_LABEL: "Ratio",
                _MEASUREMENT_GUIDANCE_LABEL: measurement_guidance,
            }
        },
        "dataType": "xbrli:pureItemType",
        "baseDataType": "xbrli:pureItemType",
        "periodType": "duration",
        "numeric": True,
    }


def _build_taxonomy(entry_point: str, measurement_guidance: str) -> Taxonomy:
    bits = {
        "entryPoint": entry_point,
        # Same namespace, same local name, in every taxonomy this file
        # builds -- deliberately, to reproduce the qname collision.
        "namespaces": {"shared": _NS},
        "concepts": {
            "shared:Ratio": _concept(measurement_guidance=measurement_guidance)
        },
        "presentation": {},
        "dimensions": {},
    }
    return loadTaxonomyJSON(bits)


class TestCrossTaxonomyIsolation:
    def test_two_taxonomies_sharing_a_qname_resolve_independently(self) -> None:
        # xbrli:pureItemType's UTR candidates include both "pure" and "Rate"
        # (see TaxonomyChecker's own tests for the same data type), so each
        # taxonomy's concept can resolve to a different, real unit.
        taxonomyA = _build_taxonomy("test://units/shared-qname-a", "pure")
        taxonomyB = _build_taxonomy("test://units/shared-qname-b", "Rate")

        conceptA = taxonomyA.getConcept("shared:Ratio")
        conceptB = taxonomyB.getConcept("shared:Ratio")
        assert conceptA == conceptB  # same qname -- the crux of the bug

        [unitA] = conceptA.getRequiredUnitQNames()
        [unitB] = conceptB.getRequiredUnitQNames()
        assert str(unitA) == "xbrli:pure"
        assert str(unitB) == "utr:Rate"

        # Re-querying the first concept after resolving the second must not
        # have picked up the second taxonomy's cached answer.
        [unitAAgain] = conceptA.getRequiredUnitQNames()
        assert str(unitAAgain) == "xbrli:pure"
