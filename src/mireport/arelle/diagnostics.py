"""Structured diagnostics for taxonomy extraction.

``ArelleDiagnostic`` is the Arelle-facing concrete type of
:class:`mireport.diagnostics.AbstractDiagnostic`, parameterised on
``arelle.ModelValue.QName``. The same object is used whether the diagnostic
ends up in the Arelle log (:func:`logTo`) or in an exception message
(``ArelleModelInconsistency`` in ``support.py``).

The rendered text carries no level prefix: the level travels on the log
record itself (``callArelleForTaxonomyInfo`` sets a ``logFormat`` that
displays it).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import ClassVar

from arelle.Cntlr import Cntlr
from arelle.ModelValue import QName

from mireport.diagnostics import AbstractDiagnostic


@dataclass(frozen=True)
class ArelleDiagnostic(AbstractDiagnostic[QName]):
    """A structured diagnostic over arelle.ModelValue.QName."""


def logTo(cntlr: Cntlr, diagnostic: ArelleDiagnostic) -> None:
    """Send a diagnostic to the Arelle log with its level."""
    cntlr.addToLog(diagnostic.format(), level=diagnostic.level)


class DiagnosticCollector:
    """Token-keyed hand-back channel for ArelleDiagnostic objects.

    The taxonomy-info plugin file is imported by Arelle as its own module,
    but this module is imported by name on both sides so `sys.modules`
    guarantees a single instance — the caller opens a collector, passes the
    token through pluginOptions, and the plugin adds to it in-process
    (arelle.api.Session runs in the caller's thread).
    """

    _registry: ClassVar[dict[str, list[ArelleDiagnostic]]] = {}

    @classmethod
    def open(cls) -> str:
        token = uuid.uuid4().hex
        cls._registry[token] = []
        return token

    @classmethod
    def exists(cls, token: str) -> bool:
        return token in cls._registry

    @classmethod
    def add(cls, token: str, diagnostic: ArelleDiagnostic) -> None:
        cls._registry[token].append(diagnostic)

    @classmethod
    def close(cls, token: str) -> list[ArelleDiagnostic]:
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

    def emit(self, diagnostic: ArelleDiagnostic) -> None:
        if self._token is not None:
            DiagnosticCollector.add(self._token, diagnostic)
        else:
            logTo(self._cntlr, diagnostic)
