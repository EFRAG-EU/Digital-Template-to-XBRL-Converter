from __future__ import annotations

import logging

from mireport.entrypoints import EntryPointSet, entryPointSetOf
from mireport.json import getJsonFiles, getObject

L = logging.getLogger(__name__)


def _loadConfigs() -> tuple[
    dict[str, dict],
    dict[EntryPointSet, dict],
    dict[EntryPointSet, str],
    dict[EntryPointSet, str],
]:
    """Configs by file name and by entry-point set, the per-entry-point
    layout strategy overrides and the per-entry-point template version
    settings; each URL a config names is a one-document set. Parsed once,
    here, so lookups never re-normalise config URLs."""
    byFilename: dict[str, dict] = {}
    byEntryPoint: dict[EntryPointSet, dict] = {}
    layoutOverrides: dict[EntryPointSet, str] = {}
    templateVersionSettings: dict[EntryPointSet, str] = {}
    for resource in getJsonFiles(__name__):
        config = byFilename[resource.name] = getObject(resource)
        for url, strategyName in (
            config.get("layoutStrategy", {}).get("entryPoints", {}).items()
        ):
            layoutOverrides[entryPointSetOf(url)] = strategyName
        for url, setting in config.get("templateVersions", {}).items():
            templateVersionSettings[entryPointSetOf(url)] = setting
        entryPoints = config.get("taxonomyEntryPoints", {})
        for ep in (
            entryPoints.get("supportedEntryPoint"),
            *entryPoints.get("oldEntryPoints", []),
        ):
            if not ep:
                continue
            if (entryPointSet := entryPointSetOf(ep)) in byEntryPoint:
                L.warning(
                    "Entry point %s already registered; ignoring duplicate in %s",
                    ep,
                    resource.name,
                )
            else:
                byEntryPoint[entryPointSet] = config
    return byFilename, byEntryPoint, layoutOverrides, templateVersionSettings


(
    _CONFIG_BY_FILENAME,
    _CONFIG_BY_ENTRY_POINT,
    _LAYOUT_OVERRIDES,
    _TEMPLATE_VERSION_SETTINGS,
) = _loadConfigs()


def getDisclosureConfig(entryPointSet: EntryPointSet) -> dict | None:
    """The config for exactly this entry-point set, if any."""
    return _CONFIG_BY_ENTRY_POINT.get(entryPointSet)


def areVersionsOfOneTaxonomy(a: EntryPointSet, b: EntryPointSet) -> bool:
    """Whether two entry-point sets are versions of one taxonomy: one disclosure config names
    them both (its supported entry point and its old ones)."""
    config = _CONFIG_BY_ENTRY_POINT.get(a)
    return config is not None and config is _CONFIG_BY_ENTRY_POINT.get(b)


def getTemplateVersionSetting(entryPointSet: EntryPointSet) -> str | None:
    """How the latest Digital Template version for this entry-point set is
    determined: a version number, ``"auto"`` (ask the migration tool) or
    ``"none"`` (no migration); None when the config says nothing."""
    return _TEMPLATE_VERSION_SETTINGS.get(entryPointSet)


def getLayoutStrategyName(entryPointSet: EntryPointSet) -> str | None:
    """The layout strategy named for this entry-point set: its override, else
    its config's default; None when no config covers it."""
    if (config := _CONFIG_BY_ENTRY_POINT.get(entryPointSet)) is None:
        return None
    return _LAYOUT_OVERRIDES.get(
        entryPointSet, config.get("layoutStrategy", {}).get("default")
    )


# Back-compat: existing callers (e.g. processor.py) import this by name
VSME_DEFAULTS: dict = _CONFIG_BY_FILENAME["vsme.json"]
