"""Unit tests for the taxonomy-info Arelle plugin harness.

The extraction logic itself is tested in test_taxonomy_extraction.py; the
end-to-end plugin run is covered by
tests/integrationTests/test_taxonomy_info_regeneration.py.
"""

from __future__ import annotations

from mireport.arelle.taxonomy_info import TaxonomyInfoPluginData


class TestTaxonomyInfoPluginData:
    def test_instances_have_independent_taxonomy_dicts(self) -> None:
        first = TaxonomyInfoPluginData("first")
        second = TaxonomyInfoPluginData("second")
        first.Taxonomy["concepts"] = {"vsme:Thing": {}}
        assert second.Taxonomy == {}
        assert first.Taxonomy is not second.Taxonomy

    def test_instances_have_independent_utr_dicts(self) -> None:
        first = TaxonomyInfoPluginData("first")
        second = TaxonomyInfoPluginData("second")
        first.UTR["utr"] = [{"unitId": "kg"}]
        assert second.UTR == {}
        assert first.UTR is not second.UTR
