"""An xBRL-JSON document as data: parsed and shape-checked, before any taxonomy is involved.

``XbrlJsonDocument.fromDict``/``fromFile`` read one; ``toDict``/``toFile`` write one, so
anything that produces xBRL-JSON builds an ``XbrlJsonDocument`` and the reader and writers agree
on one shape. Values stay as the document writes them (prefixed names, OIM period and unit
strings, value text); making sense of them against a taxonomy is the processor's job.

A fact whose shape is wrong is not fatal: it is kept in ``invalidFacts`` with the reason, and the
rest of the document is still read. Only a document that is not xBRL-JSON at all raises.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Any

XBRL_JSON_DOCUMENT_TYPE = "https://xbrl.org/2021/xbrl-json"
# The core dimensions of an OIM fact; every other key under "dimensions" is a taxonomy dimension.
CORE_DIMENSIONS = ("concept", "entity", "period", "unit", "language", "noteId")
_FACT_PROPERTIES = frozenset({"value", "decimals", "dimensions", "links"})
_DOCUMENT_INFO_PROPERTIES = frozenset(
    {"documentType", "namespaces", "taxonomy", "features", "linkTypes", "linkGroups"}
)
_DOCUMENT_PROPERTIES = frozenset({"documentInfo", "facts"})

# Error codes from the xBRL-JSON and OIM specifications, carried on problems so a message says
# which rule a document broke.
INVALID_JSON_STRUCTURE = "xbrlje:invalidJSONStructure"
INVALID_PERIOD = "oime:invalidPeriodRepresentation"
INVALID_UNIT = "oime:invalidUnitStringRepresentation"
INVALID_DIMENSION_VALUE = "oime:invalidDimensionValue"
INVALID_LANGUAGE = "oime:invalidLanguage"
UNSUPPORTED = "mireport:unsupported"

# A footnote link: the fact-footnote arcrole, in the standard link role. xBRL-JSON predefines
# no link type names, so a document using them declares them in linkTypes/linkGroups.
FACT_FOOTNOTE_ARCROLE = "http://www.xbrl.org/2003/arcrole/fact-footnote"
STANDARD_LINK_ROLE = "http://www.xbrl.org/2003/role/link"


class XbrlJsonException(Exception):
    """The document cannot be turned into a report at all."""


class FactProblem(Exception):
    """Why a fact was not read: an error code and a sentence."""

    def __init__(self, code: str, reason: str) -> None:
        super().__init__(code, reason)
        self.code = code
        self.reason = reason

    def __str__(self) -> str:
        return f"{self.reason} [{self.code}]"


@dataclass(frozen=True, slots=True)
class InvalidFact:
    """A fact the document holds but whose shape is wrong, kept so it can be reported."""

    id: str
    problem: FactProblem


@dataclass(frozen=True, slots=True)
class XbrlJsonFact:
    """One fact object. ``dimensions`` holds only the taxonomy-defined dimensions (an explicit
    member's prefixed name, a typed dimension's value, or None for a nil typed value);
    ``extensions`` holds any properties outside the specification, by their prefixed name."""

    id: str
    concept: str
    value: str | None
    entity: str | None = None
    period: str | None = None
    unit: str | None = None
    language: str | None = None
    noteId: str | None = None
    dimensions: Mapping[str, str | None] = field(default_factory=dict)
    decimals: int | None = None
    links: Mapping[str, Mapping[str, tuple[str, ...]]] = field(default_factory=dict)
    extensions: Mapping[str, Any] = field(default_factory=dict)

    @property
    def isNil(self) -> bool:
        return self.value is None

    @property
    def isNote(self) -> bool:
        return self.concept == "xbrl:note"

    @classmethod
    def fromDict(cls, factId: str, data: Any) -> XbrlJsonFact:
        """Read one fact object; raises FactProblem if its shape is wrong."""
        if not isinstance(data, Mapping):
            raise FactProblem(INVALID_JSON_STRUCTURE, "A fact must be a JSON object.")
        dimensions = data.get("dimensions")
        if not isinstance(dimensions, Mapping):
            raise FactProblem(
                INVALID_JSON_STRUCTURE, "A fact needs a dimensions object."
            )
        concept = dimensions.get("concept")
        if not isinstance(concept, str) or not concept:
            raise FactProblem(INVALID_JSON_STRUCTURE, "A fact needs a concept.")
        core: dict[str, str | None] = {}
        for name in CORE_DIMENSIONS[1:]:
            if (text := dimensions.get(name)) is not None and not isinstance(text, str):
                raise FactProblem(
                    INVALID_JSON_STRUCTURE, f"The {name} dimension must be a string."
                )
            core[name] = text
        taxonomyDimensions: dict[str, str | None] = {}
        for name, member in dimensions.items():
            if name in CORE_DIMENSIONS:
                continue
            if member is not None and not isinstance(member, str):
                raise FactProblem(
                    INVALID_JSON_STRUCTURE,
                    f"Dimension {name} must have a string value, not {member!r}.",
                )
            taxonomyDimensions[name] = member
        return cls(
            id=factId,
            concept=concept,
            value=_valueText(data.get("value")),
            entity=core["entity"],
            period=core["period"],
            unit=core["unit"],
            language=core["language"],
            noteId=core["noteId"],
            dimensions=taxonomyDimensions,
            decimals=_decimals(data.get("decimals")),
            links=_links(data.get("links")),
            extensions={k: v for k, v in data.items() if k not in _FACT_PROPERTIES},
        )

    def toDict(self) -> dict[str, Any]:
        dimensions: dict[str, Any] = {"concept": self.concept}
        for name in CORE_DIMENSIONS[1:]:
            if (text := getattr(self, name)) is not None:
                dimensions[name] = text
        dimensions.update(self.dimensions)
        # Dimensions first: the order the coverage reports pinned elsewhere were written in.
        fact: dict[str, Any] = {"dimensions": dimensions, "value": self.value}
        if self.decimals is not None:
            fact["decimals"] = self.decimals
        if self.links:
            fact["links"] = {
                linkType: {group: list(targets) for group, targets in groups.items()}
                for linkType, groups in self.links.items()
            }
        fact.update(self.extensions)
        return fact


def _valueText(value: Any) -> str | None:
    """A fact's value as its text. xBRL-JSON writes values as strings, but numbers and booleans
    written as JSON are common enough to take as the text they stand for."""
    match value:
        case None | str():
            return value
        case bool():
            return "true" if value else "false"
        case int() | Decimal():
            return str(value)
        case float():
            return repr(value)
        case _:
            raise FactProblem(
                INVALID_JSON_STRUCTURE, f"A fact value must be a string, not {value!r}."
            )


def _decimals(decimals: Any) -> int | None:
    """None when absent, which xBRL-JSON reads as infinitely precise."""
    if decimals is None:
        return None
    if isinstance(decimals, str) and decimals.upper() == "INF":
        return None
    if isinstance(decimals, bool) or not isinstance(decimals, int):
        raise FactProblem(
            INVALID_JSON_STRUCTURE, f"decimals must be an integer, not {decimals!r}."
        )
    return decimals


def _links(links: Any) -> dict[str, dict[str, tuple[str, ...]]]:
    if links is None:
        return {}
    shapeProblem = FactProblem(
        INVALID_JSON_STRUCTURE,
        "links must map link types to link groups to arrays of fact ids.",
    )
    if not isinstance(links, Mapping):
        raise shapeProblem
    parsed: dict[str, dict[str, tuple[str, ...]]] = {}
    for linkType, groups in links.items():
        if not isinstance(groups, Mapping):
            raise shapeProblem
        parsed[linkType] = {}
        for group, targets in groups.items():
            if not isinstance(targets, list) or not all(
                isinstance(t, str) for t in targets
            ):
                raise shapeProblem
            parsed[linkType][group] = tuple(targets)
    return parsed


def _refuseConstant(name: str) -> Any:
    raise XbrlJsonException(f"{name} is not a JSON number.")


@dataclass(frozen=True, slots=True)
class XbrlJsonDocument:
    taxonomy: tuple[str, ...]
    namespaces: Mapping[str, str]
    facts: tuple[XbrlJsonFact, ...] = ()
    features: Mapping[str, Any] = field(default_factory=dict)
    linkTypes: Mapping[str, str] = field(default_factory=dict)
    linkGroups: Mapping[str, str] = field(default_factory=dict)
    # Properties outside the specification, on documentInfo and on the document itself.
    documentInfoExtensions: Mapping[str, Any] = field(default_factory=dict)
    extensions: Mapping[str, Any] = field(default_factory=dict)
    invalidFacts: tuple[InvalidFact, ...] = ()

    @classmethod
    def fromDict(cls, data: Any) -> XbrlJsonDocument:
        if not isinstance(data, Mapping):
            raise XbrlJsonException("An xBRL-JSON document must be a JSON object.")
        info = data.get("documentInfo")
        if not isinstance(info, Mapping):
            raise XbrlJsonException("The document has no documentInfo object.")
        if (documentType := info.get("documentType")) != XBRL_JSON_DOCUMENT_TYPE:
            raise XbrlJsonException(
                f"documentType must be {XBRL_JSON_DOCUMENT_TYPE}, not {documentType!r}."
            )
        taxonomy = info.get("taxonomy", [])
        if not isinstance(taxonomy, list) or not all(
            isinstance(d, str) for d in taxonomy
        ):
            raise XbrlJsonException(
                f"documentInfo.taxonomy must be an array of entry point documents, not {taxonomy!r}."
            )
        namespaces = _stringMap(info, "namespaces")
        facts = data.get("facts", {})
        if not isinstance(facts, Mapping):
            raise XbrlJsonException("facts must be a JSON object keyed by fact id.")
        good: list[XbrlJsonFact] = []
        bad: list[InvalidFact] = []
        for factId, fact in facts.items():
            try:
                good.append(XbrlJsonFact.fromDict(str(factId), fact))
            except FactProblem as problem:
                bad.append(InvalidFact(str(factId), problem))
        features = info.get("features", {})
        if not isinstance(features, Mapping):
            raise XbrlJsonException("documentInfo.features must be a JSON object.")
        return cls(
            taxonomy=tuple(taxonomy),
            namespaces=namespaces,
            facts=tuple(good),
            features=dict(features),
            linkTypes=_stringMap(info, "linkTypes"),
            linkGroups=_stringMap(info, "linkGroups"),
            documentInfoExtensions={
                k: v for k, v in info.items() if k not in _DOCUMENT_INFO_PROPERTIES
            },
            extensions={k: v for k, v in data.items() if k not in _DOCUMENT_PROPERTIES},
            invalidFacts=tuple(bad),
        )

    @classmethod
    def fromFile(cls, path: Path | str) -> XbrlJsonDocument:
        """Read a document; JSON numbers are read exactly (as Decimal), and NaN and Infinity,
        which are not JSON, are refused."""
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f, parse_float=Decimal, parse_constant=_refuseConstant)
        except json.JSONDecodeError as e:
            raise XbrlJsonException(f"{path} is not JSON: {e}") from e
        return cls.fromDict(data)

    def toDict(self) -> dict[str, Any]:
        info: dict[str, Any] = {"documentType": XBRL_JSON_DOCUMENT_TYPE}
        if self.features:
            info["features"] = dict(self.features)
        info["namespaces"] = dict(self.namespaces)
        if self.linkTypes:
            info["linkTypes"] = dict(self.linkTypes)
        if self.linkGroups:
            info["linkGroups"] = dict(self.linkGroups)
        info["taxonomy"] = list(self.taxonomy)
        info.update(self.documentInfoExtensions)
        document: dict[str, Any] = {"documentInfo": info}
        document.update(self.extensions)
        document["facts"] = {fact.id: fact.toDict() for fact in self.facts}
        return document

    def toJson(self) -> str:
        """The document as JSON text: indented, LF line ends, ending in a newline."""
        return json.dumps(self.toDict(), indent=2, ensure_ascii=False) + "\n"

    def toFile(self, path: Path | str) -> None:
        """Write the document as UTF-8 JSON (see ``toJson``)."""
        Path(path).write_text(self.toJson(), encoding="utf-8", newline="\n")

    def resolve(self, prefixed: str) -> tuple[str, str]:
        """The (namespace, local name) a prefixed name stands for in this document."""
        prefix, _, local = prefixed.partition(":")
        if not local or (namespace := self.namespaces.get(prefix)) is None:
            raise FactProblem(
                INVALID_JSON_STRUCTURE,
                f"{prefixed!r} is not a name with a declared prefix.",
            )
        return namespace, local


def _stringMap(info: Mapping[str, Any], name: str) -> dict[str, str]:
    value = info.get(name, {})
    if not isinstance(value, Mapping) or not all(
        isinstance(k, str) and isinstance(v, str) for k, v in value.items()
    ):
        raise XbrlJsonException(f"documentInfo.{name} must map names to URIs.")
    return dict(value)
