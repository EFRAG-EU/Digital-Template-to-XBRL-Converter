"""Locale metadata for the converter UI."""

from __future__ import annotations

from mireport.localise import (
    EU_LOCALES,
    extract_base_languages,
    get_locale_list,
)
from mireport.taxonomy import getTaxonomy, listTaxonomies


def make_locale_json() -> list[dict[str, str]]:
    """Build the locale dropdown metadata used by the index page."""
    languages: set[str] = {
        lang for ep in listTaxonomies() for lang in getTaxonomy(ep).supportedLanguages
    }
    return get_locale_list(
        EU_LOCALES,
        supportedLanguages=extract_base_languages(languages),
    )
