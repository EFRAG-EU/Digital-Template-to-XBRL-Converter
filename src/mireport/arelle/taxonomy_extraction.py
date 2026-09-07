"""Taxonomy and UTR extraction over a loaded Arelle DTS.

This module holds the extraction logic used by the ``taxonomy_info.py``
Arelle plugin. It is a normal importable module (unlike the plugin file,
which Arelle loads by file path as its own module) so the extractors can be
unit tested and reused without the plugin machinery. The extractors know
nothing about Arelle plugin data: :meth:`TaxonomyInfoExtractor.extract` and
:meth:`UTRInfoExtractor.extract` simply return the extracted data and the
plugin decides where to put it.
"""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from collections.abc import Collection, Iterable, Iterator, Mapping
from pathlib import Path
from typing import TYPE_CHECKING, NamedTuple, TypeVar

if TYPE_CHECKING:
    from typing import Any

from arelle import XbrlConst
from arelle.Cntlr import Cntlr
from arelle.ModelDtsObject import ModelConcept, ModelRoleType
from arelle.ModelValue import QName
from arelle.ModelXbrl import ModelXbrl
from arelle.RuntimeOptions import RuntimeOptions
from arelle.ValidateUtr import UtrEntry

from mireport.arelle.diagnostics import ArelleDiagnostic, DiagnosticEmitter
from mireport.arelle.model_access import (
    ConceptRelationship,
    ConceptRelationshipSet,
    ValidatedModel,
    qnameOf,
)
from mireport.arelle.support import (
    ArelleModelInconsistency,
    ArelleObjectJSONEncoder,
    ArelleQNameCanonicaliser,
    ArelleRelatedException,
)

T = TypeVar("T")

# The UtrEntry attributes worth serialising (the UTR schema's primary key is
# status + unitId).
_UTR_INTERESTING_KEYS = (
    "unitId",
    "unitName",
    "nsUnit",
    "itemType",
    "nsItemType",
    "numeratorItemType",
    "nsNumeratorItemType",
    "definition",
    "denominatorItemType",
    "nsDenominatorItemType",
    "symbol",
    "status",
)


def unique_list(i: Iterable[T]) -> list[T]:
    # N.B. This maintains insertion order where list(set()) does not.
    return list(dict.fromkeys(i))


def _overlappingPrimaryItems(
    primaryItemsByHypercube: Mapping[QName, Collection[QName]],
) -> frozenset[QName]:
    """The primary items declared in more than one hypercube of a base set.

    Mirrors Taxonomy._overlappingPrimaryItems in mireport/taxonomy.py, which acts
    on the same data once it has been loaded back out of the baked JSON (keyed by
    canonicalised QName strings there, rather than QNames here). Keep the two in
    step if the underlying rule ever changes.
    """
    hypercubesPerPrimaryItem = Counter(
        qname
        for primaryItems in primaryItemsByHypercube.values()
        for qname in set(primaryItems)
    )
    return frozenset(
        qname for qname, count in hypercubesPerPrimaryItem.items() if count > 1
    )


class DefinitionRow(NamedTuple):
    """One concept in a depth-first walk of a definition (domain-member) tree."""

    indent: int
    qname: QName
    isUsable: bool


class PresentationRow(NamedTuple):
    """One concept in a depth-first walk of a presentation tree."""

    indent: int
    qname: QName
    preferredLabel: str | None


def writeDataFile(
    cntlr: Cntlr,
    jsonPath: str | Path,
    dataName: str,
    data: dict,
) -> None:
    if not data:
        cntlr.addToLog(f"No {dataName} data to write")
        return

    # N.B. dumps() rather than dump(): only the one-shot dumps()/encode()
    # path can use the C-accelerated encoder, and from Python 3.14 that
    # extends to indented output. Streaming via dump() never gets it.
    payload = json.dumps(data, indent=2, sort_keys=True, cls=ArelleObjectJSONEncoder)
    Path(jsonPath).write_text(payload, encoding="UTF-8")
    cntlr.addToLog(f"{dataName} data written to {jsonPath}")


class UTRInfoExtractor:
    def __init__(self, cntlr: Cntlr, modelXbrl: ModelXbrl):
        self.cntlr: Cntlr = cntlr
        self.modelXbrl: ModelXbrl = modelXbrl
        if (
            utrModel := getattr(
                self.modelXbrl.modelManager.disclosureSystem, "utrItemTypeEntries", None
            )
        ) is not None:
            self.utrModel: dict[str, dict[str, UtrEntry]] = utrModel
        else:
            message = (
                "No UTR entries found. Perhaps you forgot to set `utrValidate=True`?"
            )
            self.cntlr.addToLog(message)
            raise ArelleRelatedException(message)

    def extract(self) -> dict[str, Any]:
        return {"utr": self.getUTRForJSON()}

    def getUTRForJSON(self) -> list[dict]:
        """Get the UTR entries from the modelXbrl."""
        # N.B. UTR schema primary key is the status and unitId
        jUTR: list[dict] = []
        utrEntries = [
            entry
            for entriesByUnitId in self.utrModel.values()
            for entry in entriesByUnitId.values()
        ]
        for entry in sorted(utrEntries, key=lambda e: e.unitId or ""):
            jEntry = {}
            for key in _UTR_INTERESTING_KEYS:
                if (value := getattr(entry, key)) is not None and value.strip() != "":
                    jEntry[key] = value
            jUTR.append(jEntry)
        return jUTR


class TaxonomyInfoExtractor:
    def __init__(self, cntlr: Cntlr, options: RuntimeOptions, modelXbrl: ModelXbrl):
        self.cntlr: Cntlr = cntlr
        self.options: RuntimeOptions = options
        self.modelXbrl: ModelXbrl = modelXbrl
        self.model: ValidatedModel = ValidatedModel(modelXbrl)
        self.diagnostics: DiagnosticEmitter = DiagnosticEmitter(
            cntlr, getattr(options, "diagnosticsToken", None)
        )
        self.taxonomyJson: dict[str, Any] = defaultdict(dict)
        self.qnameConverter: ArelleQNameCanonicaliser = (
            ArelleQNameCanonicaliser.bootstrap(modelXbrl)
        )
        self.dimensionDefaults: dict[ModelConcept, ModelConcept] = {}

    def extract(self) -> dict[str, Any]:
        """Extract the taxonomy information and return it as a JSON-ready
        dict with all QNames canonicalised to strings."""
        self.taxonomyJson["entryPoint"] = self.options.entrypointFile

        self.extractPresentation()
        # Extract dimension defaults before other dimension-related information
        # (used by other dimension-related extraction methods)
        self.extractDimensionDefaults()
        self.extractDimensionDefinitions()
        self.reportDomainMemberOnlyLinkroleRoots()
        self.extractConceptsAndMetadata()
        self.reportIsolatedConcepts()

        self.cntlr.addToLog("Processing namespaces and namespace prefixes")
        self.taxonomyJson = self.qnameConverter.convertRecursive(self.taxonomyJson)
        self.taxonomyJson["namespaces"] = self.qnameConverter.getNamespacePrefixMap()
        return self.taxonomyJson

    def walkDefinitionChildren(
        self,
        parent_concept: ModelConcept,
        relSet: ConceptRelationshipSet,
        indent: int,
    ) -> Iterator[DefinitionRow]:
        """Yield the descendants of `parent_concept` depth-first, following
        each arc's consecutive linkrole (xbrldt:targetRole)."""
        for rel in relSet.relationshipsFrom(parent_concept):
            yield DefinitionRow(indent, rel.targetQName, rel.isUsable)
            yield from self.walkDefinitionChildren(
                rel.target, relSet.consecutiveSet(rel), indent + 1
            )

    def walkPresentationChildren(
        self,
        parent_concept: ModelConcept,
        relSet: ConceptRelationshipSet,
        indent: int,
    ) -> Iterator[PresentationRow]:
        """Yield the descendants of `parent_concept` depth-first."""
        for rel in relSet.relationshipsFrom(parent_concept):
            yield PresentationRow(indent, rel.targetQName, rel.preferredLabel)
            yield from self.walkPresentationChildren(rel.target, relSet, indent + 1)

    def getPrimaryItems(
        self, elrUri: str, domainHeadConcept: ModelConcept
    ) -> list[tuple[int, QName]]:
        relSet = self.model.conceptRelationshipSet(XbrlConst.domainMember, elrUri)
        domainHeadQName = qnameOf(domainHeadConcept)

        # N.B. domainHeadConcept does not have to be a root concept

        if not relSet.hasRelationshipsFrom(domainHeadConcept):
            self.diagnostics.emit(
                ArelleDiagnostic.warning(
                    "Hypercube has no primary items beyond the domain head (no outgoing domain-member relationships)",
                    elr=elrUri,
                    concepts=(domainHeadQName,),
                ),
            )
            return [(0, domainHeadQName)]

        return [(0, domainHeadQName)] + [
            (row.indent, row.qname)
            for row in self.walkDefinitionChildren(domainHeadConcept, relSet, 1)
        ]

    def getDimensions(
        self, elrUri: str, hypercube: ModelConcept, hypercubeIsClosed: bool
    ) -> list[ConceptRelationship]:
        relSet = self.model.conceptRelationshipSet(XbrlConst.hypercubeDimension, elrUri)

        if not relSet.hasRelationshipsFrom(hypercube):
            # This hypercube has no dimensions of its own. Other hypercubes
            # sharing the same ELR may still have dimensions of their own, so
            # this is not by itself a model inconsistency.
            if hypercubeIsClosed:
                self.diagnostics.emit(
                    ArelleDiagnostic.warning(
                        "Closed hypercube has no dimensions (no outgoing hypercube-dimension relationships)",
                        elr=elrUri,
                        concepts=(qnameOf(hypercube),),
                    ),
                )
            return []

        if relSet.hasRelationshipsTo(hypercube):
            # It has outgoing relationships (we didn't return above) but is
            # also somebody else's target within the same hypercube-dimension
            # set, i.e. it isn't a root of that set. A hypercube must not
            # itself be used as a dimension.
            raise ArelleModelInconsistency(
                ArelleDiagnostic.error(
                    "Hypercube is also the target of a hypercube-dimension relationship",
                    elr=elrUri,
                    concepts=(qnameOf(hypercube),),
                )
            )
        return relSet.relationshipsFrom(hypercube)

    def getDomainMembersForExplicitDimension(
        self,
        explicitDimension: ModelConcept,
        elrUri: str,
    ) -> list[QName]:
        dimensionDomainRelSet = self.model.conceptRelationshipSet(
            XbrlConst.dimensionDomain, elrUri
        )

        dimensionDomainRoots = dimensionDomainRelSet.rootConcepts()
        if explicitDimension not in dimensionDomainRoots:
            raise ArelleModelInconsistency(
                ArelleDiagnostic.error(
                    "Dimension is not a root of the dimension-domain relationship set",
                    elr=elrUri,
                    concepts=(qnameOf(explicitDimension),),
                    roots=sorted(qnameOf(root) for root in dimensionDomainRoots),
                )
            )
        dimensionDomainRels = dimensionDomainRelSet.relationshipsFrom(explicitDimension)
        domainMemberTrees: list[tuple[ModelConcept, bool, ConceptRelationshipSet]] = [
            (
                rel.target,
                rel.isUsable,
                self.model.conceptRelationshipSet(
                    XbrlConst.domainMember, rel.consecutiveLinkrole
                ),
            )
            for rel in dimensionDomainRels
        ]

        hasDefaultedDomainMember = explicitDimension in self.dimensionDefaults

        if not domainMemberTrees:
            if hasDefaultedDomainMember:
                self.diagnostics.emit(
                    ArelleDiagnostic.warning(
                        "Dimension has a defaulted domain member but no domain relationships",
                        elr=elrUri,
                        concepts=(qnameOf(explicitDimension),),
                        defaultMember=qnameOf(
                            self.dimensionDefaults[explicitDimension]
                        ),
                    ),
                )
            else:
                self.diagnostics.emit(
                    ArelleDiagnostic.warning(
                        "Dimension has no domain relationships",
                        elr=elrUri,
                        concepts=(qnameOf(explicitDimension),),
                    ),
                )
            return []

        dimensionHasMultipleDimensionDomainRelationships = 1 < len(dimensionDomainRels)

        members: list[QName] = []
        for domainHeadConcept, usable, domainMemberRelSet in domainMemberTrees:
            self.verifyDomainMemberTree(
                explicitDimension,
                hasDefaultedDomainMember,
                dimensionHasMultipleDimensionDomainRelationships,
                domainHeadConcept,
                domainMemberRelSet,
            )
            if usable:
                members.append(qnameOf(domainHeadConcept))
            members.extend(
                row.qname
                for row in self.walkDefinitionChildren(
                    domainHeadConcept, domainMemberRelSet, 1
                )
                if row.isUsable
            )
        return unique_list(members)

    def verifyDomainMemberTree(
        self,
        explicitDimension: ModelConcept,
        hasDefaultedDomainMember: bool,
        dimensionHasMultipleDimensionDomainRelationships: bool,
        domainHeadConcept: ModelConcept,
        domainMemberRelSet: ConceptRelationshipSet,
    ) -> None:
        outgoing = domainMemberRelSet.hasRelationshipsFrom(domainHeadConcept)
        incoming = domainMemberRelSet.hasRelationshipsTo(domainHeadConcept)
        elrUri = domainMemberRelSet.linkrole

        if not outgoing:
            if (
                hasDefaultedDomainMember
                and domainHeadConcept == self.dimensionDefaults[explicitDimension]
            ):
                # Dimension pointing at domain head with no members that's
                # also the default is the standard pattern for a domain that
                # is expected to be extended by the reporting entity.
                pass
            elif dimensionHasMultipleDimensionDomainRelationships:
                # Multiple dimension-domain relationships instead of one
                # dimension-domain followed by domain-member(s) is not great
                # modelling but technically OK.
                pass
            else:
                self.diagnostics.emit(
                    ArelleDiagnostic.warning(
                        "Dimension has a domain head with no outgoing domain-member relationships",
                        elr=elrUri,
                        concepts=(qnameOf(explicitDimension),),
                        domainHead=qnameOf(domainHeadConcept),
                    ),
                )

        if incoming:
            self.diagnostics.emit(
                ArelleDiagnostic.warning(
                    "Dimension has a domain head with incoming domain-member relationships. How exciting!",
                    elr=elrUri,
                    concepts=(qnameOf(explicitDimension),),
                    domainHead=qnameOf(domainHeadConcept),
                ),
            )

    def getDomainMembersForEnumeration(
        self,
        elrUri: str,
        headUsable: bool,
        domainHeadConcept: ModelConcept,
        enumerationConcept: QName,
    ) -> list[QName]:
        domainHeadQName = qnameOf(domainHeadConcept)
        domainMemberRelSet = self.model.conceptRelationshipSet(
            XbrlConst.domainMember, elrUri
        )
        if elrUri not in self.model.linkrolesFor(XbrlConst.domainMember):
            self.diagnostics.emit(
                ArelleDiagnostic.warning(
                    "Extensible enumeration linkrole has no domain-member relationships",
                    elr=elrUri,
                    concepts=(enumerationConcept,),
                    domainHead=domainHeadQName,
                ),
            )
        else:
            if not domainMemberRelSet.hasRelationshipsFrom(domainHeadConcept):
                self.diagnostics.emit(
                    ArelleDiagnostic.warning(
                        "Extensible enumeration domain head has no outgoing domain-member relationships",
                        elr=elrUri,
                        concepts=(enumerationConcept,),
                        domainHead=domainHeadQName,
                    ),
                )
            if domainMemberRelSet.hasRelationshipsTo(domainHeadConcept):
                # Unlike the other conditions here, this doesn't stop the
                # domain from resolving -- walkDefinitionChildren() still
                # walks correctly downward from the declared head regardless
                # of what points to it. Just a curiosity, not a defect.
                self.diagnostics.emit(
                    ArelleDiagnostic.info(
                        "Extensible enumeration domain head is not a root of the domain-member relationship set",
                        elr=elrUri,
                        concepts=(enumerationConcept,),
                        domainHead=domainHeadQName,
                    ),
                )

        members: list[QName] = []
        if headUsable:
            members.append(domainHeadQName)
        members.extend(
            row.qname
            for row in self.walkDefinitionChildren(
                domainHeadConcept, domainMemberRelSet, 1
            )
            if row.isUsable
        )
        if not (members := unique_list(members)):
            self.diagnostics.emit(
                ArelleDiagnostic.warning(
                    "Extensible enumeration resolved no usable domain members",
                    elr=elrUri,
                    concepts=(enumerationConcept,),
                    domainHead=domainHeadQName,
                ),
            )
        return members

    def extractDimensionDefaults(self) -> None:
        self.cntlr.addToLog("Processing dimension defaults")
        elrsWithDefaults = self.model.linkrolesFor(XbrlConst.dimensionDefault)
        dimToElrMap: dict[ModelConcept, list[str]] = defaultdict(list)

        for elrUri in elrsWithDefaults:
            dimensionDefaultRelSet = self.model.conceptRelationshipSet(
                XbrlConst.dimensionDefault, elrUri
            )

            for d in dimensionDefaultRelSet.rootConcepts():
                dimToElrMap[d].append(elrUri)

                defaultRels = dimensionDefaultRelSet.relationshipsFrom(d)
                if len(defaultRels) != 1:
                    raise ArelleModelInconsistency(
                        ArelleDiagnostic.error(
                            "More than one default member for dimension",
                            elr=elrUri,
                            concepts=(qnameOf(d),),
                            members=[rel.targetQName for rel in defaultRels],
                        )
                    )
                m = defaultRels[0].target
                if (m0 := self.dimensionDefaults.get(d)) is not None:
                    otherElrs = dimToElrMap[d][:-1]
                    if m0 != m:
                        self.diagnostics.emit(
                            ArelleDiagnostic.warning(
                                "Inconsistent duplicate definition of dimension default",
                                elr=elrUri,
                                concepts=(qnameOf(d),),
                                member=qnameOf(m),
                                previousMember=qnameOf(m0),
                                otherElrs=otherElrs,
                            ),
                        )
                    else:
                        self.diagnostics.emit(
                            ArelleDiagnostic.info(
                                "Consistent duplicate definition of dimension default",
                                elr=elrUri,
                                concepts=(qnameOf(d),),
                                member=qnameOf(m),
                                otherElrs=otherElrs,
                            ),
                        )
                self.dimensionDefaults[d] = m
        
        if self.dimensionDefaults:
            self.taxonomyJson["dimensions"]["_defaults"] = {
                qnameOf(d): qnameOf(m) for d, m in self.dimensionDefaults.items()
            }
        else:
            self.diagnostics.emit(
                ArelleDiagnostic.info(
                    "No dimension defaults found"
                )
            )

    def addConceptMetadata(self, concept: ModelConcept, jconcept: dict) -> None:
        meta = {
            "abstract": concept.isAbstract,
            "dimension": concept.isDimensionItem,
            "hypercube": concept.isHypercubeItem,
            "nillable": concept.isNillable,
            "numeric": concept.isNumeric,
        }
        for json_key, concept_property in meta.items():
            if concept_property is True:
                jconcept[json_key] = concept_property

    def keepLongerLabel(
        self,
        existing: str | None,
        label: str,
        *,
        elr: str | None = None,
        concepts: Iterable[QName] = (),
        **details: Any,
    ) -> str:
        """Resolve an inconsistent duplicate label by keeping the longer
        text, emitting a diagnostic. Returns `label` unchanged when there is
        no conflicting existing label."""
        if existing and existing != label:
            self.diagnostics.emit(
                ArelleDiagnostic.warning(
                    "Inconsistent duplicate labels found; keeping the longer label",
                    elr=elr,
                    concepts=concepts,
                    label=label,
                    otherLabel=existing,
                    **details,
                ),
            )
            label = max(existing, label, key=len)
        return label

    def addLabels(
        self,
        concept: ModelConcept,
        jconcept: dict,
    ) -> None:
        """Add labels to the concept JSON."""
        labels: dict[str, dict[str, str]] = {}
        jconcept["labels"] = labels
        for labelRel in self.model.resourceRelationshipsFrom(
            concept, XbrlConst.conceptLabel
        ):
            label_resource = labelRel.resource
            role: str = label_resource.role or XbrlConst.standardLabel
            if (lang := label_resource.xmlLang) and (lang := lang.strip().lower()):
                # BCP47 says that xml:lang is case insensitive
                langLabels = labels.setdefault(lang, {})
                langLabels[role] = self.keepLongerLabel(
                    langLabels.get(role),
                    label_resource.stringValue.strip(),
                    concepts=(qnameOf(concept),),
                    lang=lang,
                    role=role,
                )
            else:
                self.diagnostics.emit(
                    ArelleDiagnostic.warning(
                        "Label has no xml:lang so is being ignored",
                        concepts=(qnameOf(concept),),
                        role=role,
                    ),
                )

    def addReferences(
        self,
        concept: ModelConcept,
        jconcept: dict,
    ) -> None:
        """Add references to the concept JSON."""
        refs: list[dict[str, Any]] = []
        for refRel in self.model.resourceRelationshipsFrom(
            concept, XbrlConst.conceptReference
        ):
            ref_resource = refRel.resource
            if not refRel.role:
                raise ArelleModelInconsistency(
                    ArelleDiagnostic.error(
                        "Reference resource has no role",
                        concepts=(qnameOf(concept),),
                        resource=repr(ref_resource),
                    )
                )
            role: str = str(refRel.role)

            ref_parts: list[tuple[QName, str]] = []
            for part in ref_resource.iterchildren():
                if value := part.stringValue.strip():
                    ref_parts.append((part.qname, value))

            if ref_parts:
                refs.append(
                    {
                        "role": role,
                        "order": refRel.order,
                        "parts": ref_parts,
                        "sort_key": (
                            refRel.order,
                            role,
                            tuple((str(name), str(value)) for name, value in ref_parts),
                        ),
                    }
                )

        if refs:
            all_order1 = all(r["order"] == 1 for r in refs)

            if not all_order1:
                self.diagnostics.emit(
                    ArelleDiagnostic.info(
                        "References use order values other than 1 and will be sorted by order",
                        concepts=(qnameOf(concept),),
                        orders=sorted({r["order"] for r in refs}),
                    ),
                )

            refs.sort(key=lambda r: r["sort_key"])

            refs = [{"role": r["role"], "parts": r["parts"]} for r in refs]
            jconcept["references"] = refs

    def extractConceptsAndMetadata(self) -> None:
        self.cntlr.addToLog("Processing concepts (including labels and references)")
        for qname, concept in self.model.itemConcepts():
            dataType, baseDataType = self.model.typeQNamesOf(concept)
            jconcept: dict[str, Any] = {
                "dataType": dataType,
                "baseDataType": baseDataType,
                "periodType": concept.periodType,
            }
            self.addConceptMetadata(concept, jconcept)
            self.addLabels(concept, jconcept)
            self.addReferences(concept, jconcept)

            if concept.isEnumeration and not concept.isEnumeration2Item:
                self.diagnostics.emit(
                    ArelleDiagnostic.warning(
                        "Extensible enumerations other than 2.0 are not supported",
                        concepts=(qname,),
                    ),
                )
            if concept.isEnumeration2Item:
                headUsable = concept.isEnumDomainUsable
                linkrole = concept.enumLinkrole
                domainQName = concept.enumDomainQname
                if linkrole is None or domainQName is None:
                    raise ArelleModelInconsistency(
                        ArelleDiagnostic.error(
                            "Extensible enumeration has no enumeration domain or linkrole",
                            concepts=(qname,),
                        )
                    )
                jconcept.setdefault("other", {})["ee20DomainMembers"] = (
                    self.getDomainMembersForEnumeration(
                        linkrole,
                        headUsable,
                        self.model.concept(domainQName),
                        qname,
                    )
                )
            if concept.isTypedDimension:
                jconcept.setdefault("other", {})["typedElement"] = (
                    self.model.typedDomainQNameOf(concept)
                )
            self.taxonomyJson["concepts"][qname] = jconcept

    def reportIsolatedConcepts(self) -> None:
        """Warn about concepts absent from the DTS's arc structure, at three
        exclusive levels (a concept is reported at its single most severe
        level, so the three lists partition the isolated concepts):

        - fully isolated: no relationship, incoming or outgoing, in any
          base set at all
        - documentation only: relationships exist, but only
          concept-label/concept-reference ones
        - not presented: has other relationships, but none in the
          presentation (parent-child) linkbase

        A concept is excluded from "not presented" when it is a domain
        member (or domain head) of an enum2 concept or explicit dimension
        that is itself presented -- such members are not normally presented
        directly, so flagging them would be noise rather than signal.
        """
        baseSets = self.model.baseSetsInDTS()
        documentationArcroles = frozenset(
            {XbrlConst.conceptLabel, XbrlConst.conceptReference}
        )

        presented: set[QName] = set()
        arcrolesByQName: dict[QName, set[str]] = {}
        for qname, concept in self.model.itemConcepts():
            touched: set[str] = set()
            for arcrole, linkrole in baseSets:
                relSet = self.model.conceptRelationshipSet(arcrole, linkrole)
                if relSet.hasRelationshipsFrom(concept) or relSet.hasRelationshipsTo(
                    concept
                ):
                    touched.add(arcrole)
            arcrolesByQName[qname] = touched
            if XbrlConst.parentChild in touched:
                presented.add(qname)

        excludedFromNotPresented = self._presentedDimensionalDomainMembers(presented)

        fullyIsolated: list[QName] = []
        documentationOnly: list[QName] = []
        notPresented: list[QName] = []
        for qname, touched in arcrolesByQName.items():
            if not touched:
                fullyIsolated.append(qname)
            elif touched <= documentationArcroles:
                documentationOnly.append(qname)
            elif (
                XbrlConst.parentChild not in touched
                and qname not in excludedFromNotPresented
            ):
                notPresented.append(qname)

        documentationOnlyText = (
            "concept(s) have only label/reference relationships "
            "(no presentation or definition)"
        )
        for text, qnames in (
            ("concept(s) have no relationship in any linkbase", fullyIsolated),
            (documentationOnlyText, documentationOnly),
            ("concept(s) are absent from the presentation linkbase", notPresented),
        ):
            if qnames:
                self.diagnostics.emit(
                    ArelleDiagnostic.warning(
                        f"{len(qnames)} {text}",
                        concepts=sorted(qnames),
                    ),
                )

    def _presentedDimensionalDomainMembers(
        self, presented: Collection[QName]
    ) -> frozenset[QName]:
        """QNames that are a domain member (or domain head) of an enum2
        concept or explicit dimension which is itself presented."""
        excluded: set[QName] = set()

        for qname, concept in self.model.itemConcepts():
            if concept.isEnumeration2Item and qname in presented:
                # enumLinkrole/enumDomainQname are known good here:
                # extractConceptsAndMetadata() already raised if either was
                # missing for this concept.
                linkrole = concept.enumLinkrole
                domainQName = concept.enumDomainQname
                if linkrole is None or domainQName is None:
                    continue
                domainHead = self.model.concept(domainQName)
                excluded.add(domainQName)
                domainMemberRelSet = self.model.conceptRelationshipSet(
                    XbrlConst.domainMember, linkrole
                )
                excluded.update(
                    row.qname
                    for row in self.walkDefinitionChildren(
                        domainHead, domainMemberRelSet, 1
                    )
                )
            elif concept.isExplicitDimension and qname in presented:
                for linkrole in self.model.linkrolesFor(XbrlConst.dimensionDomain):
                    dimensionDomainRelSet = self.model.conceptRelationshipSet(
                        XbrlConst.dimensionDomain, linkrole
                    )
                    for rel in dimensionDomainRelSet.relationshipsFrom(concept):
                        excluded.add(rel.targetQName)
                        domainMemberRelSet = self.model.conceptRelationshipSet(
                            XbrlConst.domainMember, rel.consecutiveLinkrole
                        )
                        excluded.update(
                            row.qname
                            for row in self.walkDefinitionChildren(
                                rel.target, domainMemberRelSet, 1
                            )
                        )

        return frozenset(excluded)

    def extractDimensionDefinitions(self) -> None:
        self.cntlr.addToLog("Processing dimensions")
        self.taxonomyJson["dimensions"] = defaultdict(dict)
        # Get the hypercubes and primary items
        hypercubeArcRoles = (XbrlConst.all, XbrlConst.notAll)
        for elrUri in self.model.linkrolesFor(*hypercubeArcRoles):
            relSet = self.model.conceptRelationshipSet(hypercubeArcRoles, elrUri)
            roots = relSet.rootConcepts()
            primaryItemsByHypercube: dict[QName, set[QName]] = defaultdict(set)
            for root_concept in roots:
                for rel in relSet.relationshipsFrom(root_concept):
                    concept = rel.target
                    if not concept.isHypercubeItem:
                        raise ArelleModelInconsistency(
                            ArelleDiagnostic.error(
                                "Expected a hypercube as the target of an all/notAll relationship",
                                elr=elrUri,
                                concepts=(rel.targetQName,),
                            )
                        )
                    if rel.targetQName in self.taxonomyJson["dimensions"][elrUri]:
                        # Two different root primary items targeting the same
                        # hypercube in one ELR is a shape mireport doesn't
                        # understand (which root's primary items apply?), and
                        # would otherwise silently overwrite the first root's
                        # cube entry -- dimensions[elrUri][hypercube] is keyed
                        # by hypercube alone.
                        raise ArelleModelInconsistency(
                            ArelleDiagnostic.error(
                                "Hypercube is targeted by all/notAll relationships from more than one root primary item",
                                elr=elrUri,
                                concepts=(rel.targetQName,),
                            )
                        )
                    if not rel.isClosed:
                        self.diagnostics.emit(
                            ArelleDiagnostic.info(
                                "Hypercube is open",
                                elr=elrUri,
                                concepts=(rel.targetQName,),
                            ),
                        )
                    cube: dict[str, Any] = {
                        "primaryItems": self.getPrimaryItems(
                            rel.consecutiveLinkrole, root_concept
                        ),
                        "xbrldt:contextElement": rel.contextElement,
                        "xbrldt:closed": rel.isClosed,
                    }
                    primaryItemsByHypercube[rel.targetQName].update(
                        q for _, q in cube["primaryItems"]
                    )
                    for dimensionRel in self.getDimensions(
                        rel.consecutiveLinkrole, concept, rel.isClosed
                    ):
                        dimension = dimensionRel.target
                        if dimension.isExplicitDimension:
                            cube.setdefault("explicitDimensions", {})[
                                dimensionRel.targetQName
                            ] = self.getDomainMembersForExplicitDimension(
                                dimension, dimensionRel.consecutiveLinkrole
                            )
                        elif dimension.isTypedDimension:
                            cube.setdefault("typedDimensions", []).append(
                                dimensionRel.targetQName
                            )
                    self.taxonomyJson["dimensions"][elrUri][rel.targetQName] = cube

            self.reportHypercubesForLinkrole(elrUri, primaryItemsByHypercube)

    def reportHypercubesForLinkrole(
        self, elrUri: str, primaryItemsByHypercube: Mapping[QName, Collection[QName]]
    ) -> None:
        """Report a base set holding several hypercubes, warning if they share
        primary items.

        XDT conjoins a base set's hypercubes, so a primary item declared in more
        than one of them must satisfy all of them at once -- something mireport
        does not support (see Taxonomy._overlappingPrimaryItems in taxonomy.py).
        """
        if len(primaryItemsByHypercube) < 2:
            return
        hypercubes = sorted(primaryItemsByHypercube)
        if shared := _overlappingPrimaryItems(primaryItemsByHypercube):
            self.diagnostics.emit(
                ArelleDiagnostic.warning(
                    f"Extended link role has {len(hypercubes)} hypercubes sharing primary items",
                    elr=elrUri,
                    concepts=hypercubes,
                    primaryItems=sorted(shared),
                    hint=(
                        "XDT conjoins a base set's hypercubes, so a primary item in "
                        "more than one of them must satisfy all of them at once."
                    ),
                ),
            )
        else:
            self.diagnostics.emit(
                ArelleDiagnostic.info(
                    f"Extended link role has {len(hypercubes)} hypercubes",
                    elr=elrUri,
                    concepts=hypercubes,
                ),
            )

    def reportDomainMemberOnlyLinkroleRoots(self) -> None:
        """Warn about an extended link role holding domain-member relationships
        but none of the dimensional arcroles (all/notAll/hypercube-dimension/
        dimension-domain), when its domain-member tree has more than one root.

        extractDimensionDefinitions() already checks all/notAll roots for the
        hypercube-bearing ELRs it walks; this covers the other ELRs that hold
        a domain-member tree by itself (e.g. an enumeration domain, or a
        general-purpose taxonomy hierarchy), where a second root is otherwise
        never noticed."""
        dimensionalArcroles = frozenset(
            {
                XbrlConst.all,
                XbrlConst.notAll,
                XbrlConst.hypercubeDimension,
                XbrlConst.dimensionDomain,
            }
        )
        arcrolesByLinkrole: dict[str, set[str]] = defaultdict(set)
        for arcrole, linkrole in self.model.baseSetsInDTS():
            arcrolesByLinkrole[linkrole].add(arcrole)

        for linkrole, arcroles in arcrolesByLinkrole.items():
            if XbrlConst.domainMember not in arcroles or arcroles & dimensionalArcroles:
                continue
            relSet = self.model.conceptRelationshipSet(XbrlConst.domainMember, linkrole)
            roots = relSet.rootConcepts()
            if len(roots) > 1:
                self.diagnostics.emit(
                    ArelleDiagnostic.warning(
                        f"Domain-member-only extended link role has multiple ({len(roots)}) roots",
                        elr=linkrole,
                        # document order, deliberately not sorted
                        concepts=(qnameOf(root) for root in roots),
                    ),
                )

    def getLabelsForRoleType(self, roleType: ModelRoleType) -> dict[str, str]:
        labels: dict[str, str] = {}
        for labelRel in self.model.resourceRelationshipsFrom(
            roleType, XbrlConst.elementLabel
        ):
            label_resource = labelRel.resource
            if lang := label_resource.xmlLang:
                # BCP47 says that xml:lang is case insensitive
                lang = lang.lower()
                labels[lang] = self.keepLongerLabel(
                    labels.get(lang),
                    label_resource.stringValue.strip(),
                    elr=roleType.roleURI,
                    lang=lang,
                    definition=roleType.definition,
                )
        return labels

    def extractPresentation(self) -> None:
        self.cntlr.addToLog("Processing presentation network")
        for elrUri in self.model.linkrolesFor(XbrlConst.parentChild):
            self.cntlr.addToLog(f"Processing {elrUri}")
            roleType = self.model.roleType(elrUri)
            self.taxonomyJson["presentation"][elrUri] = {
                "definition": roleType.definition,
            }
            if labels := self.getLabelsForRoleType(roleType):
                self.taxonomyJson["presentation"][elrUri]["labels"] = labels
            relSet = self.model.conceptRelationshipSet(XbrlConst.parentChild, elrUri)
            roots = relSet.rootConcepts()
            match len(roots):
                case 0:
                    self.diagnostics.emit(
                        ArelleDiagnostic.warning("Presentation is empty", elr=elrUri),
                    )
                case 1:
                    pass
                case _:
                    self.diagnostics.emit(
                        ArelleDiagnostic.warning(
                            f"Presentation has multiple ({len(roots)}) roots so presentation order will be arbitrary",
                            elr=elrUri,
                            # document order, deliberately not sorted
                            concepts=(qnameOf(root) for root in roots),
                        ),
                    )
            rows: list[tuple[int, QName] | tuple[int, QName, str]] = []
            for root in roots:
                rows.append((0, qnameOf(root)))
                rows.extend(
                    (row.indent, row.qname)
                    if row.preferredLabel is None
                    else (row.indent, row.qname, row.preferredLabel)
                    for row in self.walkPresentationChildren(root, relSet, 1)
                )
            self.taxonomyJson["presentation"][elrUri]["rows"] = rows
        self.cntlr.addToLog("Processing presentation network [completed]")
