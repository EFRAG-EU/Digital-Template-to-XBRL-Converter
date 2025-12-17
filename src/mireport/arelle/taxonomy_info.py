import json
import logging
import time
from collections import defaultdict
from collections.abc import Iterable, MutableMapping
from contextlib import closing
from typing import Any, Optional, TypeVar

from arelle import XbrlConst
from arelle.api.Session import Session
from arelle.Cntlr import Cntlr
from arelle.logging.handlers.LogToXmlHandler import LogToXmlHandler
from arelle.ModelDtsObject import ModelConcept, ModelResource, ModelRoleType
from arelle.ModelRelationshipSet import ModelRelationshipSet
from arelle.ModelValue import QName
from arelle.ModelXbrl import ModelXbrl
from arelle.RuntimeOptions import RuntimeOptions
from arelle.utils.PluginData import PluginData
from arelle.ValidateUtr import UtrEntry

from mireport.arelle.support import (
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


def concepts_to_qnames(
    concept_list: Iterable[ModelConcept], sorted: bool = True
) -> list[QName]:
    """
    Convert an iterable of ModelConcept objects to a list of their qualified names.

    Args:
        concept_list: An iterable of ModelConcept objects to convert.
        sorted: If True, sorts the qualified names alphabetically before joining. Defaults to True.

    Returns:
        A list of qualified names (QNames).
    """
    qname_list = [concept.qname for concept in concept_list]
    if sorted:
        qname_list.sort()
    return qname_list


def callArelleForTaxonomyInfo(
    entry_point: str,
    taxonomy_zips: list[str],
    taxonomy_json_path: str,
    utr_json_path: Optional[str] = None,
) -> ArelleProcessingResult:
    pluginOptions = {"taxonomyDataFile": taxonomy_json_path}
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
        logFormat="%(asctime)s [%(messageCode)s] %(message)s - %(file)s",
        logPropagate=False,
        packages=taxonomy_zips,
        pluginOptions=pluginOptions,
        plugins=__file__,
        validate=True,
        utrValidate=utrValidation,
    )
    with Session() as session, closing(LogToXmlHandler()) as log_handler:
        session.run(
            options,
            logHandler=log_handler,
            logFilters=[],
        )
        results = ArelleProcessingResult.fromLogToXmlHandler(log_handler)
    return results


class TaxonomyInfoPluginData(PluginData):
    Taxonomy: dict = dict()
    UTR: dict = dict()


def pluginData(cntlr: Cntlr) -> TaxonomyInfoPluginData:
    pluginData = cntlr.getPluginData(PLUGIN_NAME)
    if pluginData is None:
        pluginData = TaxonomyInfoPluginData(PLUGIN_NAME)
        cntlr.setPluginData(pluginData)
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
            for dataTypeIsh in self.utrModel.keys()
            for u in self.utrModel[dataTypeIsh].values()
        ]
        for entry in sorted(utrEntries, key=lambda e: e.unitId):
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
        self.taxonomyJson: dict[str, MutableMapping] = defaultdict(dict)
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
        self.cntlr.addToLog("Verifying all concepts have QNames with prefixes defined")
        namespacesWithoutPrefix: set[str] = set()
        undesiredQNames: set[QName] = set()

        def verify_qname(qname: QName) -> None:
            """Get the QName as a string with prefix if possible."""
            if qname.prefix is None:
                undesiredQNames.add(qname)
                if qname.namespaceURI not in namespacesWithoutPrefix:
                    namespacesWithoutPrefix.add(qname.namespaceURI)
                    self.cntlr.addToLog(
                        f"WARNING: Found concept with namespace with no prefix defined. Namespace: '{qname.namespaceURI}' (first localName found with this namespace: '{qname.localName}')",
                        level=logging.WARNING,
                    )

        for qname, concept in sorted(self.modelXbrl.qnameConcepts.items()):
            if concept.isItem:
                if concept.qname.namespaceURI in {XbrlConst.xbrli, XbrlConst.xbrldt}:
                    continue
                verify_qname(qname)
                verify_qname(concept.type.qname)
                verify_qname(concept.baseXbrliTypeQname)

        if not namespacesWithoutPrefix and not undesiredQNames:
            self.cntlr.addToLog(
                f"... All {len(self.modelXbrl.qnameConcepts):,} concepts (and their data-types) have QNames with prefixes defined"
            )
        else:
            ns_str = "\n".join(sorted(namespacesWithoutPrefix))
            self.cntlr.addToLog(
                f"{len(namespacesWithoutPrefix):,} namespaces (affecting {len(undesiredQNames):,} QNames) with no prefix defined.\n{ns_str}",
                level=logging.WARNING,
            )

    def walkDefinitionChildren(
        self,
        parent_concept: ModelConcept,
        relSet: ModelRelationshipSet,
        rows: list[tuple[int, QName, bool | None]],
        indent: int,
        includeUsable: bool = False,
    ) -> None:
        for rel in relSet.fromModelObject(parent_concept):
            child_concept = rel.toModelObject
            if includeUsable:
                rows.append((indent, child_concept.qname, rel.isUsable))
            else:
                rows.append((indent, child_concept.qname, None))
            if rel.targetRole:
                childRelSet = self.modelXbrl.relationshipSet(
                    rel.arcrole, rel.targetRole
                )
            else:
                childRelSet = relSet
            self.walkDefinitionChildren(
                child_concept, childRelSet, rows, indent + 1, includeUsable
            )

    def walkPresentationChildren(
        self,
        parent_concept: ModelConcept,
        relSet: ModelRelationshipSet,
        rows: list[tuple[int, QName, str] | tuple[int, QName]],
        indent: int,
    ) -> None:
        for rel in relSet.fromModelObject(parent_concept):
            child_concept = rel.toModelObject
            row: tuple[int, QName, str] | tuple[int, QName]
            if (preferredLabel := rel.preferredLabel) is None:
                row = (indent, child_concept.qname)
            else:
                row = (indent, child_concept.qname, preferredLabel)
            rows.append(row)
            self.walkPresentationChildren(child_concept, relSet, rows, indent + 1)

    def getPrimaryItems(
        self, elrUri: str, domainHeadConcept: ModelConcept
    ) -> list[tuple[int, QName]]:
        relSet = self.modelXbrl.relationshipSet(XbrlConst.domainMember, elrUri)
        rows: list[tuple[int, QName, bool | None]] = []
        rows.append((0, domainHeadConcept.qname, None))

        # N.B. domainHeadConcept does not have to be a root concept

        if not relSet.fromModelObject(domainHeadConcept):
            self.cntlr.addToLog(
                f"WARNING: {elrUri} has no primary items attached to hypercube beyond {domainHeadConcept.qname} (no outgoing domain-member relationships).",
                level=logging.WARNING,
            )
            return [(0, domainHeadConcept.qname)]

        self.walkDefinitionChildren(domainHeadConcept, relSet, rows, 1)
        return [(i, qname) for i, qname, _ in rows]

    def getDimensions(
        self, elrUri: str, hypercube: ModelConcept, hypercubeIsClosed: bool
    ) -> list[tuple[ModelConcept, str]]:
        relSet = self.modelXbrl.relationshipSet(XbrlConst.hypercubeDimension, elrUri)
        roots: frozenset[ModelConcept] = frozenset(relSet.rootConcepts)

        if not roots:
            if hypercubeIsClosed:
                self.cntlr.addToLog(
                    f"WARNING: {elrUri} contains a closed hypercube '{hypercube.qname}' with no dimensions (no outgoing hypercube-dimension relationships).",
                    level=logging.WARNING,
                )
            return []

        if len(roots) > 1 and elrUri not in self.elr_hypercube_dimension_seen:
            self.elr_hypercube_dimension_seen.add(elrUri)
            self.cntlr.addToLog(
                f"INFO: {elrUri} has {len(roots)} hypercubes: {concepts_to_qnames(roots)}.",
                level=logging.INFO,
            )

        assert hypercube in roots, (
            f"{hypercube.qname} should be in {concepts_to_qnames(roots)}"
        )
        return [
            (rel.toModelObject, rel.consecutiveLinkrole)
            for rel in relSet.fromModelObject(hypercube)
        ]

    def getDomainMembersForExplicitDimension(
        self,
        explicitDimension: ModelConcept,
        elrUri: str,
    ) -> list[QName]:
        dimensionDomainRelSet = self.modelXbrl.relationshipSet(
            XbrlConst.dimensionDomain, elrUri
        )

        assert explicitDimension in dimensionDomainRelSet.rootConcepts, (
            f"Dimension {explicitDimension.qname} should be in {concepts_to_qnames(dimensionDomainRelSet.rootConcepts)}"
        )
        domainMemberTrees: list[tuple[ModelConcept, bool, ModelRelationshipSet]] = [
            (
                rel.toModelObject,
                rel.isUsable,
                self.modelXbrl.relationshipSet(
                    XbrlConst.domainMember, rel.consecutiveLinkrole
                ),
            )
            for rel in dimensionDomainRelSet.fromModelObject(explicitDimension)
        ]

        hasDefaultedDomainMember = explicitDimension in self.dimensionDefaults

        if not domainMemberTrees:
            if hasDefaultedDomainMember:
                self.cntlr.addToLog(
                    f"WARNING: {elrUri} Dimension {explicitDimension.qname} has a defaulted domain member {self.dimensionDefaults[explicitDimension].qname} but no domain relationships",
                    level=logging.WARNING,
                )
            else:
                self.cntlr.addToLog(
                    f"WARNING: {elrUri} Dimension {explicitDimension.qname} has no domain relationships",
                    level=logging.WARNING,
                )
            return []

        dimensionHasMultipleDimensionDomainRelationships = 1 < len(
            dimensionDomainRelSet.fromModelObject(explicitDimension)
        )

        rows: list[tuple[int, QName, bool | None]] = []
        for domainHeadConcept, usable, domainMemberRelSet in domainMemberTrees:
            self.verifyDomainMemberTree(
                explicitDimension,
                hasDefaultedDomainMember,
                dimensionHasMultipleDimensionDomainRelationships,
                domainHeadConcept,
                domainMemberRelSet,
            )

            rows.append((0, domainHeadConcept.qname, usable))
            self.walkDefinitionChildren(
                domainHeadConcept, domainMemberRelSet, rows, 1, includeUsable=True
            )
        return unique_list(q for _, q, usable in rows if usable)

    def verifyDomainMemberTree(
        self,
        explicitDimension,
        hasDefaultedDomainMember,
        dimensionHasMultipleDimensionDomainRelationships,
        domainHeadConcept,
        domainMemberRelSet,
    ) -> None:
        outgoing = domainMemberRelSet.fromModelObject(domainHeadConcept)
        incoming = domainMemberRelSet.toModelObject(domainHeadConcept)
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
                self.cntlr.addToLog(
                    f"WARNING: {elrUri} Dimension {explicitDimension.qname} has domain head {domainHeadConcept.qname} with no outgoing domain-member relationships",
                    level=logging.WARNING,
                )

        if incoming:
            self.cntlr.addToLog(
                f"WARNING: {elrUri} Dimension {explicitDimension.qname} has domain head {domainHeadConcept.qname} with incoming domain-member relationships. How exciting!",
                level=logging.WARNING,
            )

    def getDomainMembersForEE(
        self, elrUri: str, headUsable: bool, domainHeadConcept: ModelConcept
    ) -> list[QName]:
        """Deliberately over simplified for now."""
        domainMemberRelSet = self.modelXbrl.relationshipSet(
            XbrlConst.domainMember, elrUri
        )
        rows: list[tuple[int, QName, bool | None]] = []
        self.walkDefinitionChildren(
            domainHeadConcept, domainMemberRelSet, rows, 1, includeUsable=True
        )
        if headUsable:
            rows.insert(0, (0, domainHeadConcept.qname, headUsable))
        return unique_list(q for _, q, usable in rows if usable)

    def extractDimensionDefaults(self) -> None:
        elrsWithDefaults = set()
        for arcroleUri, elrUri, linkqname, arcqname in self.modelXbrl.baseSets.keys():
            if arcroleUri == XbrlConst.dimensionDefault and elrUri is not None:
                elrsWithDefaults.add(elrUri)

        dimToElrMap: dict[ModelConcept, list[str]] = defaultdict(list)

        for elrUri in elrsWithDefaults:
            dimensionDefaultRelSet = self.modelXbrl.relationshipSet(
                XbrlConst.dimensionDefault, elrUri
            )

            dimensions: list[ModelConcept] = dimensionDefaultRelSet.rootConcepts

            for d in dimensions:
                dimToElrMap[d].append(elrUri)

                members: list[ModelConcept] = [
                    rel.toModelObject
                    for rel in dimensionDefaultRelSet.fromModelObject(d)
                ]
                assert len(members) == 1, (
                    f"More than one default for dimension {d.qname} in {elrUri}, {concepts_to_qnames(members)}."
                )
                m = members[0]
                if (m0 := self.dimensionDefaults.get(d)) is not None:
                    otherElrs = dimToElrMap[d][:-1]
                    if m0 != m:
                        self.cntlr.addToLog(
                            f"WARNING: Inconsistent duplicate definition of dimension default for dimension {d.qname}, member {m.qname=} {m0.qname=} in {elrUri=}. {otherElrs=}",
                            level=logging.WARNING,
                        )
                    else:
                        self.cntlr.addToLog(
                            f"INFO: Consistent duplicate definition of dimension default for dimension {d.qname}, member {m.qname} in {elrUri}. {otherElrs=}",
                            level=logging.INFO,
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
        relSet = self.modelXbrl.relationshipSet(XbrlConst.conceptLabel)
        for r in relSet.fromModelObject(concept):
            label_resource: ModelResource = r.toModelObject
            role: str = str(label_resource.role) or XbrlConst.standardLabel
            if lang := label_resource.xmlLang:
                # BCP47 says that xml:lang is case insensitive
                lang = lang.lower()
                label = label_resource.stringValue.strip()
                if (label0 := jconcept["labels"][lang].get(role)) and label0 != label:
                    self.cntlr.addToLog(
                        f"WARNING: Inconsistent duplicate labels found for [{concept.qname}]: {label0=} {label=} {lang=} {role=}",
                        level=logging.WARNING,
                    )
                    label = max(label0, label, key=len)
                jconcept["labels"][lang][role] = label
            else:
                self.cntlr.addToLog(
                    f"WARNING: Label for {concept.qname} ({role=}) has no xml:lang so is being ignored.",
                    level=logging.WARNING,
                )

    def extractConceptsAndMetadata(self) -> None:
        self.cntlr.addToLog("Processing concepts (including labels and references)")
        for qname, concept in sorted(self.modelXbrl.qnameConcepts.items()):
            if concept.isItem:
                if concept.qname.namespaceURI in (XbrlConst.xbrli, XbrlConst.xbrldt):
                    # We don't need/want xbrli:item, xbrldt:dimensionItem or
                    # xbrldt:hypercubeItem in our concept list. Arelle docs
                    # suggests isItem should supress xbrli:item but it doesn't.
                    continue
                jconcept = {
                    # We use concept.type.qname as it gets the namespace prefix
                    # right, i.e. something defined in modelXbrl.prefixedNamespace.
                    # concept.typeQname works almost the same but prefers to use a
                    # prefix from ?the defining schema? and can use one that is not
                    # defined in modelXbrl.prefixedNamespace which makes it
                    # impossible to find the namespace
                    "dataType": concept.type.qname,
                    "baseDataType": concept.baseXbrliTypeQname,
                    "periodType": concept.periodType,
                }
                self.addConceptMetadata(concept, jconcept)
                self.addLabels(concept, jconcept)

                if concept.isEnumeration and not concept.isEnumeration2Item:
                    self.cntlr.addToLog(
                        f"WARNING: Extensible enumerations other than 2.0 are not supported. {concept.qname}",
                        level=logging.WARN,
                    )
                if concept.isEnumeration2Item:
                    headUsable = concept.isEnumDomainUsable
                    linkrole = concept.enumLinkrole
                    jconcept.setdefault("other", {})["ee20DomainMembers"] = (
                        self.getDomainMembersForEE(
                            linkrole,
                            headUsable,
                            self.modelXbrl.qnameConcepts[concept.enumDomainQname],
                        )
                    )
                if concept.isTypedDimension:
                    jconcept.setdefault("other", {})["typedElement"] = (
                        concept.typedDomainElement.qname
                    )
                self.taxonomyJson["concepts"][qname] = jconcept

    def extractDimensionDefinitions(self) -> None:
        self.cntlr.addToLog("Processing dimensions")
        self.taxonomyJson["dimensions"] = defaultdict(dict)
        # Get the hypercubes and primary items
        hypercubeArcRoles = (XbrlConst.all, XbrlConst.notAll)
        for arcroleUri, elrUri, linkqname, arcqname in self.modelXbrl.baseSets.keys():
            if linkqname is None or arcqname is None:
                continue
            if arcroleUri in hypercubeArcRoles and elrUri is not None:
                relSet = self.modelXbrl.relationshipSet(hypercubeArcRoles, elrUri)
                for root_concept in relSet.rootConcepts:
                    for rel in relSet.fromModelObject(root_concept):
                        concept: ModelConcept = rel.toModelObject
                        if concept.isHypercubeItem:
                            cube = {
                                "primaryItems": self.getPrimaryItems(
                                    rel.consecutiveLinkrole, root_concept
                                ),
                                "xbrldt:contextElement": rel.contextElement,
                                "xbrldt:closed": rel.isClosed,
                            }
                            for dimension, consecutiveElr in self.getDimensions(
                                rel.consecutiveLinkrole, concept, rel.isClosed
                            ):
                                if dimension.isExplicitDimension:
                                    cube.setdefault("explicitDimensions", {})[
                                        dimension.qname
                                    ] = self.getDomainMembersForExplicitDimension(
                                        dimension, consecutiveElr
                                    )
                                elif dimension.isTypedDimension:
                                    cube.setdefault("typedDimensions", []).append(
                                        dimension.qname
                                    )
                            self.taxonomyJson["dimensions"][elrUri][concept.qname] = (
                                cube
                            )
                        else:
                            raise ArelleRelatedException(
                                f"Found a {concept} but expected a hypercube."
                            )

        self.cntlr.addToLog("Processing dimension defaults")
        if self.dimensionDefaults:
            self.taxonomyJson["dimensions"]["_defaults"] = {
                d.qname: m.qname for d, m in self.dimensionDefaults.items()
            }

    def getLabelsForRoleType(self, roleType: ModelRoleType) -> dict[str, str]:
        relSet = self.modelXbrl.relationshipSet(XbrlConst.elementLabel)
        labels: dict[str, str] = {}
        for r in relSet.fromModelObject(roleType):
            label_resource: ModelResource = r.toModelObject
            if lang := label_resource.xmlLang:
                # BCP47 says that xml:lang is case insensitive
                lang = lang.lower()
                label = label_resource.stringValue.strip()
                if (label0 := labels.get(lang)) and label0 != label:
                    self.cntlr.addToLog(
                        f"Inconsistent duplicate labels found for roleType [{roleType.roleURI}]: {label0=} {label=} {lang=} {roleType.definition=}",
                        level=logging.WARNING,
                    )
                    label = max(label0, label, key=len)
                labels[lang] = label
        return labels

    def getRoleType(self, roleUri: str) -> ModelRoleType:
        matching = self.modelXbrl.roleTypes.get(roleUri, [])
        if (num := len(matching)) != 1:
            raise ArelleRelatedException(
                f"Wrong number {num} of role type objects found for {roleUri=} (expected 1)"
            )
        return matching[0]

    def extractPresentation(self) -> None:
        self.cntlr.addToLog("Processing presentation network")
        for arcroleUri, elrUri, linkqname, arcqname in self.modelXbrl.baseSets.keys():
            # cntlr.addToLog(f"{arcroleUri}, {elrUri}, {linkqname}, {arcqname}")
            if linkqname is None or arcqname is None:
                continue
            if arcroleUri == XbrlConst.parentChild and elrUri is not None:
                self.cntlr.addToLog(f"Processing {elrUri}")
                roleType = self.getRoleType(elrUri)
                self.taxonomyJson["presentation"][elrUri] = {
                    "definition": roleType.definition,
                }
                if labels := self.getLabelsForRoleType(roleType):
                    self.taxonomyJson["presentation"][elrUri]["labels"] = labels
                relSet = self.modelXbrl.relationshipSet(XbrlConst.parentChild, elrUri)
                roots = relSet.rootConcepts
                match len(roots):
                    case 0:
                        self.cntlr.addToLog(
                            f"WARNING: {elrUri} presentation is empty",
                            level=logging.WARNING,
                        )
                    case 1:
                        pass
                    case _:
                        self.cntlr.addToLog(
                            f"WARNING: {elrUri} has multiple ({len(roots)}) roots. Presentation order will be arbitrary. Roots: {concepts_to_qnames(roots, sorted=False)}",
                            level=logging.WARNING,
                        )
                rows: list[tuple[int, QName, str] | tuple[int, QName]] = []
                for root in roots:
                    rows.append((0, root.qname))
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
