"""Per-run state shared in-process between callArelleForTaxonomyInfo() and
the taxonomy-info plugin.

The plugin file is imported by Arelle as its own module, but this module is
imported by name on both sides so ``sys.modules`` guarantees a single
registry. The caller opens a run, passes only its token through
pluginOptions (``RuntimeOptionValue`` admits no lists or objects), and the
plugin looks the run up in-process -- arelle.api.Session runs in the
caller's thread. What the caller hands in and what the plugin hands back
travel on the same typed object rather than being flattened into options.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import ClassVar

from mireport.arelle.diagnostics import ArelleDiagnostic
from mireport.entrypoints import EntryPointSet


@dataclass
class TaxonomyInfoRun:
    """In: the documents that together form the entry point.
    Out: the diagnostics the plugin raised."""

    entryPointSet: EntryPointSet
    diagnostics: list[ArelleDiagnostic] = field(default_factory=list)


class TaxonomyInfoRunRegistry:
    _registry: ClassVar[dict[str, TaxonomyInfoRun]] = {}

    @classmethod
    def open(cls, *, entryPointSet: EntryPointSet) -> str:
        token = uuid.uuid4().hex
        cls._registry[token] = TaxonomyInfoRun(entryPointSet=entryPointSet)
        return token

    @classmethod
    def get(cls, token: str | None) -> TaxonomyInfoRun | None:
        """The run for token, or None when there is none -- as when the plugin
        is used from plain arelleCmdLine rather than via the Session API."""
        return cls._registry.get(token) if token is not None else None

    @classmethod
    def close(cls, token: str) -> TaxonomyInfoRun:
        """Remove the run and return it, with everything the plugin added."""
        return cls._registry.pop(token)
