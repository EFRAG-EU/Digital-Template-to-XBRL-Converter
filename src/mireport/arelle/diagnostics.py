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

import logging
from collections.abc import Callable
from dataclasses import dataclass, replace

from arelle.Cntlr import Cntlr
from arelle.ModelValue import QName

from mireport.diagnostics import AbstractDiagnostic


@dataclass(frozen=True)
class ArelleDiagnostic(AbstractDiagnostic[QName]):
    """A structured diagnostic over arelle.ModelValue.QName."""


def logTo(cntlr: Cntlr, diagnostic: ArelleDiagnostic) -> None:
    """Send a diagnostic to the Arelle log with its level."""
    cntlr.addToLog(diagnostic.format(), level=diagnostic.level)


class DiagnosticEmitter:
    """Where the plugin sends its diagnostics, chosen once at start-up:
    sink, when the caller gave one (the run's diagnostics on the Session API
    path, see mireport.arelle.taxonomy_info_run), otherwise the Arelle log
    (plain arelleCmdLine plugin usage).

    elrDefinition, when given, fills in the role definition of any emitted
    diagnostic that names an elr but not its definition."""

    def __init__(
        self,
        cntlr: Cntlr,
        sink: list[ArelleDiagnostic] | None,
        *,
        elrDefinition: Callable[[str], str | None] | None = None,
    ) -> None:
        self._cntlr = cntlr
        self._sink = sink
        self._elrDefinition = elrDefinition
        self._hasErrors = False

    @property
    def hasErrors(self) -> bool:
        """Whether any error-level diagnostic has been emitted."""
        return self._hasErrors

    def emit(self, diagnostic: ArelleDiagnostic) -> None:
        if (
            self._elrDefinition is not None
            and diagnostic.elr is not None
            and diagnostic.elrDefinition is None
        ):
            diagnostic = replace(
                diagnostic, elrDefinition=self._elrDefinition(diagnostic.elr)
            )
        if diagnostic.level >= logging.ERROR:
            self._hasErrors = True
        if self._sink is not None:
            self._sink.append(diagnostic)
        else:
            logTo(self._cntlr, diagnostic)
