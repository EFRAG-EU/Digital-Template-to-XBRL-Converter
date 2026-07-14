"""Structured diagnostics for taxonomy extraction.

Diagnostic messages about a taxonomy (warnings, errors, curiosities) carry
their metadata — extended link role, concept QNames, remediation hints, and
other details — as structured fields rather than interpolated prose, with a
single :meth:`Diagnostic.format` rendering for humans. The same object is
used whether the diagnostic ends up in the Arelle log (:func:`logTo`) or in
an exception message (``ArelleModelInconsistency`` in ``support.py``).

The rendered text carries no level prefix: the level travels on the log
record itself (``callArelleForTaxonomyInfo`` sets a ``logFormat`` that
displays it).

This module deliberately imports nothing from the rest of
``mireport.arelle`` so any of it (including ``support.py``) can use it.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import ClassVar, Self

from arelle.Cntlr import Cntlr
from arelle.ModelValue import QName


def _formatValue(value: object) -> str:
    if isinstance(value, (list, tuple, set, frozenset)):
        return ", ".join(str(item) for item in value)
    return str(value)


@dataclass(frozen=True)
class Diagnostic:
    """A structured diagnostic about taxonomy content or shape."""

    text: str
    level: int = logging.INFO
    elr: str | None = None
    concepts: tuple[QName, ...] = ()
    hint: str | None = None
    details: Mapping[str, object] = field(default_factory=dict)

    @classmethod
    def _make(
        cls,
        text: str,
        level: int,
        *,
        elr: str | None = None,
        concepts: Iterable[QName] = (),
        hint: str | None = None,
        **details: object,
    ) -> Self:
        return cls(
            text=text,
            level=level,
            elr=elr,
            concepts=tuple(concepts),
            hint=hint,
            details=details,
        )

    @classmethod
    def info(
        cls,
        text: str,
        *,
        elr: str | None = None,
        concepts: Iterable[QName] = (),
        hint: str | None = None,
        **details: object,
    ) -> Self:
        return cls._make(
            text, logging.INFO, elr=elr, concepts=concepts, hint=hint, **details
        )

    @classmethod
    def warning(
        cls,
        text: str,
        *,
        elr: str | None = None,
        concepts: Iterable[QName] = (),
        hint: str | None = None,
        **details: object,
    ) -> Self:
        return cls._make(
            text, logging.WARNING, elr=elr, concepts=concepts, hint=hint, **details
        )

    @classmethod
    def error(
        cls,
        text: str,
        *,
        elr: str | None = None,
        concepts: Iterable[QName] = (),
        hint: str | None = None,
        **details: object,
    ) -> Self:
        return cls._make(
            text, logging.ERROR, elr=elr, concepts=concepts, hint=hint, **details
        )

    def format(self) -> str:
        """Render for humans: the sentence, then one indented line per
        populated field (elr, concept(s), details in insertion order, hint
        last)."""
        lines = [self.text]
        if self.elr is not None:
            lines.append(f"  elr: {self.elr}")
        if len(self.concepts) == 1:
            lines.append(f"  concept: {self.concepts[0]}")
        elif self.concepts:
            lines.append("  concepts:")
            lines.extend(f"    {qname}" for qname in self.concepts)
        for key, value in self.details.items():
            lines.append(f"  {key}: {_formatValue(value)}")
        if self.hint is not None:
            lines.append(f"  hint: {self.hint}")
        return "\n".join(lines)


def logTo(cntlr: Cntlr, diagnostic: Diagnostic) -> None:
    """Send a diagnostic to the Arelle log with its level."""
    cntlr.addToLog(diagnostic.format(), level=diagnostic.level)


class DiagnosticCollector:
    """Token-keyed hand-back channel for Diagnostic objects.

    The taxonomy-info plugin file is imported by Arelle as its own module,
    but this module is imported by name on both sides so `sys.modules`
    guarantees a single instance — the caller opens a collector, passes the
    token through pluginOptions, and the plugin adds to it in-process
    (arelle.api.Session runs in the caller's thread).
    """

    _registry: ClassVar[dict[str, list[Diagnostic]]] = {}

    @classmethod
    def open(cls) -> str:
        token = uuid.uuid4().hex
        cls._registry[token] = []
        return token

    @classmethod
    def exists(cls, token: str) -> bool:
        return token in cls._registry

    @classmethod
    def add(cls, token: str, diagnostic: Diagnostic) -> None:
        cls._registry[token].append(diagnostic)

    @classmethod
    def close(cls, token: str) -> list[Diagnostic]:
        """Remove the collector and return everything it gathered."""
        return cls._registry.pop(token)


class DiagnosticEmitter:
    """Where the plugin sends its diagnostics, chosen once at start-up:
    a DiagnosticCollector when the caller registered one (Session API path),
    otherwise the Arelle log (plain arelleCmdLine plugin usage)."""

    def __init__(self, cntlr: Cntlr, token: str | None) -> None:
        self._cntlr = cntlr
        self._token = (
            token if token is not None and DiagnosticCollector.exists(token) else None
        )

    def emit(self, diagnostic: Diagnostic) -> None:
        if self._token is not None:
            DiagnosticCollector.add(self._token, diagnostic)
        else:
            logTo(self._cntlr, diagnostic)
