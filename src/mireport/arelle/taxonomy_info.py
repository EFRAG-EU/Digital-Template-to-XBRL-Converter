from __future__ import annotations

import json
import time
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, TypeVar

if TYPE_CHECKING:
    from typing import Any

from arelle import XbrlConst
from arelle.api.Session import Session
from arelle.Cntlr import Cntlr
from arelle.ModelDtsObject import ModelConcept, ModelRoleType
from arelle.ModelValue import QName
from arelle.ModelXbrl import ModelXbrl
from arelle.RuntimeOptions import RuntimeOptions, RuntimeOptionValue
from arelle.utils.PluginData import PluginData
from arelle.ValidateUtr import UtrEntry

from mireport.arelle.diagnostics import (
    Diagnostic,
    DiagnosticCollector,
    DiagnosticEmitter,
)
from mireport.arelle.model_access import (
    ConceptRelationship,
    ConceptRelationshipSet,
    ValidatedModel,
    qnameOf,
)
from mireport.arelle.support import (
    ArelleModelInconsistency,
    ArelleObjectJSONEncoder,
    ArelleProcessingResult,
    ArelleQNameCanonicaliser,
    ArelleRelatedException,
)

PLUGIN_NAME = "Taxonomy Information Extractor"
T = TypeVar("T")


def unique_list(i: Iterable[T]) -> list[T]:
    # N.B. This maintains insertion order where list(set()) does not.
    return list(dict.fromkeys(i))


def callArelleForTaxonomyInfo(
    entry_point: str,
    taxonomy_zips: list[str],
    taxonomy_json_path: str,
    utr_json_path: str | None = None,
) -> ArelleProcessingResult:
    diagnosticsToken = DiagnosticCollector.open()
    pluginOptions: dict[str, RuntimeOptionValue] = {
        "taxonomyDataFile": taxonomy_json_path,
        "diagnosticsToken": diagnosticsToken,
    }
    utrValidation = False
    if utr_json_path is not None:
        pluginOptions["utrDataFile"] = utr_json_path
        utrValidation = True

    options = RuntimeOptions(
        abortOnMajorError=True,
        entrypointFile=entry_point,
        internetConnectivity="offline",
        formulaAction="none",
        keepOpen=False,
        logFile="logToBuffer",
        logFormat="%(message)s",
        logPropagate=False,
        packages=taxonomy_zips,
        pluginOptions=pluginOptions,
        plugins=__file__,
        validate=True,
        utrValidate=utrValidation,
    )
    try:
        with Session() as session:
            session.run(
                options,
                logFilters=[],
            )
            results = ArelleProcessingResult.fromSession(session)
    finally:
        diagnostics = DiagnosticCollector.close(diagnosticsToken)
    results.addDiagnostics(diagnostics)
    return results


@dataclass
class TaxonomyInfoPluginData(PluginData):
    # N.B. per-instance dicts. Plain class attributes would be shared by every
    # Cntlr that loads this plugin.
    Taxonomy: dict = field(default_factory=dict)
    UTR: dict = field(default_factory=dict)


def pluginData(cntlr: Cntlr) -> TaxonomyInfoPluginData:
    pluginData = cntlr.getPluginData(PLUGIN_NAME)
    if pluginData is None:
        pluginData = TaxonomyInfoPluginData(PLUGIN_NAME)
        cntlr.setPluginData(pluginData)
    if not isinstance(pluginData, TaxonomyInfoPluginData):
        raise ArelleRelatedException(
            f"Plugin data for {PLUGIN_NAME!r} has unexpected type {type(pluginData)!r}"
        )
    return pluginData


def writeDataFile(
    cntlr: Cntlr,
    jsonPath: str,
    dataType: str,
) -> None:
    pdata = pluginData(cntlr)
    data = getattr(pdata, dataType, None)
    if not data:
        cntlr.addToLog(f"No {dataType} data to write")
        return

    with open(jsonPath, "w", encoding="UTF-8") as f:
        tidied = ArelleObjectJSONEncoder.tidyKeys(data)
        json.dump(tidied, f, indent=2, sort_keys=True, cls=ArelleObjectJSONEncoder)
        cntlr.addToLog(f"{dataType} data written to {jsonPath}")


class UTRInfoExtractor:
    def __init__(
        self, cntlr: Cntlr, modelXbrl: ModelXbrl, pData: TaxonomyInfoPluginData
    ):
        self.cntlr: Cntlr = cntlr
        self.modelXbrl: ModelXbrl = modelXbrl
        self.pluginData: TaxonomyInfoPluginData = pData
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

    def extract(self) -> None:
        self.pluginData.UTR.update(
            {
                "utr": self.getUTRForJSON(),
            }
        )

    def getUTRForJSON(self) -> list[dict]:
        """Get the UTR entries from the modelXbrl."""
        # N.B. UTR schema primary key is the status and unitId
        jUTR: list[dict] = []
        interestingKeys = [
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
        ]
        utrEntries = [
            u
            for dataTypeIsh in self.utrModel
            for u in self.utrModel[dataTypeIsh].values()
        ]
        for entry in sorted(utrEntries, key=lambda e: e.unitId or ""):
            jEntry = {}
            for key in interestingKeys:
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
        self.elr_hypercube_dimension_seen: set[str] = set()

    def extract(self) -> None:
        self.verifyConceptQNamesHavePrefixes()
        self.taxonomyJson["entryPoint"] = self.options.entrypointFile

        self.extractPresentation()
        self.extractDimensionDefaults()
        self.extractDimensionDefinitions()
        self.extractConceptsAndMetadata()

        self.cntlr.addToLog("Processing namespaces and namespace prefixes")
        self.taxonomyJson = self.qnameConverter.convert_recursive(self.taxonomyJson)
        self.taxonomyJson["namespaces"] = self.qnameConverter.getNamespacePrefixMap()
        pdata = pluginData(self.cntlr)
        pdata.Taxonomy.update(self.taxonomyJson)

        if self.options.utrValidate:
            self.cntlr.addToLog(
                "UTR validation is on so attempting to process UTR entries"
            )
            utrExtractor = UTRInfoExtractor(self.cntlr, self.modelXbrl, pdata)
            utrExtractor.extract()

    def verifyConceptQNamesHavePrefixes(self) -> None:
        """Fail early if Arelle has given us broken QNames.

        itemConcepts() and typeQNamesOf() raise ArelleModelInconsistency for
        any QName without a prefix and namespace, which the QName
        canonicalisation at the end of extract() relies on.
        """
        self.cntlr.addToLog("Verifying all concepts have QNames with prefixes defined")
        for _, concept in self.model.itemConcepts():
            self.model.typeQNamesOf(concept)
        self.cntlr.addToLog(
            f"... All {self.model.conceptCount:,} concepts (and their data-types) have QNames with prefixes defined"
        )

    def walkDefinitionChildren(
        self,
        parent_concept: ModelConcept,
        relSet: ConceptRelationshipSet,
        rows: list[tuple[int, QName, bool | None]],
        indent: int,
        includeUsable: bool = False,
    ) -> None:
        for rel in relSet.relationshipsFrom(parent_concept):
            if includeUsable:
                rows.append((indent, rel.targetQName, rel.isUsable))
            else:
                rows.append((indent, rel.targetQName, None))
            self.walkDefinitionChildren(
                rel.target, relSet.consecutiveSet(rel), rows, indent + 1, includeUsable
            )

    def walkPresentationChildren(
        self,
        parent_concept: ModelConcept,
        relSet: ConceptRelationshipSet,
        rows: list[tuple[int, QName, str] | tuple[int, QName]],
        indent: int,
    ) -> None:
        for rel in relSet.relationshipsFrom(parent_concept):
            row: tuple[int, QName, str] | tuple[int, QName]
            if (preferredLabel := rel.preferredLabel) is None:
                row = (indent, rel.targetQName)
            else:
                row = (indent, rel.targetQName, preferredLabel)
            rows.append(row)
            self.walkPresentationChildren(rel.target, relSet, rows, indent + 1)

    def getPrimaryItems(
        self, elrUri: str, domainHeadConcept: ModelConcept
    ) -> list[tuple[int, QName]]:
        relSet = self.model.conceptRelationshipSet(XbrlConst.domainMember, elrUri)
        domainHeadQName = qnameOf(domainHeadConcept)
        rows: list[tuple[int, QName, bool | None]] = []
        rows.append((0, domainHeadQName, None))

        # N.B. domainHeadConcept does not have to be a root concept

        if not relSet.hasRelationshipsFrom(domainHeadConcept):
            self.diagnostics.emit(
                Diagnostic.warning(
                    "Hypercube has no primary items beyond the domain head (no outgoing domain-member relationships)",
                    elr=elrUri,
                    concepts=(domainHeadQName,),
                ),
            )
            return [(0, domainHeadQName)]

        self.walkDefinitionChildren(domainHeadConcept, relSet, rows, 1)
        return [(i, qname) for i, qname, _ in rows]

    def getDimensions(
        self, elrUri: str, hypercube: ModelConcept, hypercubeIsClosed: bool
    ) -> list[ConceptRelationship]:
        relSet = self.model.conceptRelationshipSet(XbrlConst.hypercubeDimension, elrUri)
        roots: frozenset[ModelConcept] = frozenset(relSet.rootConcepts())

        if not roots:
            if hypercubeIsClosed:
                self.diagnostics.emit(
                    Diagnostic.warning(
                        "Closed hypercube has no dimensions (no outgoing hypercube-dimension relationships)",
                        elr=elrUri,
                        concepts=(qnameOf(hypercube),),
                    ),
                )
            return []

        if len(roots) > 1 and elrUri not in self.elr_hypercube_dimension_seen:
            self.elr_hypercube_dimension_seen.add(elrUri)
            self.diagnostics.emit(
                Diagnostic.info(
                    f"Extended link role has {len(roots)} hypercubes",
                    elr=elrUri,
                    concepts=sorted(qnameOf(root) for root in roots),
                ),
            )

        if hypercube not in roots:
            raise ArelleModelInconsistency(
                Diagnostic.error(
                    "Hypercube is not a root of the hypercube-dimension relationship set",
                    elr=elrUri,
                    concepts=(qnameOf(hypercube),),
                    roots=sorted(qnameOf(root) for root in roots),
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
                Diagnostic.error(
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
                    Diagnostic.warning(
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
                    Diagnostic.warning(
                        "Dimension has no domain relationships",
                        elr=elrUri,
                        concepts=(qnameOf(explicitDimension),),
                    ),
                )
            return []

        dimensionHasMultipleDimensionDomainRelationships = 1 < len(dimensionDomainRels)

        rows: list[tuple[int, QName, bool | None]] = []
        for domainHeadConcept, usable, domainMemberRelSet in domainMemberTrees:
            self.verifyDomainMemberTree(
                explicitDimension,
                hasDefaultedDomainMember,
                dimensionHasMultipleDimensionDomainRelationships,
                domainHeadConcept,
                domainMemberRelSet,
            )
            rows.append((0, qnameOf(domainHeadConcept), usable))
            self.walkDefinitionChildren(
                domainHeadConcept, domainMemberRelSet, rows, 1, includeUsable=True
            )
        return unique_list(q for _, q, usable in rows if usable)

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
                    Diagnostic.warning(
                        "Dimension has a domain head with no outgoing domain-member relationships",
                        elr=elrUri,
                        concepts=(qnameOf(explicitDimension),),
                        domainHead=qnameOf(domainHeadConcept),
                    ),
                )

        if incoming:
            self.diagnostics.emit(
                Diagnostic.warning(
                    "Dimension has a domain head with incoming domain-member relationships. How exciting!",
                    elr=elrUri,
                    concepts=(qnameOf(explicitDimension),),
                    domainHead=qnameOf(domainHeadConcept),
                ),
            )

    def getDomainMembersForEE(
        self, elrUri: str, headUsable: bool, domainHeadConcept: ModelConcept
    ) -> list[QName]:
        """Deliberately over simplified for now."""
        domainMemberRelSet = self.model.conceptRelationshipSet(
            XbrlConst.domainMember, elrUri
        )
        rows: list[tuple[int, QName, bool | None]] = []
        self.walkDefinitionChildren(
            domainHeadConcept, domainMemberRelSet, rows, 1, includeUsable=True
        )
        if headUsable:
            rows.insert(0, (0, qnameOf(domainHeadConcept), headUsable))
        return unique_list(q for _, q, usable in rows if usable)

    def extractDimensionDefaults(self) -> None:
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
                        Diagnostic.error(
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
                            Diagnostic.warning(
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
                            Diagnostic.info(
                                "Consistent duplicate definition of dimension default",
                                elr=elrUri,
                                concepts=(qnameOf(d),),
                                member=qnameOf(m),
                                otherElrs=otherElrs,
                            ),
                        )
                self.dimensionDefaults[d] = m

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

    def addLabels(
        self,
        concept: ModelConcept,
        jconcept: dict,
    ) -> None:
        """Add labels to the concept JSON."""
        jconcept["labels"] = defaultdict(dict)
        for labelRel in self.model.resourceRelationshipsFrom(
            concept, XbrlConst.conceptLabel
        ):
            label_resource = labelRel.resource
            role: str = str(label_resource.role) or XbrlConst.standardLabel
            if (lang := label_resource.xmlLang) and (lang := lang.strip().lower()):
                # BCP47 says that xml:lang is case insensitive
                label = label_resource.stringValue.strip()
                if (label0 := jconcept["labels"][lang].get(role)) and label0 != label:
                    self.diagnostics.emit(
                        Diagnostic.warning(
                            "Inconsistent duplicate labels found; keeping the longer label",
                            concepts=(qnameOf(concept),),
                            lang=lang,
                            role=role,
                            label=label,
                            otherLabel=label0,
                        ),
                    )
                    label = max(label0, label, key=len)
                jconcept["labels"][lang][role] = label
            else:
                self.diagnostics.emit(
                    Diagnostic.warning(
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
                    Diagnostic.error(
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
                        "sort_key": tuple(
                            (
                                refRel.order,
                                role,
                                tuple(
                                    (str(name), str(value)) for name, value in ref_parts
                                ),
                            )
                        ),
                    }
                )

        if refs:
            all_order1 = all(r["order"] == 1 for r in refs)

            if not all_order1:
                self.diagnostics.emit(
                    Diagnostic.info(
                        "References use order values other than 1 and will be sorted by order",
                        concepts=(qnameOf(concept),),
                        orders=sorted(set(r["order"] for r in refs)),
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
                    Diagnostic.warning(
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
                        Diagnostic.error(
                            "Extensible enumeration has no enumeration domain or linkrole",
                            concepts=(qname,),
                        )
                    )
                jconcept.setdefault("other", {})["ee20DomainMembers"] = (
                    self.getDomainMembersForEE(
                        linkrole,
                        headUsable,
                        self.model.concept(domainQName),
                    )
                )
            if concept.isTypedDimension:
                jconcept.setdefault("other", {})["typedElement"] = (
                    self.model.typedDomainQNameOf(concept)
                )
            self.taxonomyJson["concepts"][qname] = jconcept

    def extractDimensionDefinitions(self) -> None:
        self.cntlr.addToLog("Processing dimensions")
        self.taxonomyJson["dimensions"] = defaultdict(dict)
        # Get the hypercubes and primary items
        hypercubeArcRoles = (XbrlConst.all, XbrlConst.notAll)
        for elrUri in self.model.linkrolesFor(*hypercubeArcRoles):
            relSet = self.model.conceptRelationshipSet(hypercubeArcRoles, elrUri)
            for root_concept in relSet.rootConcepts():
                for rel in relSet.relationshipsFrom(root_concept):
                    concept = rel.target
                    if not concept.isHypercubeItem:
                        raise ArelleModelInconsistency(
                            Diagnostic.error(
                                "Expected a hypercube as the target of an all/notAll relationship",
                                elr=elrUri,
                                concepts=(rel.targetQName,),
                            )
                        )
                    if not rel.isClosed:
                        self.diagnostics.emit(
                            Diagnostic.info(
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

        self.cntlr.addToLog("Processing dimension defaults")
        if self.dimensionDefaults:
            self.taxonomyJson["dimensions"]["_defaults"] = {
                qnameOf(d): qnameOf(m) for d, m in self.dimensionDefaults.items()
            }

    def getLabelsForRoleType(self, roleType: ModelRoleType) -> dict[str, str]:
        labels: dict[str, str] = {}
        for labelRel in self.model.resourceRelationshipsFrom(
            roleType, XbrlConst.elementLabel
        ):
            label_resource = labelRel.resource
            if lang := label_resource.xmlLang:
                # BCP47 says that xml:lang is case insensitive
                lang = lang.lower()
                label = label_resource.stringValue.strip()
                if (label0 := labels.get(lang)) and label0 != label:
                    self.diagnostics.emit(
                        Diagnostic.warning(
                            "Inconsistent duplicate labels found for role type; keeping the longer label",
                            elr=roleType.roleURI,
                            lang=lang,
                            label=label,
                            otherLabel=label0,
                            definition=roleType.definition,
                        ),
                    )
                    label = max(label0, label, key=len)
                labels[lang] = label
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
                        Diagnostic.warning("Presentation is empty", elr=elrUri),
                    )
                case 1:
                    pass
                case _:
                    self.diagnostics.emit(
                        Diagnostic.warning(
                            f"Presentation has multiple ({len(roots)}) roots so presentation order will be arbitrary",
                            elr=elrUri,
                            # document order, deliberately not sorted
                            concepts=(qnameOf(root) for root in roots),
                        ),
                    )
            rows: list[tuple[int, QName, str] | tuple[int, QName]] = []
            for root in roots:
                rows.append((0, qnameOf(root)))
                self.walkPresentationChildren(root, relSet, rows, 1)
            self.taxonomyJson["presentation"][elrUri]["rows"] = rows
        self.cntlr.addToLog("Processing presentation network [completed]")


def runTaxonomyInfo(
    cntlr: Cntlr,
    options: RuntimeOptions,
    modelXbrl: ModelXbrl,
    *args: Any,
    **kwargs: Any,
) -> None:
    start = time.perf_counter_ns()
    cntlr.addToLog(f"{PLUGIN_NAME} starting.")
    extractor = TaxonomyInfoExtractor(cntlr, options, modelXbrl)
    extractor.extract()
    if (jsonPath := getattr(options, "taxonomyDataFile", None)) is not None:
        writeDataFile(cntlr, jsonPath, "Taxonomy")
    if (jsonPath := getattr(options, "utrDataFile", None)) is not None:
        writeDataFile(cntlr, jsonPath, "UTR")
    elapsed = (time.perf_counter_ns() - start) / 1_000_000_000
    cntlr.addToLog(f"{PLUGIN_NAME} completed ({elapsed:,.2f} seconds elapsed).")


__pluginInfo__ = {
    "name": PLUGIN_NAME,
    "description": "Extracts information from a taxonomy",
    "license": "Apache-2.0",
    "version": "0.7",
    "author": "Stuart Rowan",
    "copyright": " Copyright :; EFRAG :: 2025",
    "CntlrCmdLine.Xbrl.Run": runTaxonomyInfo,
}
