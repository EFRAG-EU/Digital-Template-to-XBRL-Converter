"""Structured diagnostics about a taxonomy.

Diagnostic messages about a taxonomy (warnings, errors, curiosities) carry
their metadata — extended link role, concept QNames, remediation hints, and
other details — as structured fields rather than interpolated prose, with a
single :meth:`AbstractDiagnostic.format` rendering for humans.

:class:`AbstractDiagnostic` is parameterised on the QName type in use so it
can be shared between :mod:`mireport.taxonomy` (this module's
:class:`Diagnostic`, over :class:`mireport.xml.QName`) and
:mod:`mireport.arelle.diagnostics` (``ArelleDiagnostic``, over
``arelle.ModelValue.QName``) without either side importing the other's QName
type. This module imports nothing from ``mireport.arelle`` so it stays usable
without Arelle installed.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Generic, Self, TypeVar

from mireport.xml import QName

QNameT = TypeVar("QNameT")


def _formatValue(value: object) -> str:
    if isinstance(value, (list, tuple, set, frozenset)):
        return ", ".join(str(item) for item in value)
    return str(value)


@dataclass(frozen=True)
class AbstractDiagnostic(Generic[QNameT]):
    """A structured diagnostic about taxonomy content or shape."""

    text: str
    level: int = logging.INFO
    elr: str | None = None
    concepts: tuple[QNameT, ...] = ()
    hint: str | None = None
    details: Mapping[str, object] = field(default_factory=dict)

    @classmethod
    def _make(
        cls,
        text: str,
        level: int,
        *,
        elr: str | None = None,
        concepts: Iterable[QNameT] = (),
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
        concepts: Iterable[QNameT] = (),
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
        concepts: Iterable[QNameT] = (),
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
        concepts: Iterable[QNameT] = (),
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


@dataclass(frozen=True)
class Diagnostic(AbstractDiagnostic[QName]):
    """A structured diagnostic over mireport.xml.QName."""
