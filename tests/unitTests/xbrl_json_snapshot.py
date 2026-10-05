"""A report's facts as an xBRL-JSON document, for the snapshot (control) files.

Each fact's value is the XBRL value (a percent shown as 28.41 with ix:scale -2 is written
0.2840909...), with its concept, entity, period, unit, taxonomy dimensions and decimals; footnotes
are note facts linked with ``links.footnote``. What the OIM has no place for goes in extension
properties, which xBRL-JSON allows on the report and on facts (QName names, prefix bound to a
namespace outside xbrl.org): ``mireport:scale`` on a fact, ``mireport:messageSeverities`` on the
report. Facts are in a stable order with ids ``f0001``... so a diff reads as facts.

This is a data-only writer for tests; a production xBRL-JSON writer would replace it.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from decimal import Decimal
from typing import Any

from mireport.report import InlineReport
from mireport.report.fact import Fact
from mireport.taxonomy import QName

XBRL_JSON_DOCUMENT_TYPE = "https://xbrl.org/2021/xbrl-json"
XBRL_NS = "https://xbrl.org/2021"
LEI_NS = "http://standards.iso.org/iso/17442"
EXTENSION_NS = "urn:mireport:xbrl-json-extension"


def _period(fact: Fact) -> str:
    """The OIM period: a fact's periods are already OIM's (a duration up to the start of the day
    after its last, an instant at the start of a day)."""
    return str(fact.period.oim)


def _value(fact: Fact) -> Any:
    concept, value = fact.concept, fact.value
    if fact.enumeration is not None:
        return " ".join(sorted(str(m.qname) for m in fact.enumeration))
    if concept.isBoolean:
        return value if isinstance(value, bool) else str(value).lower() == "true"
    if concept.isNumeric:
        return format(Decimal(str(value)).scaleb(fact.scale or 0), "f")
    return str(value)


def report_to_xbrl_json(
    report: InlineReport,
    entryPointSet: Iterable[str],
    messageSeverities: dict[str, int] | None = None,
) -> dict[str, Any]:
    namespaces: dict[str, str] = {}

    def q(qname: QName) -> str:
        namespaces[qname.prefix] = qname.namespace
        return str(qname)

    scheme = report.entityScheme or ""
    entity_prefix = "lei" if scheme == LEI_NS else "entity"
    namespaces[entity_prefix] = scheme
    entity = f"{entity_prefix}:{report.entityIdentifier}"

    def key(fact: Fact) -> str:
        return json.dumps([str(fact.concept.qname), _dimensions(fact), _value(fact)])

    def _dimensions(fact: Fact) -> dict[str, Any]:
        dims: dict[str, Any] = {
            "concept": str(fact.concept.qname),
            "entity": entity,
            "period": _period(fact),
        }
        if fact.unit is not None:
            dims["unit"] = fact.unit.toUnitString()
        for d in sorted(fact.explicit_values, key=lambda d: str(d.dimension.qname)):
            dims[str(d.dimension.qname)] = str(d.member.qname)
        for t in sorted(fact.typed_values, key=lambda t: str(t.dimension.qname)):
            dims[str(t.dimension.qname)] = t.value
        return dims

    facts: dict[str, Any] = {}
    notes: dict[int, str] = {}  # footnote id -> the id of its note fact
    ordered = sorted(report.facts, key=key)
    for n, fact in enumerate(ordered, start=1):
        dims = _dimensions(fact)
        q(fact.concept.qname)
        if fact.unit is not None:
            for measure in (*fact.unit.numerator, *fact.unit.denominator):
                q(measure)
        for d in fact.explicit_values:
            q(d.dimension.qname)
            q(d.member.qname)
        for t in fact.typed_values:
            q(t.dimension.qname)
        for m in fact.enumeration or ():
            q(m.qname)
        entry: dict[str, Any] = {"value": _value(fact), "dimensions": dims}
        if fact.decimals is not None:
            entry["decimals"] = fact.decimals
        if fact.scale is not None:
            entry["mireport:scale"] = fact.scale
        if fact.footnotes:
            ids = []
            for footnote in fact.footnotes:
                if footnote.id not in notes:
                    notes[footnote.id] = f"n{len(notes) + 1:03d}"
                ids.append(notes[footnote.id])
            entry["links"] = {"footnote": {"_": sorted(ids)}}
        facts[f"f{n:04d}"] = entry

    footnotes = {
        note_id: str(footnote.content)
        for footnote in {fn for f in report.facts for fn in f.footnotes}
        if (note_id := notes[footnote.id])
    }
    for note_id, content in sorted(footnotes.items()):
        facts[note_id] = {
            "value": content,
            "dimensions": {
                "concept": "xbrl:note",
                "noteId": note_id,
                "language": report.taxonomy.defaultLanguage or "en",
            },
        }
    namespaces["xbrl"] = XBRL_NS
    namespaces["mireport"] = EXTENSION_NS
    document: dict[str, Any] = {
        "documentInfo": {
            "documentType": XBRL_JSON_DOCUMENT_TYPE,
            "namespaces": dict(sorted(namespaces.items())),
            "taxonomy": sorted(entryPointSet),
        },
        "facts": facts,
    }
    if messageSeverities is not None:
        document["mireport:messageSeverities"] = dict(sorted(messageSeverities.items()))
    return document
