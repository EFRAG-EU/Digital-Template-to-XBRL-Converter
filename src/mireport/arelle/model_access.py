"""Typed access layer over the Arelle DTS model.

Arelle's model is honest about its types: `rel.toModelObject` is
`ModelObject | None`, `concept.qname` is `QName | None`,
`relSet.rootConcepts` is `list[ModelObject]`, and so on. Our taxonomy
extraction code relies on much stronger guarantees (concept-to-concept arcs,
concepts that always have QNames, ...). This module is where those guarantees
are checked, exactly once, so that everything downstream can work with clean
types instead of scattering asserts and isinstance checks at every call site.

The layering rule:

- This module answers "did Arelle give us the *shape* we expect?" (type
  narrowing, None checks, cardinality). Violations raise
  :class:`ArelleModelInconsistency`.
- Callers (e.g. ``taxonomy_info.py``) answer "does the taxonomy *content*
  make sense?" (duplicate dimension defaults, empty presentations, ...) and
  handle their own logging policy.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from typing import NamedTuple, Self

from arelle import XbrlConst
from arelle.ModelDtsObject import (
    ModelConcept,
    ModelRelationship,
    ModelResource,
    ModelRoleType,
)
from arelle.ModelValue import QName
from arelle.ModelXbrl import ModelXbrl

from mireport.arelle.support import ArelleModelInconsistency


def _requireFullyQualified(qname: QName, context: str) -> QName:
    """All QNames we extract must have a prefix and a namespace, otherwise
    they cannot be canonicalised (see ArelleQNameCanonicaliser)."""
    if qname.namespaceURI is None:
        raise ArelleModelInconsistency(
            f"QName {qname!r} {context} has no namespace defined. Check that "
            'the taxonomy schemas have elementFormDefault="qualified" set '
            "(without it, locally declared elements end up in no namespace)."
        )
    if qname.prefix is None:
        raise ArelleModelInconsistency(
            f"QName {qname!r} {context} has no namespace prefix defined "
            f"(namespace: {qname.namespaceURI!r})"
        )
    return qname


def qnameOf(concept: ModelConcept) -> QName:
    """Return the concept's QName, which our extraction requires to exist and
    be fully qualified (prefix and namespace)."""
    if (qname := concept.qname) is None:
        raise ArelleModelInconsistency(f"Concept {concept!r} has no QName")
    return _requireFullyQualified(qname, f"of concept {concept!r}")


def _asConcept(obj: object, context: str) -> ModelConcept:
    if not isinstance(obj, ModelConcept):
        raise ArelleModelInconsistency(
            f"Expected a ModelConcept {context} but got {obj!r}"
        )
    return obj


@dataclass(frozen=True)
class ConceptRelationship:
    """A validated concept-to-concept arc. target/targetQName are never None."""

    target: ModelConcept
    targetQName: QName
    consecutiveLinkrole: str
    isUsable: bool
    preferredLabel: str | None
    contextElement: str | None
    isClosed: bool

    @classmethod
    def fromArelle(cls, rel: ModelRelationship) -> Self:
        target = _asConcept(
            rel.toModelObject,
            f"as target of {rel.arcrole} relationship in {rel.linkrole}",
        )
        if (consecutiveLinkrole := rel.consecutiveLinkrole) is None:
            raise ArelleModelInconsistency(
                f"Relationship to {qnameOf(target)} in {rel.linkrole} has no linkrole"
            )
        return cls(
            target=target,
            targetQName=qnameOf(target),
            consecutiveLinkrole=consecutiveLinkrole,
            isUsable=rel.isUsable,
            preferredLabel=rel.preferredLabel,
            contextElement=rel.contextElement,
            isClosed=rel.isClosed,
        )


class ResourceRelationship(NamedTuple):
    """A validated concept/roleType-to-resource arc (labels, references)."""

    resource: ModelResource
    role: str | None
    order: float


class ConceptRelationshipSet:
    """Typed wrapper around an Arelle relationship set whose arcs are
    concept-to-concept (presentation, definition arcroles)."""

    def __init__(
        self,
        modelXbrl: ModelXbrl,
        arcroles: str | tuple[str, ...],
        linkrole: str | None = None,
    ) -> None:
        self._modelXbrl = modelXbrl
        self._arcroles = arcroles
        self._relSet = modelXbrl.relationshipSet(arcroles, linkrole)

    @property
    def linkrole(self) -> str:
        linkrole = self._relSet.linkrole
        if not isinstance(linkrole, str):
            raise ArelleModelInconsistency(
                f"Relationship set for {self._arcroles} has no single linkrole "
                f"(got {linkrole!r})"
            )
        return linkrole

    def rootConcepts(self) -> list[ModelConcept]:
        return [
            _asConcept(root, f"as root of {self.linkrole}")
            for root in self._relSet.rootConcepts
        ]

    def relationshipsFrom(self, concept: ModelConcept) -> list[ConceptRelationship]:
        return [
            ConceptRelationship.fromArelle(rel)
            for rel in self._relSet.fromModelObject(concept)
        ]

    def hasRelationshipsFrom(self, concept: ModelConcept) -> bool:
        return bool(self._relSet.fromModelObject(concept))

    def hasRelationshipsTo(self, concept: ModelConcept) -> bool:
        return bool(self._relSet.toModelObject(concept))

    def consecutiveSet(self, rel: ConceptRelationship) -> ConceptRelationshipSet:
        """The relationship set to continue tree-walking from `rel`'s target:
        this set, or a new one if the arc has a different consecutive
        linkrole (xbrldt:targetRole)."""
        if rel.consecutiveLinkrole == self.linkrole:
            return self
        return ConceptRelationshipSet(
            self._modelXbrl, self._arcroles, rel.consecutiveLinkrole
        )


class ValidatedModel:
    """Facade over ModelXbrl: everything returned is narrowed or it raises."""

    def __init__(self, modelXbrl: ModelXbrl) -> None:
        self._modelXbrl = modelXbrl

    @property
    def conceptCount(self) -> int:
        return len(self._modelXbrl.qnameConcepts)

    def conceptRelationshipSet(
        self,
        arcroles: str | tuple[str, ...],
        linkrole: str | None = None,
    ) -> ConceptRelationshipSet:
        return ConceptRelationshipSet(self._modelXbrl, arcroles, linkrole)

    def resourceRelationshipsFrom(
        self,
        source: ModelConcept | ModelRoleType,
        arcrole: str,
    ) -> Iterator[ResourceRelationship]:
        """Yield validated arcs from `source` to resources (labels, references)."""
        for rel in self._modelXbrl.relationshipSet(arcrole).fromModelObject(source):
            resource = rel.toModelObject
            if not isinstance(resource, ModelResource):
                raise ArelleModelInconsistency(
                    f"Expected a ModelResource as target of {arcrole} relationship "
                    f"from {source!r} but got {resource!r}"
                )
            yield ResourceRelationship(
                resource=resource,
                role=resource.role,
                order=rel.order,
            )

    def linkrolesFor(self, *arcroles: str) -> list[str]:
        """Extended link roles that have a base set for any of `arcroles`.
        Deduplicated, in base-set insertion order."""
        wanted = set(arcroles)
        seen: dict[str, None] = {}
        for arcroleUri, linkrole, linkqname, arcqname in self._modelXbrl.baseSets:
            if linkqname is None or arcqname is None or linkrole is None:
                continue
            if arcroleUri in wanted:
                seen.setdefault(linkrole)
        return list(seen)

    def itemConcepts(self) -> Iterator[tuple[QName, ModelConcept]]:
        """Yield (qname, concept) for item concepts, skipping the xbrli/xbrldt
        infrastructure items (xbrli:item, xbrldt:hypercubeItem, ...)."""
        for concept in self._modelXbrl.qnameConcepts.values():
            if not concept.isItem:
                continue
            qname = qnameOf(concept)
            if qname.namespaceURI in (XbrlConst.xbrli, XbrlConst.xbrldt):
                continue
            yield qname, concept

    def concept(self, qname: QName) -> ModelConcept:
        try:
            return self._modelXbrl.qnameConcepts[qname]
        except KeyError:
            raise ArelleModelInconsistency(
                f"No concept found for QName {qname}"
            ) from None

    def typeQNamesOf(self, concept: ModelConcept) -> tuple[QName, QName]:
        """(type QName, base xbrli type QName) for an item concept.

        N.B. concept.type.qname is used rather than concept.typeQname as it
        gets the namespace prefix right, i.e. something defined in
        modelXbrl.prefixedNamespaces. concept.typeQname works almost the same
        but prefers a prefix from ?the defining schema? which can be one that
        is not defined in modelXbrl.prefixedNamespaces, making it impossible
        to find the namespace.
        """
        if (conceptType := concept.type) is None or conceptType.qname is None:
            raise ArelleModelInconsistency(
                f"Concept {qnameOf(concept)} has no named type"
            )
        if (baseQName := concept.baseXbrliTypeQname) is None:
            raise ArelleModelInconsistency(
                f"Concept {qnameOf(concept)} has no base xbrli type"
            )
        return (
            _requireFullyQualified(conceptType.qname, f"of type of {qnameOf(concept)}"),
            _requireFullyQualified(baseQName, f"of base type of {qnameOf(concept)}"),
        )

    def typedDomainQNameOf(self, concept: ModelConcept) -> QName:
        """QName of the typed domain element of a typed dimension concept."""
        element = concept.typedDomainElement
        if element is None or element.qname is None:
            raise ArelleModelInconsistency(
                f"Typed dimension {qnameOf(concept)} has no typed domain element"
            )
        return element.qname

    def roleType(self, roleUri: str) -> ModelRoleType:
        matching = self._modelXbrl.roleTypes.get(roleUri, [])
        if (num := len(matching)) != 1:
            raise ArelleModelInconsistency(
                f"Wrong number {num} of role type objects found for {roleUri=} (expected 1)"
            )
        return matching[0]
