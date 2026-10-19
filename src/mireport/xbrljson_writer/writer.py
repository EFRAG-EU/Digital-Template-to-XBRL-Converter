"""Any Report as an xBRL-JSON document.

Each fact becomes one fact object, in the report's order (or a canonical one, see ``sortFacts``),
with ids ``f1``, ``f2``...: its concept, the report's entity, its period as OIM has it, its unit
(omitted for ``xbrli:pure``, as OIM requires of a dimensionless number), its taxonomy dimensions in
the order the fact has them, its canonical value (see mireport.xbrljson_writer._values) and its
decimals. A footnote becomes an ``xbrl:note`` fact (``n1``, ``n2``...) that the facts it annotates
link to. The namespaces are those of the QNames the document actually uses.

The document is the reader's own model (mireport.xbrljson_reader.document), so what is written is
what the reader reads.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING, Any

from mireport.exceptions import BrokenNamespacePrefixException, InlineReportException
from mireport.report.model import Unit
from mireport.xbrljson_reader.document import (
    FACT_FOOTNOTE_ARCROLE,
    STANDARD_LINK_ROLE,
    XbrlJsonDocument,
    XbrlJsonException,
    XbrlJsonFact,
)
from mireport.xbrljson_writer._values import canonicalValue
from mireport.xml import XBRLI_NS, NamespaceManager

if TYPE_CHECKING:
    from mireport.report.fact import Fact
    from mireport.report.report import Report
    from mireport.taxonomy import QName

CANONICAL_VALUES = {"xbrl:canonicalValues": True}


@dataclass(frozen=True)
class XbrlJsonExtensions:
    """What to write that OIM has no place for, as the extension properties xBRL-JSON allows on
    the document and on each fact: named by QName, with the prefix bound (in ``namespaces``) to a
    namespace outside xbrl.org. ``fact`` gives a fact's own properties."""

    namespaces: Mapping[str, str]
    document: Mapping[str, Any] = field(default_factory=dict)
    fact: Callable[[Fact], Mapping[str, Any]] | None = None


def _isPure(unit: Unit) -> bool:
    return (
        not unit.denominator
        and len(unit.numerator) == 1
        and unit.numerator[0].namespace == XBRLI_NS
        and unit.numerator[0].localName == "pure"
    )


class _Namespaces:
    """The document's prefixes: the taxonomy's bindings (which already include ``xbrl``, see
    getBootstrapQNameMaker) plus the entity's and any extension's, each prefix bound to one
    namespace. Only the prefixes the document uses are written."""

    def __init__(self, taxonomyBindings: Mapping[str, str]) -> None:
        self._manager = NamespaceManager.fromMapping(taxonomyBindings)
        self._used: set[str] = {"xbrl"}

    def bind(self, prefix: str, namespace: str) -> None:
        try:
            self._manager.add(prefix, namespace)
        except BrokenNamespacePrefixException as e:
            raise XbrlJsonException(str(e)) from e
        self._used.add(prefix)

    def name(self, qname: QName) -> str:
        """``qname`` as the document writes it, its prefix bound."""
        self.bind(qname.prefix, qname.namespace)
        return str(qname)

    def sorted(self) -> dict[str, str]:
        return {
            prefix: self._manager.getNamespaceForPrefix(prefix)
            for prefix in sorted(self._used)
        }


def toXbrlJson(
    report: Report,
    *,
    sortFacts: bool = False,
    extensions: XbrlJsonExtensions | None = None,
) -> XbrlJsonDocument:
    """``report`` as an xBRL-JSON document.

    With ``sortFacts`` the facts are ordered by what they say (concept, dimensions, value) rather
    than the order the report has them in, so two reports with the same facts give the same
    document whichever order they were built in: for comparing one with another.
    """
    namespaces = _Namespaces(report.taxonomy.namespacePrefixesMap)
    facts = report.facts
    entity = ""
    if facts:
        if report.entityScheme is None or report.entityIdentifier is None:
            raise InlineReportException("The report's entity is not set.")
        namespaces.bind(report.entityPrefix, report.entityScheme)
        entity = f"{report.entityPrefix}:{report.entityIdentifier}"

    def written(fact: Fact) -> XbrlJsonFact:
        """The fact, with no id or links yet."""
        unit = None
        if fact.unit is not None and not _isPure(fact.unit):
            for measure in (*fact.unit.numerator, *fact.unit.denominator):
                namespaces.name(measure)
            unit = fact.unit.toUnitString()
        dimensions: dict[str, str | None] = {}
        for explicit in fact.explicit_values:
            dimensions[namespaces.name(explicit.dimension.qname)] = namespaces.name(
                explicit.member.qname
            )
        for typed in fact.typed_values:
            dimensions[namespaces.name(typed.dimension.qname)] = typed.value
        for member in fact.enumeration or ():
            namespaces.name(member.qname)
        return XbrlJsonFact(
            id="",
            concept=namespaces.name(fact.concept.qname),
            value=canonicalValue(fact),
            entity=entity,
            period=str(fact.period.oim),
            unit=unit,
            dimensions=dimensions,
            # No decimals is what INF (every digit exact) is written as.
            decimals=None if fact.decimals == "INF" else fact.decimals,
            extensions=dict(extensions.fact(fact))
            if extensions is not None and extensions.fact is not None
            else {},
        )

    pairs = [(fact, written(fact)) for fact in facts]
    if sortFacts:
        pairs.sort(
            key=lambda pair: json.dumps(
                [pair[1].concept, pair[1].toDict()["dimensions"], pair[1].value]
            )
        )

    noteIds: dict[int, str] = {}  # footnote id -> its note fact's id
    jsonFacts: list[XbrlJsonFact] = []
    for n, (fact, jsonFact) in enumerate(pairs, start=1):
        links: dict[str, dict[str, tuple[str, ...]]] = {}
        if fact.footnotes:
            for footnote in fact.footnotes:
                noteIds.setdefault(footnote.id, f"n{len(noteIds) + 1}")
            links["footnote"] = {
                "_": tuple(sorted(noteIds[fn.id] for fn in fact.footnotes))
            }
        jsonFacts.append(replace(jsonFact, id=f"f{n}", links=links))
    footnotes = {fn.id: fn for fact in facts for fn in fact.footnotes}
    for footnoteId, noteId in noteIds.items():
        jsonFacts.append(
            XbrlJsonFact(
                id=noteId,
                concept="xbrl:note",
                value=str(footnotes[footnoteId].content),
                language=report.language,
                noteId=noteId,
            )
        )

    if extensions is not None:
        for prefix, namespace in extensions.namespaces.items():
            namespaces.bind(prefix, namespace)
    return XbrlJsonDocument(
        taxonomy=report.schemaRefs,
        namespaces=namespaces.sorted(),
        facts=tuple(jsonFacts),
        features=CANONICAL_VALUES,
        # Declared only when used: xBRL-JSON predefines no link type or group names.
        linkTypes={"footnote": FACT_FOOTNOTE_ARCROLE} if noteIds else {},
        linkGroups={"_": STANDARD_LINK_ROLE} if noteIds else {},
        extensions=dict(extensions.document) if extensions is not None else {},
    )
