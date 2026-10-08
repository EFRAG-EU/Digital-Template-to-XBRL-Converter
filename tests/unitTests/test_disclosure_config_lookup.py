"""Disclosure config and layout strategy are found by entry-point set; each
URL the disclosure JSON names is a one-document set."""

from mireport.data.disclosures import VSME_DEFAULTS, getDisclosureConfig
from mireport.entrypoints import entryPointSetOf
from mireport.report.disclosure_layout import DisclosureLayoutStrategy

SUPPORTED = VSME_DEFAULTS["taxonomyEntryPoints"]["supportedEntryPoint"]
OLD = VSME_DEFAULTS["taxonomyEntryPoints"]["oldEntryPoints"][0]


def test_config_found_by_its_one_document_set() -> None:
    assert getDisclosureConfig(entryPointSetOf(SUPPORTED)) is VSME_DEFAULTS


def test_a_larger_set_containing_the_url_is_not_that_config() -> None:
    larger = entryPointSetOf([SUPPORTED, "https://e.com/x.xml"])

    assert getDisclosureConfig(larger) is None


def test_layout_override_matches_by_set() -> None:
    old = DisclosureLayoutStrategy.for_entry_point(entryPointSetOf(OLD))
    current = DisclosureLayoutStrategy.for_entry_point(entryPointSetOf(SUPPORTED))

    assert type(old) is not type(current)
