"""
This module provides a simple API for querying an XBRL taxonomy including
concept details and presentation networks.
"""

import re
import warnings
from collections import Counter, defaultdict
from collections.abc import Mapping
from enum import Enum, StrEnum, auto
from functools import cache, cached_property
from types import MappingProxyType
from typing import Any, NamedTuple, Optional, overload

from mireport import data
from mireport.exceptions import (
    AmbiguousComponentException,
    TaxonomyException,
    UnknownTaxonomyException,
)
from mireport.json import getObject, getResource
from mireport.localise import getBestSupportedLanguage
from mireport.stringutil import unicodeDashNormalization
from mireport.utr import UTR
from mireport.xml import (
    ENUM2_NS,
    NCNAME_RE,
    QNAME_RE,
    XBRLI_NS,
    QName,
    QNameMaker,
    getBootsrapQNameMaker,
)

VSME_ENTRY_POINT = "https://xbrl.efrag.org/taxonomy/vsme/2024-12-17/vsme-all.xsd"
MEASUREMENT_GUIDANCE_LABEL_ROLE = "http://www.xbrl.org/2003/role/measurementGuidance"
STANDARD_LABEL_ROLE = "http://www.xbrl.org/2003/role/label"
DOCUMENTATION_LABEL_ROLE = "http://www.xbrl.org/2003/role/documentation"

LABEL_SUFFIX_PATTERN = re.compile(r"\s*\[[a-z ]+\]\s*$")


class PeriodType(StrEnum):
    Duration = "duration"
    Instant = "instant"


class DimensionContainerType(StrEnum):
    Segment = "segment"
    Scenario = "scenario"


class PresentationStyle(Enum):
    """The style of a particular presentation group (ELR)."""

    Empty = auto()
    """Empty means there are
    no reportable concepts in the group.
    """

    List = auto()
    """List means there are no dimensionally
    qualified concepts in the group."""

    Table = auto()
    """Table means there are dimensionally
    qualified reportable concepts."""

    Hybrid = auto()
    """Hybrid means there is a mixture of
    dimensionally unqualified reportable concepts and dimensionally qualified
    reportable concepts."""


class Concept:
    """
    Represents a concept in an XBRL taxonomy.
    """

    __slots__ = (
        "qname",
        "periodType",
        "dataType",
        "baseDataType",
        "typedElement",
        "_labels",
        "_isAbstract",
        "_isDimension",
        "_isHypercube",
        "_isNillable",
        "_isNumeric",
        "_eeDomainMembers",
        "_eeDomainMemberStrings",
        "_qnameMaker",
        "_taxonomy",
    )

    def __init__(self, qnameMaker: QNameMaker, s_qname: str, details: dict):
        self.qname: QName = qnameMaker.fromString(s_qname)
        self._qnameMaker = qnameMaker

        self._labels: Mapping[str, dict[str, str]] = MappingProxyType(details["labels"])
        self._isAbstract: bool = details.get("abstract", False)
        self._isDimension: bool = details.get("dimension", False)
        self._isHypercube: bool = details.get("hypercube", False)
        self._isNillable: bool = details.get("nillable", False)
        self._isNumeric: bool = details.get("numeric", False)
        self._taxonomy: Taxonomy

        if (period_type := details.get("periodType")) is not None:
            self.periodType = PeriodType(period_type)
        else:
            raise TaxonomyException(
                f"Concept {self.qname} does not specify a period type."
            )

        if (data_type := details.get("dataType")) is not None:
            self.dataType = self._qnameMaker.fromString(data_type)
        else:
            raise TaxonomyException(
                f"Concept {self.qname} does not specify a data type."
            )

        if (baseDataType := details.get("baseDataType")) is not None:
            self.baseDataType = self._qnameMaker.fromString(baseDataType)
        else:
            raise TaxonomyException(
                f"Concept {self.qname} does not specify a base data type."
            )

        other = details.get("other", {})

        self.typedElement = None
        if (tElem := other.get("typedElement")) is not None:
            self.typedElement = self._qnameMaker.fromString(tElem)

        self._eeDomainMembers: Optional[list["Concept"]] = None
        self._eeDomainMemberStrings: Optional[list[str]] = None
        if (eeDom := other.get("ee20DomainMembers")) is not None:
            self._eeDomainMemberStrings = eeDom

    def __repr__(self) -> str:
        return f"Concept(qname={self.qname})"

    def __str__(self) -> str:
        return str(self.qname)

    def __lt__(self, other: object) -> bool:
        if isinstance(other, Concept):
            return str(self.qname) < str(other.qname)
        return NotImplemented

    def __eq__(self, other: object) -> bool:
        if self is other:
            return True
        if isinstance(other, Concept):
            return self.qname == other.qname
        return NotImplemented

    def __hash__(self) -> int:
        return hash(self.qname)

    def _reifyUsingTaxonomy(self, taxonomy: "Taxonomy") -> None:
        """Reify any bits of the concept that need the rest of the taxonomy."""
        if getattr(self, "_taxonomy", None) is not None:
            raise TaxonomyException(
                f"Already reified with {self._taxonomy=}. New attempt using {taxonomy=}."
            )
        self._taxonomy = taxonomy
        if self._eeDomainMemberStrings is not None:
            self._eeDomainMembers = [
                taxonomy.getConcept(member) for member in self._eeDomainMemberStrings
            ]
            self._eeDomainMemberStrings = None

    def _getLabelForRole(
        self,
        roleUri: str,
        requestedLanguage: str = "en",
        fallbackLabel: Optional[str] = None,
        fallbackAnyLang: bool = False,
        removeSuffix: bool = False,
    ) -> Optional[str]:
        requestedLanguage = requestedLanguage.lower()
        labels_for_lang: dict[str, str] = {}
        desired_label = None

        if requestedLanguage in self._labels:
            labels_for_lang = self._labels[requestedLanguage]
            desired_label = labels_for_lang.get(roleUri)
        else:
            label_langs = self._labels.keys()
            wanted_lang = requestedLanguage.partition("-")[0]
            for p in label_langs:
                if p.partition("-")[0] == wanted_lang:
                    labels_for_lang = self._labels[p]
                    desired_label = labels_for_lang.get(roleUri)
                    if desired_label:
                        break

        if not desired_label and fallbackAnyLang:
            for d in self._labels.values():
                for role, label in d.items():
                    if role == roleUri:
                        labels_for_lang[role] = label
                        desired_label = labels_for_lang.get(roleUri)
                        if desired_label:
                            break

        if desired_label is None and fallbackLabel is not None:
            desired_label = fallbackLabel

        if not desired_label or not removeSuffix:
            return desired_label

        return LABEL_SUFFIX_PATTERN.sub("", desired_label)

    @overload
    def getStandardLabel(
        self,
        lang: str = "en",
        *,
        fallbackIfMissing: str,
        removeSuffix: bool = ...,
        fallbackAnyLang: bool = ...,
    ) -> str: ...

    @overload
    def getStandardLabel(
        self,
        lang: str = "en",
        *,
        fallbackIfMissing: None = None,
        removeSuffix: bool = ...,
        fallbackAnyLang: bool = ...,
    ) -> Optional[str]: ...

    def getStandardLabel(
        self,
        lang: str = "en",
        *,
        fallbackIfMissing: Optional[str] = None,
        removeSuffix: bool = False,
        fallbackAnyLang: bool = False,
    ) -> Optional[str]:
        return self._getLabelForRole(
            STANDARD_LABEL_ROLE,
            requestedLanguage=lang,
            fallbackLabel=fallbackIfMissing,
            fallbackAnyLang=fallbackAnyLang,
            removeSuffix=removeSuffix,
        )

    def getDocumentationLabel(
        self, lang: str = "en", fallbackIfMissing: Optional[str] = None
    ) -> Optional[str]:
        return self._getLabelForRole(
            DOCUMENTATION_LABEL_ROLE,
            requestedLanguage=lang,
            fallbackLabel=fallbackIfMissing,
        )

    def getMeasurementGuidanceLabel(
        self, lang: str = "en", fallbackIfMissing: Optional[str] = None
    ) -> Optional[str]:
        return self._getLabelForRole(
            MEASUREMENT_GUIDANCE_LABEL_ROLE,
            requestedLanguage=lang,
            fallbackLabel=fallbackIfMissing,
        )

    @cache
    def getRequiredUnitQNames(self) -> Optional[frozenset[QName]]:
        """If there is a valid UTR unitId or a valid unit QName in the
        measurement guidance label of the concept, return the first one found.
        Otherwise return None.
        """
        if not self.isNumeric:
            return None

        measurementLabel = self.getMeasurementGuidanceLabel()
        if not measurementLabel:
            # N.B. Deals with None or empty string
            return None

        allValidUnitQNames = frozenset(
            {u for u in self._taxonomy.UTR.getUnitsForDataType(self.dataType)}
        )
        if not allValidUnitQNames:
            return None

        # Perhaps the label is just a unitId
        if (
            qname := self._taxonomy.UTR.getQNameForUnitId(measurementLabel)
        ) is not None:
            if qname in allValidUnitQNames:
                return frozenset({qname})

        # Perhaps the label is just a unit QNAME
        if self._qnameMaker.isValidQName(measurementLabel):
            qname = self._qnameMaker.fromString(measurementLabel)
            if qname in allValidUnitQNames:
                return frozenset({qname})

        valid: list[QName] = []

        # We might have a measurement label that is a mixture of human readable text and units in []
        between_square_bracket_pattern = re.compile(r"\[([^\]]+)\]")
        content = between_square_bracket_pattern.finditer(measurementLabel)

        for m1 in content:
            for m2 in QNAME_RE.finditer(m1.group(1)):
                s = m2.group(0)
                if self._qnameMaker.isValidQName(s):
                    q = self._qnameMaker.fromString(s)
                    if q in allValidUnitQNames:
                        valid.append(q)

        if not valid:
            # If we're still empty, then let's see if someone has used bare unitIds
            delimiters = [" ", ",", "*", "/"]
            if any(c in delimiters for c in measurementLabel):
                desired = {x for x in NCNAME_RE.findall(measurementLabel)}
                allValidUnitIds = {u.localName: u for u in allValidUnitQNames}
                for d in desired:
                    q2 = allValidUnitIds.get(d)
                    if q2 is not None:
                        valid.append(q2)

        match len(valid):
            case 0:
                return None
            case _:
                return frozenset(valid)

    @property
    def isAbstract(self) -> bool:
        return self._isAbstract

    @property
    def isDimension(self) -> bool:
        return self._isDimension

    @property
    def isHypercube(self) -> bool:
        return self._isHypercube

    @property
    def isTypedDimension(self) -> bool:
        return self.isDimension and self.typedElement is not None

    @property
    def isExplicitDimension(self) -> bool:
        return self.isDimension and not self.isTypedDimension

    @property
    def isReportable(self) -> bool:
        return not self.isAbstract

    @property
    def isMonetary(self) -> bool:
        return self.baseDataType == self._qnameMaker.fromNamespaceAndLocalName(
            XBRLI_NS, "monetaryItemType"
        )

    @property
    def isTextblock(self) -> bool:
        return self.dataType.localName == "textBlockItemType"

    @property
    def isDate(self) -> bool:
        return self.baseDataType == self._qnameMaker.fromNamespaceAndLocalName(
            XBRLI_NS, "dateItemType"
        )

    @property
    def isNumeric(self) -> bool:
        return self._isNumeric

    @property
    def isNillable(self) -> bool:
        return self._isNillable

    @property
    def isBoolean(self) -> bool:
        return self.baseDataType == self._qnameMaker.fromNamespaceAndLocalName(
            XBRLI_NS, "booleanItemType"
        )

    @property
    def isEnumerationSingle(self) -> bool:
        return self.dataType == self._qnameMaker.fromNamespaceAndLocalName(
            ENUM2_NS, "enumerationItemType"
        )

    @property
    def isEnumerationSet(self) -> bool:
        return self.dataType == self._qnameMaker.fromNamespaceAndLocalName(
            ENUM2_NS, "enumerationSetItemType"
        )

    @property
    def expandedName(self) -> str:
        return f"{self.qname.namespace}#{self.qname.localName}"

    def getEEDomain(self) -> list["Concept"]:
        return list(self._eeDomainMembers) if self._eeDomainMembers is not None else []


class Relationship(NamedTuple):
    roleUri: str
    depth: int
    concept: Concept
    preferredLabel: Optional[str] = None

    def getLabel(self, lang: str = "en", removeSuffix: bool = True) -> Optional[str]:
        """Get the label for this relationship's concept."""
        if self.preferredLabel is None:
            return self.concept.getStandardLabel(lang, removeSuffix=removeSuffix)
        return self.concept._getLabelForRole(
            self.preferredLabel, lang, removeSuffix=removeSuffix
        )


class PresentationGroup(NamedTuple):
    roleUri: str
    label: str
    relationships: list[Relationship]
    style: PresentationStyle


class BaseSet(NamedTuple):
    roleUri: str
    hyperCubes: frozenset[Concept]


class Taxonomy:
    def __init__(
        self,
        concepts: dict[str, Concept],
        entryPoint: str,
        presentation: dict[str, dict[str, Any]],
        dimensions: dict[str, dict],
        qnameMaker: QNameMaker,
        utr: UTR,
    ) -> None:
        self._entryPoint = entryPoint
        self._dimensions = dimensions
        self._qnameMaker = qnameMaker
        self._utr = utr
        self._groups = list(presentation.keys())
        self._groupLabels: dict[str, dict[str, str]] = {}
        # https://www.xbrl.org/Specification/xbrl-xml/REC-2021-10-13/xbrl-xml-REC-2021-10-13.html#sec-dimensions
        # "If the report's DTS does not contain any hypercubes, or if
        # dimensional validity can be achieved using either container,
        # <xbrli:scenario> should be used for all dimensions."
        self._dimensionContainer = DimensionContainerType.Scenario

        self._concepts = {concept.qname: concept for concept in concepts.values()}
        for concept in concepts.values():
            concept._reifyUsingTaxonomy(self)

        self._relationships: dict[str, list[Relationship]] = defaultdict(list)
        for roleUri, bits in presentation.items():
            self._groupLabels[roleUri] = bits["labels"]
            for row in bits["rows"]:
                if len(row) == 2:
                    indent, concept_qname = row
                    preferredLabel = None
                else:
                    indent, concept_qname, preferredLabel = row
                concept = self.getConcept(concept_qname)
                self._relationships[roleUri].append(
                    Relationship(roleUri, indent, concept, preferredLabel)
                )

        self._lookupConceptsByName = defaultdict(list)
        for concept in concepts.values():
            self._lookupConceptsByName[concept.qname.localName].append(concept)

        cByStdLbl: dict[str, list[Concept]] = defaultdict(list)
        cByPretend: dict[str, list[Concept]] = defaultdict(list)
        for concept in concepts.values():
            if (label := concept.getStandardLabel()) is not None:
                cByStdLbl[label].append(concept)
                stripped = unicodeDashNormalization(label)
                cByPretend[stripped].append(concept)
                label_no_suffix, _, _ = stripped.rpartition("[")
                label_no_suffix = label_no_suffix.strip()
                cByPretend[label_no_suffix].append(concept)
                cByPretend[label_no_suffix.lower()].append(concept)
        self._lookupConceptsByStandardLabel: dict[str, frozenset[Concept]] = dict(
            (k, frozenset(v)) for k, v in cByStdLbl.items()
        )
        self._lookupConceptsByPretendLabel: dict[str, frozenset[Concept]] = dict(
            (k, frozenset(v)) for k, v in cByPretend.items()
        )

        dimensionDefaults: dict[str, str] = dimensions.pop("_defaults", {})
        self._dimensionDefaults: dict[Concept, Concept] = dict(
            (self.getConcept(dimension), self.getConcept(domainMember))
            for dimension, domainMember in dimensionDefaults.items()
        )

        self._baseSets: dict[BaseSet, list[dict]] = defaultdict(list)
        self._lookupBaseSetByCube: dict[Concept, list[BaseSet]] = defaultdict(list)
        self._lookupBaseSetByPrimaryItem: dict[Concept, list[BaseSet]] = defaultdict(
            list
        )
        desired_containers: set[DimensionContainerType] = set()
        open_hcs: set[Relationship] = set()
        domainByDimension: dict[Concept, list[Concept]] = defaultdict(list)

        for role, cubes in dimensions.items():
            cubeConcepts = frozenset(concepts[c] for c in cubes.keys())
            baseSet = BaseSet(role, cubeConcepts)
            for cubeQname, cubeDetails in cubes.items():
                hc_concept = concepts[cubeQname]
                d: dict[str, Any] = {}

                closed = bool(cubeDetails.pop("xbrldt:closed"))
                d["xbrldt:closed"] = closed
                if not closed:
                    open_hcs.add(Relationship(role, 0, hc_concept))

                container = DimensionContainerType(
                    cubeDetails.pop("xbrldt:contextElement")
                )
                desired_containers.add(container)
                d["xbrldt:contextElement"] = container

                d["primaryItems"] = [
                    Relationship(role, depth, concepts[qname])
                    for depth, qname in cubeDetails.pop("primaryItems", [])
                ]
                for r in d["primaryItems"]:
                    self._lookupBaseSetByPrimaryItem[r.concept].append(baseSet)

                d["explicitDimensions"] = {
                    concepts[dimQname]: frozenset(
                        concepts[member] for member in memberQnameList
                    )
                    for dimQname, memberQnameList in cubeDetails.pop(
                        "explicitDimensions", {}
                    ).items()
                }
                for dimension, memberList in d["explicitDimensions"].items():
                    domainByDimension[dimension].extend(memberList)

                d["typedDimensions"] = [
                    concepts[dimQname]
                    for dimQname in cubeDetails.pop("typedDimensions", [])
                ]

                self._lookupBaseSetByCube[hc_concept].append(baseSet)
                self._baseSets[baseSet].append(d)

        self._lookupDomainByDimension: dict[Concept, frozenset[Concept]] = {
            dimension: frozenset(domainlist)
            for dimension, domainlist in domainByDimension.items()
        }
        self._hypercubes = frozenset(
            c for x in self._baseSets.keys() for c in x.hyperCubes
        )

        if open_hcs:
            # Not supported by mireport (aoix doesn't care)
            te = TaxonomyException(
                f"Unsupported taxonomy: contains ({len(open_hcs)}) open hypercubes."
            )
            oc_str = "\n".join(
                f"{role}\n\t{c.qname}"
                for role, c in sorted(
                    ((x.roleUri, x.concept) for x in open_hcs),
                )
            )
            te.add_note(f"Open hypercubes:\n{oc_str}")
            warnings.warn(UserWarning(te))

        match len(desired_containers):
            case 0:
                pass
            case 1:
                self._dimensionContainer = desired_containers.pop()
            case _:
                # Not supported by mireport or aoix
                raise TaxonomyException(
                    f"Multiple dimension containers specified {desired_containers}. Not currently supported"
                )

    def getConcept(self, qname: QName | str) -> Concept:
        if isinstance(qname, str):
            qname = self._qnameMaker.fromString(qname)
        return self._concepts[qname]

    def getConceptForName(self, name: str) -> Concept | None:
        possible = self._lookupConceptsByName.get(name, [])
        match len(possible):
            case 0:
                return None
            case 1:
                return possible[0]
            case _:
                raise AmbiguousComponentException(
                    f"Ambiguous name specified. Candidates concepts: {', '.join(str(concept.qname) for concept in possible)}"
                )

    def getConceptForLabel(self, label: str) -> Optional[Concept]:
        possible: frozenset[Concept] = self._lookupConceptsByStandardLabel.get(
            label, frozenset()
        )
        if not possible:
            label = unicodeDashNormalization(label)
            possible = self._lookupConceptsByPretendLabel.get(label, frozenset())
        if not possible:
            label = label.lower()
            possible = self._lookupConceptsByPretendLabel.get(label, frozenset())
        match len(possible):
            case 0:
                return None
            case 1:
                return next(iter(possible))
            case _:
                raise AmbiguousComponentException(
                    f"Ambiguous label specified. Candidate concepts: {', '.join(str(concept.qname) for concept in sorted(possible))}"
                )

    @cached_property
    def presentation(self) -> list[PresentationGroup]:
        groups = []
        for role in self._groups:
            rels = self._relationships[role]
            style = self._identifyPresentationStyle(rels)
            groups.append(
                PresentationGroup(
                    roleUri=role,
                    label=self._groupLabels[role]["en"],
                    relationships=rels,
                    style=style,
                )
            )
        return groups

    def _identifyPresentationStyle(self, rels: list[Relationship]) -> PresentationStyle:
        hasHypercubes = any(rel for rel in rels if rel.concept.isHypercube)
        hasReportable = any(rel for rel in rels if rel.concept.isReportable)
        if not hasReportable:
            return PresentationStyle.Empty
        if hasReportable and not hasHypercubes:
            return PresentationStyle.List

        listStyle = False
        tableStyle = False

        inHypercube = [False]
        hypercubeDepth = [0]
        for rel in rels:
            if inHypercube[-1] and (0 == rel.depth or rel.depth < hypercubeDepth[-1]):
                hypercubeDepth.pop()
                inHypercube.pop()
            if rel.concept.isHypercube:
                inHypercube.append(True)
                hypercubeDepth.append(rel.depth)
            if rel.concept.isReportable:
                if inHypercube[-1] and rel.depth >= hypercubeDepth[-1]:
                    tableStyle = True
                else:
                    listStyle = True

        match (tableStyle, listStyle):
            case (True, True):
                return PresentationStyle.Hybrid
            case (True, False):
                return PresentationStyle.Table
            case (False, True):
                return PresentationStyle.List
            case (False, False) | _:
                return PresentationStyle.Empty

    @property
    def hypercubes(self) -> frozenset[Concept]:
        """All the hypercube concepts that participate in the definition linkbase. (Excludes Taxonomy.emptyHypercubes)"""
        return self._hypercubes

    @cached_property
    def emptyHypercubes(self) -> frozenset[Concept]:
        """Hypercube concepts in the DTS that do not feature in the definition linkbase. See also hypercubes."""
        all_hcs = frozenset(c for c in self._concepts.values() if c.isHypercube)
        return all_hcs - self._hypercubes

    def getTypedDimensionsForHypercube(self, hypercube: Concept) -> frozenset[Concept]:
        """This aggregates across all base-sets to give all the dimensions specified for the given hypercube."""
        baseSets = self._lookupBaseSetByCube.get(hypercube)
        if baseSets is None:
            return frozenset()
        typed = {
            td
            for b in baseSets
            for cube in self._baseSets[b]
            for td in cube["typedDimensions"]
        }
        return frozenset(typed)

    def getExplicitDimensionsForHypercube(
        self, hypercube: Concept
    ) -> frozenset[Concept]:
        """This aggregates across all base-sets to give all the dimensions specified for the given hypercube."""
        baseSets = self._lookupBaseSetByCube.get(hypercube)
        if baseSets is None:
            return frozenset()
        explicit = {
            ed
            for b in baseSets
            for cube in self._baseSets[b]
            for ed in cube["explicitDimensions"].keys()
        }
        return frozenset(explicit)

    @cache
    def getDimensionsForHypercube(self, hypercube: Concept) -> frozenset[Concept]:
        baseSets = self._lookupBaseSetByCube.get(hypercube)
        if baseSets is None:
            return frozenset()
        dims: list[Concept] = []
        for b in baseSets:
            for cube in self._baseSets[b]:
                dims.extend(ed for ed in cube["explicitDimensions"].keys())
                dims.extend(td for td in cube["typedDimensions"])
        return frozenset(dims)

    def getPrimaryItemsForHypercube(self, hypercube: Concept) -> frozenset[Concept]:
        """This aggregates across all base-sets to give all the primary items specified for the given hypercube."""
        baseSets = self._lookupBaseSetByCube.get(hypercube)
        if baseSets is None:
            return frozenset()
        primary = {
            r.concept
            for b in baseSets
            for cube in self._baseSets[b]
            for r in cube["primaryItems"]
        }
        return frozenset(primary)

    def _getHypercubesForPrimaryItem(self, primaryItem: Concept) -> frozenset[Concept]:
        baseSets = self._lookupBaseSetByPrimaryItem.get(primaryItem)
        if baseSets is None:
            return frozenset()
        return frozenset(c for x in baseSets for c in x.hyperCubes)

    def getExplicitDimensionsForPrimaryItem(
        self, primaryItem: Concept
    ) -> frozenset[Concept]:
        hcs = self._getHypercubesForPrimaryItem(primaryItem)
        eds = {ed for hc in hcs for ed in self.getExplicitDimensionsForHypercube(hc)}
        return frozenset(eds)

    def getTypedDimensionsForPrimaryItem(
        self, primaryItem: Concept
    ) -> frozenset[Concept]:
        hcs = self._getHypercubesForPrimaryItem(primaryItem)
        tds = {td for hc in hcs for td in self.getTypedDimensionsForHypercube(hc)}
        return frozenset(tds)

    @cache
    def getExplicitDimensionForDomainMember(
        self, primaryItem: Concept, dimensionValue: Concept
    ) -> Optional[Concept]:
        baseSets = self._lookupBaseSetByPrimaryItem.get(primaryItem)
        if baseSets is None:
            return None
        possible: set = set()
        for b in baseSets:
            for cube in self._baseSets[b]:
                for ed, domain in cube["explicitDimensions"].items():
                    if dimensionValue in domain:
                        possible.add(ed)
        match len(possible):
            case 0:
                return None
            case 1:
                return next(iter(possible))
            case _:
                raise AmbiguousComponentException(
                    f"Ambiguous domain member specified. Candidate dimensions: {', '.join(str(concept.qname) for concept in possible)}"
                )

    def getDomainMembersForExplicitDimension(
        self, dimension: Concept
    ) -> frozenset[Concept]:
        """This aggregates across all base-sets to give all the domain members specified for the given dimension."""
        return self._lookupDomainByDimension.get(dimension, frozenset())

    def getDimensionDefault(self, dimension: Concept) -> Optional[Concept]:
        return self._dimensionDefaults.get(dimension)

    @cached_property
    def defaultedDimensions(self) -> frozenset[Concept]:
        return frozenset(self._dimensionDefaults.keys())

    @cached_property
    def dimensionContainer(self) -> DimensionContainerType:
        return self._dimensionContainer

    @cached_property
    def entryPoint(self) -> str:
        return self._entryPoint

    @property
    def namespacePrefixesMap(self) -> Mapping[str, str]:
        return self._qnameMaker.namespacePrefixesMap

    @cached_property
    def _labelLanguageCounter(self) -> Counter[str]:
        """Generate a Counter for languages used in the taxonomy.

        The values are based on the total number of labels in the taxonomy for
        each language."""
        counts = Counter(
            lang.lower() for group in self._groupLabels.values() for lang in group
        )
        counts.update(
            lang.lower()
            for concept in self._concepts.values()
            for lang in concept._labels.keys()
        )
        return counts

    @cached_property
    def defaultLanguage(self) -> str | None:
        """Return the most used language in the taxonomy."""
        counts = self._labelLanguageCounter
        if not counts:
            # no labels at all
            return None
        return counts.most_common(1)[0][0]

    @property
    def supportedLanguages(self) -> frozenset[str]:
        """Return a frozenset of all languages that are used in the taxonomy."""
        return frozenset(self._labelLanguageCounter)

    def getBestSupportedLanguage(self, requestedLanguage: str) -> str | None:
        """Return the best supported language included with the taxonomy for the given requested language.

        @requestedLanguage: Should be as specified in BCP 47. For example, "fr-CH", "en-us", "de"."""
        return getBestSupportedLanguage(
            requestedLanguage, self.supportedLanguages, self.defaultLanguage
        )

    @property
    def UTR(self) -> UTR:
        return self._utr

    @property
    def QNameMaker(self) -> QNameMaker:
        return self._qnameMaker


_TAXONOMIES: dict[str, Taxonomy] = {}


def getTaxonomy(entryPoint: str) -> Taxonomy:
    taxonomy = _TAXONOMIES.get(entryPoint)
    if taxonomy is None:
        raise UnknownTaxonomyException(
            f'No knowledge of taxonomy entry point "{entryPoint}"'
        )
    return taxonomy


def listTaxonomies() -> list[str]:
    return sorted(_TAXONOMIES.keys())


def _loadTaxonomyFromFile(bits: dict) -> None:
    entryPoint = bits["entryPoint"]
    if _TAXONOMIES.get(entryPoint) is not None:
        raise TaxonomyException(
            f"Already loaded taxonomy. Taxonomies loaded: {' '.join(_TAXONOMIES.keys())}"
        )

    qnameMaker = getBootsrapQNameMaker()
    for prefix, namespace in bits["namespaces"].items():
        qnameMaker.addNamespacePrefix(prefix, namespace)

    concepts: dict[str, Concept] = {
        str_qname: Concept(qnameMaker, str_qname, jconcept)
        for str_qname, jconcept in bits["concepts"].items()
    }

    _TAXONOMIES[entryPoint] = Taxonomy(
        concepts,
        entryPoint=entryPoint,
        presentation=bits["presentation"],
        dimensions=bits["dimensions"],
        qnameMaker=qnameMaker,
        utr=UTR.fromDict(
            getObject(getResource(data, "utr.json")), qnameMaker=qnameMaker
        ),
    )
