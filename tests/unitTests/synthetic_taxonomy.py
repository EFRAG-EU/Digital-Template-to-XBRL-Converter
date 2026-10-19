"""A small hand-built taxonomy, for tests of behaviour no baked taxonomy exhibits.

Layout and reader behaviour around hypercubes depends on how a taxonomy is shaped (a presentation
group with no hypercube, a cube defined only in the definition linkbase), and the baked taxonomies
are one fixed shape. A test that needs another builds it here, in the taxonomy JSON the tool reads,
and registers it with the ``register_taxonomy`` fixture for the length of the test.

Everything here is invented: one namespace (``NAMESPACE``, prefix ``PREFIX``) and entry points under
the ``test://`` scheme, so none of it can be mistaken for, or collide with, a real taxonomy.
"""

from __future__ import annotations

from typing import Any

NAMESPACE = "https://example.com/synthetic"
PREFIX = "syn"
LABEL_ROLE = "http://www.xbrl.org/2003/role/label"
GROUP_ROLE = f"{NAMESPACE}/role/Group"
GROUP_ENTRY_POINT = "test://synthetic/group"

ENTITY = "lei:529900T8BM49AURSDO55"
ENTITY_SCHEME = "http://standards.iso.org/iso/17442"
DURATION = "2025-01-01T00:00:00/2026-01-01T00:00:00"


def qname(localName: str) -> str:
    """The prefixed name of a concept in the synthetic taxonomy."""
    return f"{PREFIX}:{localName}"


def concept(
    label: str,
    *,
    numeric: bool = False,
    abstract: bool = False,
    dimension: bool = False,
    hypercube: bool = False,
    periodType: str = "duration",
    other: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """One concept, as it appears under ``concepts`` in the taxonomy JSON."""
    kind = "xbrli:decimalItemType" if numeric else "xbrli:stringItemType"
    entry: dict[str, Any] = {
        "labels": {"en": {LABEL_ROLE: label}},
        "dataType": kind,
        "baseDataType": kind,
        "periodType": periodType,
    }
    if numeric:
        entry["numeric"] = True
    if abstract or dimension or hypercube:
        entry["abstract"] = True
    if dimension:
        entry["dimension"] = True
    if hypercube:
        entry["hypercube"] = True
    if other:
        entry["other"] = other
    return entry


def cube(
    items: list[str], explicit: dict[str, list[str]], typed: list[str] | None = None
) -> dict[str, Any]:
    """One hypercube, as it appears under ``dimensions`` in the taxonomy JSON."""
    entry: dict[str, Any] = {
        "primaryItems": [[i, q] for i, q in enumerate(items)],
        "xbrldt:contextElement": "scenario",
        "xbrldt:closed": True,
        "explicitDimensions": explicit,
    }
    if typed:
        entry["typedDimensions"] = typed
    return entry


def taxonomy_json(
    entryPoint: str,
    *,
    concepts: dict[str, dict[str, Any]],
    presentation: dict[str, Any],
    dimensions: dict[str, Any] | None = None,
    xsElements: dict[str, Any] | None = None,
    namespaces: dict[str, str] | None = None,
) -> dict[str, Any]:
    """A whole taxonomy document for ``loadTaxonomyJSON``."""
    return {
        "entryPoint": entryPoint,
        "namespaces": {
            PREFIX: NAMESPACE,
            "xs": "http://www.w3.org/2001/XMLSchema",
            **(namespaces or {}),
        },
        "concepts": concepts,
        "xs_elements": xsElements or {},
        "presentation": presentation,
        "dimensions": {"_defaults": {}, **(dimensions or {})},
    }


def group_taxonomy_json(entryPoint: str = GROUP_ENTRY_POINT) -> dict[str, Any]:
    """Only primary items are presented: one group, with no hypercube, axis or member in it, though
    the definition linkbase has a cube by scope (explicit axis) and one by target (typed axis)."""
    return taxonomy_json(
        entryPoint,
        concepts={
            qname("Group"): concept("Group", abstract=True),
            qname("Emissions"): concept("Emissions", numeric=True),
            qname("Total"): concept("Total", numeric=True),
            qname("Intensity"): concept("Intensity", numeric=True),
            qname("Note"): concept("Note"),
            qname("Orphan"): concept("Orphan"),
            qname("ScopeAxis"): concept("Scope [Axis]", dimension=True),
            qname("ScopeA"): concept("Scope A", abstract=True),
            qname("ScopeB"): concept("Scope B", abstract=True),
            qname("TargetAxis"): concept(
                "Target [Axis]",
                dimension=True,
                other={"typedElement": qname("TargetType")},
            ),
            qname("ScopeCube"): concept("Scope cube", hypercube=True),
            qname("TargetCube"): concept("Target cube", hypercube=True),
        },
        xsElements={
            qname("TargetType"): {"dataType": "xs:string", "baseDataType": "xs:string"}
        },
        presentation={
            GROUP_ROLE: {
                "definition": "Group",
                "rows": [
                    [0, qname("Group")],
                    [1, qname("Emissions")],
                    [1, qname("Total")],
                    [1, qname("Intensity")],
                    [1, qname("Note")],
                ],
            }
        },
        dimensions={
            GROUP_ROLE: {
                qname("ScopeCube"): cube(
                    [qname("Emissions")],
                    {qname("ScopeAxis"): [qname("ScopeA"), qname("ScopeB")]},
                ),
                qname("TargetCube"): cube(
                    [qname("Intensity"), qname("Note")], {}, [qname("TargetAxis")]
                ),
            },
        },
    )


def fact(
    concept: str,
    value: str | int,
    *,
    period: str = DURATION,
    decimals: int | str | None = 0,
    dimensions: dict[str, str] | None = None,
) -> dict[str, Any]:
    """One fact of an xBRL-JSON document, with a prefixed ``concept`` and dimension names.
    Pass ``decimals=None`` for a non-numeric fact, which may not have them."""
    return {
        "value": value,
        **({} if decimals is None else {"decimals": decimals}),
        "dimensions": {
            "concept": concept,
            "entity": ENTITY,
            "period": period,
            **(dimensions or {}),
        },
    }


def document(entryPoint: str, facts: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """An xBRL-JSON document over the synthetic taxonomy with these facts."""
    return {
        "documentInfo": {
            "documentType": "https://xbrl.org/2021/xbrl-json",
            "namespaces": {PREFIX: NAMESPACE, "lei": ENTITY_SCHEME},
            "taxonomy": [entryPoint],
        },
        "facts": facts,
    }


def group_document() -> dict[str, Any]:
    """Facts for ``group_taxonomy_json``: two by scope, one plain, two by target, one note."""
    scope, target = qname("ScopeAxis"), qname("TargetAxis")
    return document(
        GROUP_ENTRY_POINT,
        {
            "f1": fact(qname("Emissions"), "10", dimensions={scope: qname("ScopeA")}),
            "f2": fact(qname("Emissions"), "20", dimensions={scope: qname("ScopeB")}),
            "f3": fact(qname("Total"), "30"),
            "f4": fact(qname("Intensity"), "5", dimensions={target: "T1"}),
            "f5": fact(qname("Intensity"), "7", dimensions={target: "T2"}),
            "f6": fact(
                qname("Note"), "note one", decimals=None, dimensions={target: "T1"}
            ),
        },
    )
