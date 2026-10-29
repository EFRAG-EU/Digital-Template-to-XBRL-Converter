"""Disclosure config and layout strategy are found by entry-point set; each
URL the disclosure JSON names is a one-document set."""

from mireport.data.disclosures import (
    VSME_DEFAULTS,
    areVersionsOfOneTaxonomy,
    getDisclosureConfig,
)
from mireport.entrypoints import entryPointSetOf
from mireport.report.disclosure_layout import DisclosureLayoutStrategy
from mireport.taxonomy import listTaxonomies, loadBuiltInTaxonomyJSON

SUPPORTED = VSME_DEFAULTS["taxonomyEntryPoints"]["supportedEntryPoint"]
OLD = VSME_DEFAULTS["taxonomyEntryPoints"]["oldEntryPoints"][0]


def test_config_found_by_its_one_document_set() -> None:
    assert getDisclosureConfig(entryPointSetOf(SUPPORTED)) is VSME_DEFAULTS


def test_versions_of_one_taxonomy_are_those_one_config_names() -> None:
    supported, old = entryPointSetOf(SUPPORTED), entryPointSetOf(OLD)
    unknown = entryPointSetOf("https://example.com/2025-01-01/x.xsd")

    assert areVersionsOfOneTaxonomy(old, supported)
    assert not areVersionsOfOneTaxonomy(unknown, supported)
    assert not areVersionsOfOneTaxonomy(unknown, unknown)


def test_a_larger_set_containing_the_url_is_not_that_config() -> None:
    larger = entryPointSetOf([SUPPORTED, "https://e.com/x.xml"])

    assert getDisclosureConfig(larger) is None


def test_layout_override_matches_by_set() -> None:
    old = DisclosureLayoutStrategy.for_entry_point(entryPointSetOf(OLD))
    current = DisclosureLayoutStrategy.for_entry_point(entryPointSetOf(SUPPORTED))

    assert type(old) is not type(current)


def test_every_entry_point_named_is_a_built_in_taxonomy() -> None:
    """The built-in taxonomy JSON's entry point is the right one; the config
    must name it exactly, or nothing it says about that taxonomy applies."""
    loadBuiltInTaxonomyJSON()
    entryPoints = VSME_DEFAULTS["taxonomyEntryPoints"]
    named = {
        entryPoints["supportedEntryPoint"],
        *entryPoints["oldEntryPoints"],
        *VSME_DEFAULTS["layoutStrategy"]["entryPoints"],
        *VSME_DEFAULTS["templateVersions"],
    }

    assert {entryPointSetOf(url) for url in named} <= set(listTaxonomies())
