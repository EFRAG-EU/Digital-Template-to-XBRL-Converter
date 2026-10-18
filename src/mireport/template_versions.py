"""Which Digital Template version is the latest one for a taxonomy.

A report's own template version is judged against the *supported* template
version, which is configured per taxonomy entry point (``templateVersions`` in
the disclosure config) as one of:

* a version number: that is the latest template for the taxonomy;
* ``"auto"``: whatever the installed migration tool migrates into;
* ``"none"``: the taxonomy has no migration, so only the converter's own
  version is known.

Entry points the config doesn't mention fall back to the converter's own
version, which is what the Digital Template was versioned with before
templates and taxonomies could differ.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import StrEnum
from functools import cache

from mireport.data.disclosures import getTemplateVersionSetting
from mireport.entrypoints import entryPointSetOf, isAbsoluteUri
from mireport.version import OUR_VERSION_HOLDER, VersionHolder

L = logging.getLogger(__name__)


class SupportedVersionSource(StrEnum):
    """Where the supported template version came from. The values read as the
    tail of a sentence, as they are shown to users."""

    TAXONOMY_CONFIG = "the taxonomy configuration"
    MIGRATION_TOOL = "the migration tool"
    CONVERTER = "this converter"


@dataclass(frozen=True, slots=True)
class SupportedTemplate:
    supported_template_version: VersionHolder
    supported_template_version_source: SupportedVersionSource
    # Whether migrating a report would produce this supported version.
    migration_enabled: bool


@cache
def migrationTargetTemplateVersion() -> VersionHolder | None:
    """The template version the installed migration tool migrates into, or
    None when there is no (usable) tool."""
    try:
        from migration_tool import migration_target

        raw = migration_target().target_template_version
    except ImportError:
        L.info("Migration tool (or its target API) not available.", exc_info=True)
        return None
    except (ValueError, OSError):
        L.exception("Migration tool could not report its target template version.")
        return None
    if (parsed := VersionHolder.parse_safe(raw)) is None:
        L.error("Migration tool reports an unusable target version: %r", raw)
        return None
    return parsed.strip_build_metadata


def resolveSupportedTemplate(entryPoint: str) -> SupportedTemplate:
    """The supported template for the taxonomy entry point a report names.
    ``entryPoint`` may be blank or junk: the report is being checked, not yet
    trusted."""
    converter_version = OUR_VERSION_HOLDER.strip_build_metadata
    setting: str | None = None
    if entryPoint.strip() and isAbsoluteUri(entryPoint):
        setting = getTemplateVersionSetting(entryPointSetOf(entryPoint))
    migration_target_template_version = migrationTargetTemplateVersion()

    match setting:
        case None | "none":
            version, source = converter_version, SupportedVersionSource.CONVERTER
        case "auto":
            if migration_target_template_version is None:
                version, source = converter_version, SupportedVersionSource.CONVERTER
            else:
                version = migration_target_template_version
                source = SupportedVersionSource.MIGRATION_TOOL
        case _:
            version = VersionHolder.parse(setting).strip_build_metadata
            source = SupportedVersionSource.TAXONOMY_CONFIG

    return SupportedTemplate(
        supported_template_version=version,
        supported_template_version_source=source,
        migration_enabled=(
            setting != "none" and migration_target_template_version == version
        ),
    )
