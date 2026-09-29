"""Taxonomy entry points as sets of documents.

An entry point is the set of documents that together form one DTS; XBRL
gives the set no order, so it is an :class:`EntryPointSet` (a validated
frozenset) here -- the registry key for a taxonomy, and what disclosure
config and layout strategies are looked up by. Where an order is needed (the
baked JSON, Arelle's one entrypointFile plus imports, schema refs, display)
it is sorted order.

Values from outside (JSON, command lines, workbooks, config) go through
:func:`entryPointSetOf` once; anything typed EntryPointSet is already valid,
so internal code passes it on as-is.

Deliberately dependency-free so the taxonomy, disclosure config and Arelle
layers can all use it without import cycles.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from typing import Self

from mireport.exceptions import TaxonomyException

# RFC 3986 scheme ":" then something, no whitespace anywhere. The scheme is
# at least two characters so a Windows drive ("C:\...") is not one.
_ABSOLUTE_URI = re.compile(r"[A-Za-z][A-Za-z0-9+.\-]+:\S+")


def isAbsoluteUri(text: str) -> bool:
    """Whether text looks like an absolute URI.

    Hand-rolled because the stdlib doesn't validate -- urllib.parse accepts
    anything, and reads "C:\\x.xsd" as scheme "c" -- and a full RFC 3986
    validator would be a dependency for no gain: Arelle and taxonomy-package
    remapping reject genuinely broken URLs. This only has to keep paths,
    relative references and junk out of registry keys, baked JSON and
    schema-ref hrefs, where they would only mean something on one machine.
    """
    return _ABSOLUTE_URI.fullmatch(text) is not None


class EntryPointSet(frozenset[str]):
    """A non-empty set of entry-point documents, each an absolute URI. Validated on
    construction, so holding one means it is usable; equal to, and hashed
    as, the plain frozenset of the same documents."""

    __slots__ = ()

    def __new__(cls, documents: str | Iterable[str]) -> Self:
        candidates: list[object] = (
            [documents] if isinstance(documents, str) else list(documents)
        )
        if not candidates:
            raise TaxonomyException("An entry point needs at least one document.")
        # Sorted out before frozenset construction, which cannot take an
        # unhashable one.
        valid: list[str] = []
        bad: list[object] = []
        for document in candidates:
            if isinstance(document, str) and document.strip():
                valid.append(document)
            else:
                bad.append(document)
        if bad:
            raise TaxonomyException(f"Unusable entry point document(s): {bad!r}")
        if notUris := [d for d in valid if not isAbsoluteUri(d)]:
            raise TaxonomyException(
                f"Entry point document(s) not an absolute URI: {notUris!r}"
            )
        return super().__new__(cls, valid)

    def __repr__(self) -> str:
        return f"EntryPointSet({sorted(self)!r})"


def entryPointSetOf(documents: str | Iterable[str]) -> EntryPointSet:
    """The entry-point set for documents (a single str is a one-document
    set). An EntryPointSet is returned as-is: it is already valid."""
    if isinstance(documents, EntryPointSet):
        return documents
    return EntryPointSet(documents)


def describeEntryPointSet(entryPointSet: frozenset[str]) -> str:
    """One line for messages: the documents in sorted order."""
    return " + ".join(sorted(entryPointSet))
