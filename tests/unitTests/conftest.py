"""Fixtures shared by the unit tests."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from typing import Any

import pytest

import mireport.taxonomy as _taxonomy_module
from mireport.taxonomy import Taxonomy, loadTaxonomyJSON


@pytest.fixture
def register_taxonomy() -> Iterator[Callable[[dict[str, Any]], Taxonomy]]:
    """Register taxonomy JSON (see ``synthetic_taxonomy``) for the length of the test.

    The registry is process-wide, so whatever a test registers is removed again afterwards and
    can neither leak into another test nor shadow a baked taxonomy.
    """
    before = set(_taxonomy_module._TAXONOMIES)
    yield loadTaxonomyJSON
    for entryPointSet in set(_taxonomy_module._TAXONOMIES) - before:
        del _taxonomy_module._TAXONOMIES[entryPointSet]
