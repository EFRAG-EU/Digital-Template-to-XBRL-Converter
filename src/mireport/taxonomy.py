"""
This module provides a simple API for querying an XBRL taxonomy including
concept details and presentation networks.
"""

from __future__ import annotations

import logging
import re
import warnings
from collections import Counter, defaultdict
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from enum import Enum, StrEnum, auto
from functools import cache, cached_property
from pathlib import Path
from typing import TYPE_CHECKING, NamedTuple, overload

from mireport.data import registries, taxonomies
from mireport.exceptions import (
    AmbiguousComponentException,
    BrokenQNameException,
    TaxonomyException,
    UnknownTaxonomyException,
    UnsupportedTaxonomyFeatureException,
)
from mireport.json import getJsonFiles, getObject, getResource
from mireport.localise import getBestSupportedLanguage
from mireport.stringutil import normalizeLabelText, stripLabelSuffix
from mireport.typealiases import LabelsByLang
from mireport.utr import UTR
from mireport.xml import (
    ENUM2_NS,
    NCNAME_RE,
    QNAME_RE,
    XBRLI_NS,
    QName,
    QNameMaker,
    getBootstrapQNameMaker,
)

if TYPE_CHECKING:
    from typing import Any, Self

L = logging.getLogger(__name__)

MEASUREMENT_GUIDANCE_LABEL_ROLE = "http://www.xbrl.org/2003/role/measurementGuidance"
STANDARD_LABEL_ROLE = "http://www.xbrl.org/2003/role/label"
DOCUMENTATION_LABEL_ROLE = "http://www.xbrl.org/2003/role/documentation"
TERSE_LABEL_ROLE = "http://www.xbrl.org/2003/role/terseLabel"
VERBOSE_LABEL_ROLE = "http://www.xbrl.org/2003/role/verboseLabel"

TOTAL_LABEL_ROLE = "http://www.xbrl.org/2003/role/totalLabel"
PERIOD_START_LABEL_ROLE = "http://www.xbrl.org/2003/role/periodStartLabel"
PERIOD_END_LABEL_ROLE = "http://www.xbrl.org/2003/role/periodEndLabel"

DEFINITION_GUIDANCE_LABEL_ROLE = "http://www.xbrl.org/2003/role/definitionGuidance"
DISCLOSURE_GUIDANCE_LABEL_ROLE = "http://www.xbrl.org/2003/role/disclosureGuidance"
PRESENTATION_GUIDANCE_LABEL_ROLE = "http://www.xbrl.org/2003/role/presentationGuidance"
MEASUREMENT_GUIDANCE_LABEL_ROLE = "http://www.xbrl.org/2003/role/measurementGuidance"


LABEL_SUFFIX_PATTERN = re.compile(r"\s*\[[A-Z]?[a-z ]+\]\s*$")

ConceptPredicate = Callable[["Concept"], bool]


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
        "_eeDomainMemberStrings",
        "_eeDomainMembers",
        "_isAbstract",
        "_isDimension",
        "_isHypercube",
        "_isNillable",
        "_isNumeric",
        "_labels",
        "_qnameMaker",
        "_taxonomy",
        "baseDataType",
        "dataType",
        "periodType",
        "qname",
        "typedElement",
    )

    def __init__(self, qnameMaker: QNameMaker, s_qname: str, details: dict):
        self.qname: QName = qnameMaker.fromString(s_qname)
        self._qnameMaker = qnameMaker

        self._labels: LabelsByLang = details["labels"]
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

        self._eeDomainMembers: tuple[Concept, ...] | None = None
        self._eeDomainMemberStrings: list[str] | None = None
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

    def _reifyUsingTaxonomy(self, taxonomy: Taxonomy) -> None:
        """Reify any bits of the concept that need the rest of the taxonomy."""
        if getattr(self, "_taxonomy", None) is not None:
            raise TaxonomyException(
                f"Already reified with {self._taxonomy=}. New attempt using {taxonomy=}."
            )
        self._taxonomy = taxonomy
        if self._eeDomainMemberStrings is not None:
            self._eeDomainMembers = tuple(
                taxonomy.getConcept(member) for member in self._eeDomainMemberStrings
            )
            self._eeDomainMemberStrings = None

    def getLabelForRole(
        self,
        roleUri: str,
        requestedLanguage: str | None = None,
        fallbackLabel: str | None = None,
        fallbackToAnyLang: bool = False,
        fallbackToQName: bool = False,
        removeSuffix: bool = False,
    ) -> str | None:
        """Return the label for *roleUri* in the requested language."""
        if (defaultLanguage := self._taxonomy.defaultLanguage) is None:
            return None

        if not requestedLanguage:
            requestedLanguage = defaultLanguage

        requestedLanguage = requestedLanguage.lower()
        labels_for_lang: Mapping[str, str]
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

        if not desired_label and fallbackToAnyLang:
            langBuckets = list(self._labels.values())
            if (
                requestedLanguage != defaultLanguage
                and (defaultBucket := self._labels.get(defaultLanguage)) is not None
            ):
                # prioritise default language
                langBuckets.insert(0, defaultBucket)
            # first hit wins
            for d in langBuckets:
                if wrongLangRightRole := d.get(roleUri):
                    desired_label = wrongLangRightRole
                    break

        if desired_label is None and fallbackLabel is not None:
            desired_label = fallbackLabel

        if desired_label is None and fallbackToQName:
            desired_label = str(self.qname)

        if not desired_label or not removeSuffix:
            return desired_label

        return LABEL_SUFFIX_PATTERN.sub("", desired_label)

    @overload
    def getStandardLabel(
        self,
        lang: str | None = None,
        *,
        fallbackIfMissing: str,
        removeSuffix: bool = ...,
        fallbackToAnyLang: bool = ...,
        fallbackToQName: bool = ...,
    ) -> str: ...

    @overload
    def getStandardLabel(
        self,
        lang: str | None = None,
        *,
        fallbackIfMissing: None = None,
        removeSuffix: bool = ...,
        fallbackToAnyLang: bool = ...,
        fallbackToQName: bool = ...,
    ) -> str | None: ...

    def getStandardLabel(
        self,
        lang: str | None = None,
        *,
        fallbackIfMissing: str | None = None,
        removeSuffix: bool = False,
        fallbackToAnyLang: bool = False,
        fallbackToQName: bool = False,
    ) -> str | None:
        return self.getLabelForRole(
            STANDARD_LABEL_ROLE,
            requestedLanguage=lang,
            fallbackLabel=fallbackIfMissing,
            fallbackToAnyLang=fallbackToAnyLang,
            removeSuffix=removeSuffix,
            fallbackToQName=fallbackToQName,
        )

    def getDocumentationLabel(
        self,
        lang: str | None = None,
        *,
        fallbackIfMissing: str | None = None,
        removeSuffix: bool = False,
        fallbackToAnyLang: bool = False,
        fallbackToQName: bool = False,
    ) -> str | None:
        return self.getLabelForRole(
            DOCUMENTATION_LABEL_ROLE,
            requestedLanguage=lang,
            fallbackLabel=fallbackIfMissing,
            removeSuffix=removeSuffix,
            fallbackToAnyLang=fallbackToAnyLang,
            fallbackToQName=fallbackToQName,
        )

    def _getLabelIterable(
        self,
        labelRole: str | None = None,
        lang: str | None = None,
    ) -> Iterable[str]:
        """
        Yield labels for this concept, optionally filtered by role and/or language.

        Args:
            labelRole: Only yield labels with this role.
            lang: Only consider labels in this language.
        """

        # If a specific language is requested, restrict to that mapping
        if lang is not None:
            labelsByRole = self._labels.get(lang)
            if not labelsByRole:
                return  # no labels for this language
            if labelRole is None:
                yield from labelsByRole.values()
            elif (label := labelsByRole.get(labelRole)) is not None:
                yield label
            return

        # No language filter → iterate all languages
        if labelRole is None:
            # Fast path: yield all labels across all languages
            for labelsByRole in self._labels.values():
                yield from labelsByRole.values()
        else:
            # Filter by role across all languages
            for labelsByRole in self._labels.values():
                if (label := labelsByRole.get(labelRole)) is not None:
                    yield label

    def getAllStandardLabels(self) -> tuple[str, ...]:
        """Return a tuple of all standard labels for this concept."""
        return tuple(self._getLabelIterable(STANDARD_LABEL_ROLE))

    @property
    def labelRoles(self) -> frozenset[str]:
        """All label role URIs for which this concept has at least one label."""
        return frozenset(
            role_uri
            for lang_labels in self._labels.values()
            for role_uri in lang_labels
        )

    # N.B. B019 (cache keeps `self` alive) is not a concern: Concepts belong to a
    # Taxonomy which is kept in a module level registry for the life of the process.
    @cache  # noqa: B019
    def getRequiredUnitQNames(self) -> frozenset[QName] | None:
        """If there is a valid UTR unitId or a valid unit QName in the
        measurement guidance label of the concept, return the first one found.
        Otherwise return None.
        """
        if not self.isNumeric:
            return None

        measurementLabel = self.getLabelForRole(
            MEASUREMENT_GUIDANCE_LABEL_ROLE,
            fallbackToAnyLang=True,
        )
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
        ) is not None and qname in allValidUnitQNames:
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

    def getEEDomain(self) -> tuple[Concept, ...]:
        return tuple(self._eeDomainMembers) if self._eeDomainMembers is not None else ()


class Relationship(NamedTuple):
    roleUri: str
    depth: int
    concept: Concept
    preferredLabel: str | None = None

    def getLabel(
        self,
        requestedLanguage: str | None = None,
        *,
        removeSuffix: bool = True,
        fallbackLabel: str | None = None,
        fallbackToAnyLang: bool = False,
        fallbackToQName: bool = False,
    ) -> str | None:
        """Get the label for this relationship's concept."""
        labelRole = self.preferredLabel or STANDARD_LABEL_ROLE
        return self.concept.getLabelForRole(
            labelRole,
            requestedLanguage,
            removeSuffix=removeSuffix,
            fallbackLabel=fallbackLabel,
            fallbackToAnyLang=fallbackToAnyLang,
            fallbackToQName=fallbackToQName,
        )

    @property
    def isPeriodStart(self) -> bool:
        return self.preferredLabel is not None and "periodStart" in self.preferredLabel

    @property
    def isPeriodEnd(self) -> bool:
        return (
            self.preferredLabel is not None
            and "periodEnd" in self.preferredLabel
            and self.concept.isNumeric
        )

    @property
    def isNegated(self) -> bool:
        return (
            self.concept.isNumeric
            and self.preferredLabel is not None
            and "negated" in self.preferredLabel
            and self.concept.isNumeric
        )


class PresentationGroup(NamedTuple):
    taxonomy: Taxonomy
    style: PresentationStyle
    roleUri: str
    definition: str
    labels: Mapping[str, str]
    relationships: tuple[Relationship, ...]

    def __eq__(self, other: object) -> bool:
        if self is other:
            return True
        if isinstance(other, PresentationGroup):
            return self.roleUri == other.roleUri
        return NotImplemented

    def __lt__(self, other: object) -> bool:
        if isinstance(other, PresentationGroup):
            return (self.definition, self.roleUri) < (other.definition, other.roleUri)
        return NotImplemented

    def getLabel(
        self,
        requestedLanguage: str | None = None,
        *,
        fallbackToDefaultLanguage: bool = True,
        fallbackToDefinition: bool = True,
    ) -> str:
        if requestedLanguage and (label := self.labels.get(requestedLanguage)):
            return label
        if (
            fallbackToDefaultLanguage
            and (default := self.taxonomy.defaultLanguage)
            and (label := self.labels.get(default))
        ):
            return label
        if fallbackToDefinition:
            return self.definition
        return ""

    @classmethod
    def fromJSON(cls, taxonomy: Taxonomy, roleUri: str, metaData: Mapping) -> Self:
        relationships: list[Relationship] = []
        for row in metaData["rows"]:
            if len(row) == 2:
                indent, concept_qname = row
                preferredLabel = None
            else:
                indent, concept_qname, preferredLabel = row
            relationships.append(
                Relationship(
                    roleUri, indent, taxonomy.getConcept(concept_qname), preferredLabel
                )
            )
        return cls(
            taxonomy,
            cls._identifyPresentationStyle(relationships),
            roleUri,
            str(metaData.get("definition", "")).strip(),
            metaData.get("labels", {}),
            tuple(relationships),
        )

    @classmethod
    def _identifyPresentationStyle(
        cls, rels: Iterable[Relationship]
    ) -> PresentationStyle:
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


@dataclass(frozen=True)
class ExplicitDimensionSignature:
    """One explicit dimension and the domain members valid for it within a single
    DimensionSignature."""

    dimension: Concept
    domain: frozenset[Concept]


@dataclass(frozen=True)
class DimensionSignature:
    """One valid dimensional shape a primary item (or hypercube) can take: the
    explicit and typed dimensions declared for one hypercube in one base set.

    A fact never declares which base set/role it belongs to -- that is purely a
    taxonomy-authoring grouping of definition-linkbase arcs, invisible in the
    instance. A fact is dimensionally valid if its dimension values match *at
    least one* DimensionSignature for its concept; checking against the union of
    every signature's dimensions instead can both demand dimensions that were
    never required together and admit member/dimension combinations that were
    never valid together. Use Taxonomy.getValidDimensionsForPrimaryItem() /
    getValidDimensionsForHypercube() to get the applicable signatures, and
    matches() on each to test a candidate set of dimension values.
    """

    roleUri: str
    hypercube: Concept
    closed: bool
    contextElement: DimensionContainerType
    primaryItems: frozenset[Concept]
    explicitDimensions: frozenset[ExplicitDimensionSignature]
    typedDimensions: frozenset[Concept]
    _taxonomy: Taxonomy = field(repr=False, compare=False)

    @cached_property
    def _explicitDimensionsByDimension(self) -> Mapping[Concept, frozenset[Concept]]:
        return {ed.dimension: ed.domain for ed in self.explicitDimensions}

    def __getitem__(self, dimension: Concept) -> frozenset[Concept]:
        """The domain members valid for *dimension* within this signature.
        Raises KeyError if *dimension* is not part of this signature."""
        return self._explicitDimensionsByDimension[dimension]

    def matches(
        self,
        explicitDims: Mapping[Concept, Concept],
        typedDims: Mapping[Concept, str] | Iterable[Concept],
    ) -> bool:
        """True if the given dimension values are a valid instantiation of this
        signature. An explicit dimension may be omitted from *explicitDims* only
        if it has a taxonomy-wide default; any other missing, unexpected, or
        out-of-domain dimension means this signature does not match."""
        typedKeys = (
            frozenset(typedDims.keys())
            if isinstance(typedDims, Mapping)
            else frozenset(typedDims)
        )
        if typedKeys != self.typedDimensions:
            return False

        byDimension = self._explicitDimensionsByDimension
        chosenKeys = frozenset(explicitDims.keys())
        if chosenKeys - frozenset(byDimension.keys()):
            return False  # a dimension was set that isn't part of this signature

        for dimension, domain in byDimension.items():
            chosen = explicitDims.get(dimension)
            if chosen is None:
                if self._taxonomy.getDimensionDefault(dimension) is None:
                    return False  # required and not defaulted, but omitted
            elif chosen not in domain:
                return False
        return True


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
        # https://www.xbrl.org/Specification/xbrl-xml/REC-2021-10-13/xbrl-xml-REC-2021-10-13.html#sec-dimensions
        # "If the report's DTS does not contain any hypercubes, or if
        # dimensional validity can be achieved using either container,
        # <xbrli:scenario> should be used for all dimensions."
        self._dimensionContainer = DimensionContainerType.Scenario

        self._concepts = {concept.qname: concept for concept in concepts.values()}
        for concept in concepts.values():
            concept._reifyUsingTaxonomy(self)

        self._groups: tuple[PresentationGroup, ...] = tuple(
            PresentationGroup.fromJSON(self, roleUri, bits)
            for roleUri, bits in presentation.items()
        )

        self._lookupConceptsByName: dict[str, list[Concept]] = defaultdict(list)
        for concept in concepts.values():
            self._lookupConceptsByName[concept.qname.localName].append(concept)

        cByStdLbl: dict[str, list[Concept]] = defaultdict(list)
        cByPretend: dict[str, list[Concept]] = defaultdict(list)
        for concept in concepts.values():
            for actual_label in concept.getAllStandardLabels():
                cByStdLbl[actual_label].append(concept)

                norm_label = normalizeLabelText(actual_label)
                norm_label_no_suffix = stripLabelSuffix(norm_label)
                norm_label_no_suffix_all_lc = norm_label_no_suffix.lower()

                cByPretend[norm_label].append(concept)
                cByPretend[norm_label_no_suffix].append(concept)
                cByPretend[norm_label_no_suffix_all_lc].append(concept)

        self._lookupConceptsByStandardLabel: dict[str, frozenset[Concept]] = {
            k: frozenset(v) for k, v in cByStdLbl.items()
        }
        self._lookupConceptsByPretendLabel: dict[str, frozenset[Concept]] = {
            k: frozenset(v) for k, v in cByPretend.items()
        }

        self._dimensionDefaults: Mapping[Concept, Concept] = {
            self.getConcept(dimension): self.getConcept(domainMember)
            for dimension, domainMember in dimensions.pop("_defaults", {}).items()
        }

        self._signaturesByHypercube: dict[Concept, list[DimensionSignature]] = (
            defaultdict(list)
        )
        self._signaturesByPrimaryItem: dict[Concept, list[DimensionSignature]] = (
            defaultdict(list)
        )
        desired_containers: set[DimensionContainerType] = set()
        unsupportedRoles: dict[str, str] = {}
        domainByDimension: dict[Concept, list[Concept]] = defaultdict(list)
        self._unsupportedRolesByConcept: dict[Concept, dict[str, str]] = defaultdict(
            dict
        )
        # Every cube in the definition linkbase, whether or not we can model it.
        self._hypercubes = frozenset(
            concepts[cubeQname] for cubes in dimensions.values() for cubeQname in cubes
        )

        for role, cubes in dimensions.items():
            if (defects := self._findBaseSetDefects(cubes)) is not None:
                unsupportedRoles[role] = defects
                # Not modelled at all, so no DimensionSignature and no contribution
                # to the container, domain or dimension-default lookups. Its
                # concepts are recorded only so that using one raises.
                for cubeQname, cubeDetails in cubes.items():
                    for concept in (
                        concepts[cubeQname],
                        *(
                            concepts[qname]
                            for _, qname in cubeDetails.get("primaryItems", [])
                        ),
                    ):
                        self._unsupportedRolesByConcept[concept][role] = defects
                continue

            for cubeQname, cubeDetails in cubes.items():
                hc_concept = concepts[cubeQname]
                closed = bool(cubeDetails.pop("xbrldt:closed"))

                container = DimensionContainerType(
                    cubeDetails.pop("xbrldt:contextElement")
                )
                desired_containers.add(container)

                primaryItemRels = [
                    Relationship(role, depth, concepts[qname])
                    for depth, qname in cubeDetails.pop("primaryItems", [])
                ]

                explicitDimensionsByName = {
                    concepts[dimQname]: frozenset(
                        concepts[member] for member in memberQnameList
                    )
                    for dimQname, memberQnameList in cubeDetails.pop(
                        "explicitDimensions", {}
                    ).items()
                }
                for dimension, memberList in explicitDimensionsByName.items():
                    domainByDimension[dimension].extend(memberList)
                explicitDimensions = frozenset(
                    ExplicitDimensionSignature(dimension=dimension, domain=domain)
                    for dimension, domain in explicitDimensionsByName.items()
                )

                typedDimensions = frozenset(
                    concepts[dimQname]
                    for dimQname in cubeDetails.pop("typedDimensions", [])
                )

                # One DimensionSignature per (role, hypercube) -- this is the unit
                # a fact must satisfy at least one of, never the union of several.
                signature = DimensionSignature(
                    roleUri=role,
                    hypercube=hc_concept,
                    closed=closed,
                    contextElement=container,
                    primaryItems=frozenset(r.concept for r in primaryItemRels),
                    explicitDimensions=explicitDimensions,
                    typedDimensions=typedDimensions,
                    _taxonomy=self,
                )
                self._signaturesByHypercube[hc_concept].append(signature)
                for r in primaryItemRels:
                    self._signaturesByPrimaryItem[r.concept].append(signature)

        self._lookupDomainByDimension: Mapping[Concept, frozenset[Concept]] = {
            dimension: frozenset(domainlist)
            for dimension, domainlist in domainByDimension.items()
        }

        if unsupportedRoles:
            # Warn rather than raise so the rest of the taxonomy stays usable.
            # Touching one of these base sets raises -- see _rejectUnsupported().
            te = TaxonomyException(
                f"Unsupported taxonomy [{entryPoint}] contains ({len(unsupportedRoles)}) "
                "base sets that mireport cannot model."
            )
            te.add_note(
                "Unsupported base sets:\n"
                + "\n".join(
                    f"{role}\n\t{reason}"
                    for role, reason in sorted(unsupportedRoles.items())
                )
            )
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

    @staticmethod
    def _findBaseSetDefects(cubes: Mapping[str, Mapping]) -> str | None:
        """Describe why mireport cannot model this base set, or None if it can.

        Neither defect is fatal on its own -- see the warning in __init__ -- but a
        base set carrying one cannot be reasoned about, because both break the
        assumption that a base set contributes exactly one closed dimensional
        shape that a fact either matches or does not.
        """
        defects: list[str] = []
        if len(cubes) > 1:
            defects.append(
                f"{len(cubes)} hypercubes in one base set "
                f"({', '.join(sorted(cubes))}); only one is supported"
            )
        defects.extend(
            f"hypercube {cubeQname} is open"
            for cubeQname, cubeDetails in cubes.items()
            if not cubeDetails["xbrldt:closed"]
        )
        return "; ".join(defects) if defects else None

    def _rejectUnsupported(self, subject: Concept) -> None:
        if faulty := self._unsupportedRolesByConcept.get(subject):
            raise UnsupportedTaxonomyFeatureException(
                f"{subject.qname} is declared in base set(s) that mireport cannot "
                "model: "
                + "; ".join(
                    f"{role} ({reason})" for role, reason in sorted(faulty.items())
                )
            )

    def getConcept(self, qname: QName | str) -> Concept:
        if isinstance(qname, str):
            qname = self._qnameMaker.fromString(qname)
        return self._concepts[qname]

    def resolveConcept(
        self,
        text: str,
        *,
        by_label: bool = False,
        by_name: bool = False,
        by_qname: bool = False,
        only_reportable: bool = True,
        predicate: ConceptPredicate | None = None,
    ) -> Concept | None:
        """Resolve a string to a Concept using one or more strategies.

        Strategies are tried in specificity order: qname → name → label.
        Candidates are filtered before ambiguity checking, so filters
        participate in disambiguation: when only_reportable=True (the default),
        non-reportable (abstract) candidates are dropped — a label shared by
        one reportable and several abstract concepts resolves unambiguously
        rather than raising — and a caller-supplied predicate narrows the
        candidates the same way (both filters compose). Exceptions raised by
        the predicate itself propagate to the caller.
        Returns the first match, or None if all enabled strategies find nothing.
        Raises AmbiguousComponentException if multiple candidates survive filtering.
        Raises ValueError if no strategy is enabled.
        """
        if not (by_label or by_name or by_qname):
            raise ValueError(
                "resolveConcept requires at least one strategy to be enabled"
            )

        def passes(c: Concept) -> bool:
            return (not only_reportable or c.isReportable) and (
                predicate is None or predicate(c)
            )

        if by_qname:
            try:
                concept = self.getConcept(text)
            except (BrokenQNameException, KeyError):
                pass  # not a valid QName format or concept not present
            else:
                if passes(concept):
                    return concept

        candidates: set[Concept] = set()

        if by_name:
            candidates.update(self._lookupConceptsByName.get(text, []))

        if by_label:
            possible: frozenset[Concept] = self._lookupConceptsByStandardLabel.get(
                text, frozenset()
            )
            if not possible:
                normalized = normalizeLabelText(text)
                possible = self._lookupConceptsByPretendLabel.get(
                    normalized, frozenset()
                )
                if not possible:
                    no_suffix = stripLabelSuffix(normalized)
                    if no_suffix != normalized:
                        possible = self._lookupConceptsByPretendLabel.get(
                            no_suffix, frozenset()
                        )
                    if not possible:
                        possible = self._lookupConceptsByPretendLabel.get(
                            no_suffix.lower(), frozenset()
                        )
            candidates.update(possible)

        candidates = {c for c in candidates if passes(c)}

        match len(candidates):
            case 0:
                return None
            case 1:
                return next(iter(candidates))
            case _:
                ordered = sorted(candidates)
                raise AmbiguousComponentException(
                    f"Ambiguous concept specified. Candidate concepts: "
                    f"{', '.join(str(c.qname) for c in ordered)}",
                    candidates=ordered,
                )

    @cached_property
    def concepts(self) -> frozenset[Concept]:
        """All concepts in the taxonomy."""
        return frozenset(self._concepts.values())

    @property
    def presentation(self) -> tuple[PresentationGroup, ...]:
        return self._groups

    @property
    def hypercubes(self) -> frozenset[Concept]:
        """All the hypercube concepts that participate in the definition linkbase. (Excludes Taxonomy.emptyHypercubes)"""
        return self._hypercubes

    @cached_property
    def emptyHypercubes(self) -> frozenset[Concept]:
        """Hypercube concepts in the DTS that do not feature in the definition linkbase. See also hypercubes."""
        all_hcs = frozenset(c for c in self._concepts.values() if c.isHypercube)
        return all_hcs - self._hypercubes

    def getValidDimensionsForHypercube(
        self, hypercube: Concept
    ) -> frozenset[DimensionSignature]:
        """All the DimensionSignatures declared for this hypercube, one per base
        set it participates in. A fact must satisfy at least one of these -- not
        their union.

        Raises UnsupportedTaxonomyFeatureException if this hypercube is declared
        in a base set mireport cannot model."""
        self._rejectUnsupported(hypercube)
        return frozenset(self._signaturesByHypercube.get(hypercube, ()))

    def getValidDimensionsForPrimaryItem(
        self, primaryItem: Concept
    ) -> frozenset[DimensionSignature]:
        """All the DimensionSignatures a primary item can be reported against, one
        per (base set, hypercube) it participates in as a primary item. A fact
        must satisfy at least one of these -- not their union.

        Raises UnsupportedTaxonomyFeatureException if this primary item is declared
        in a base set mireport cannot model."""
        self._rejectUnsupported(primaryItem)
        return frozenset(self._signaturesByPrimaryItem.get(primaryItem, ()))

    def getTypedDimensionsForHypercube(self, hypercube: Concept) -> frozenset[Concept]:
        """The union, across every base-set this hypercube participates in, of its
        typed dimensions. This is not a valid dimensional signature by itself --
        see getValidDimensionsForHypercube()."""
        return frozenset(
            td
            for signature in self.getValidDimensionsForHypercube(hypercube)
            for td in signature.typedDimensions
        )

    def getExplicitDimensionsForHypercube(
        self, hypercube: Concept
    ) -> frozenset[Concept]:
        """The union, across every base-set this hypercube participates in, of its
        explicit dimensions. This is not a valid dimensional signature by itself --
        see getValidDimensionsForHypercube()."""
        return frozenset(
            ed.dimension
            for signature in self.getValidDimensionsForHypercube(hypercube)
            for ed in signature.explicitDimensions
        )

    @cache  # noqa: B019 - Taxonomy lives for the life of the process. See above.
    def getDimensionsForHypercube(self, hypercube: Concept) -> frozenset[Concept]:
        """The union, across every base-set this hypercube participates in, of all
        its dimensions (explicit and typed). This is not a valid dimensional
        signature by itself -- see getValidDimensionsForHypercube()."""
        return self.getExplicitDimensionsForHypercube(
            hypercube
        ) | self.getTypedDimensionsForHypercube(hypercube)

    def getPrimaryItemsForHypercube(self, hypercube: Concept) -> frozenset[Concept]:
        """This aggregates across all base-sets to give all the primary items specified for the given hypercube."""
        return frozenset(
            primaryItem
            for signature in self.getValidDimensionsForHypercube(hypercube)
            for primaryItem in signature.primaryItems
        )

    def getExplicitDimensionsForPrimaryItem(
        self, primaryItem: Concept
    ) -> frozenset[Concept]:
        """The union, across every applicable hypercube/base-set, of the explicit
        dimensions a primary item can carry. This is not a valid dimensional
        signature by itself -- see getValidDimensionsForPrimaryItem()."""
        return frozenset(
            ed.dimension
            for signature in self.getValidDimensionsForPrimaryItem(primaryItem)
            for ed in signature.explicitDimensions
        )

    def getTypedDimensionsForPrimaryItem(
        self, primaryItem: Concept
    ) -> frozenset[Concept]:
        """The union, across every applicable hypercube/base-set, of the typed
        dimensions a primary item can carry. This is not a valid dimensional
        signature by itself -- see getValidDimensionsForPrimaryItem()."""
        return frozenset(
            td
            for signature in self.getValidDimensionsForPrimaryItem(primaryItem)
            for td in signature.typedDimensions
        )

    @cache  # noqa: B019 - Taxonomy lives for the life of the process. See above.
    def getExplicitDimensionForDomainMember(
        self, primaryItem: Concept, dimensionValue: Concept
    ) -> Concept | None:
        possible: set[Concept] = {
            ed.dimension
            for signature in self.getValidDimensionsForPrimaryItem(primaryItem)
            for ed in signature.explicitDimensions
            if dimensionValue in ed.domain
        }
        match len(possible):
            case 0:
                return None
            case 1:
                return next(iter(possible))
            case _:
                ordered = sorted(possible)
                raise AmbiguousComponentException(
                    f"Ambiguous domain member specified. Candidate dimensions: "
                    f"{', '.join(str(concept.qname) for concept in ordered)}",
                    candidates=ordered,
                )

    def getDomainMembersForExplicitDimension(
        self, dimension: Concept
    ) -> frozenset[Concept]:
        """This aggregates across all base-sets to give all the domain members specified for the given dimension."""
        return self._lookupDomainByDimension.get(dimension, frozenset())

    def getDimensionDefault(self, dimension: Concept) -> Concept | None:
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
            lang.lower() for group in self._groups for lang in group.labels
        )
        counts.update(
            lang.lower()
            for concept in self._concepts.values()
            for lang in concept._labels
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


def listTaxonomies() -> tuple[str, ...]:
    return tuple(_TAXONOMIES.keys())


def loadBuiltInTaxonomyJSON() -> None:
    """Loads the taxonomies, unit registry and other models."""
    for f in getJsonFiles(taxonomies):
        try:
            _createTaxonomyFromJSON(getObject(f))
        except Exception as e:  # noqa: BLE001 - one bad file must not lose the rest
            L.error(f"Error loading taxonomy from {f.name}", exc_info=e)


def loadTaxonomyJSON(source: Path | dict) -> Taxonomy:
    """Load one taxonomy from JSON that is not built in, and return it.

    source may be a path to a file written by mireport.arelle.taxonomy_info, or
    an already parsed dict. This is the counterpart to loadBuiltInTaxonomyJSON()
    for callers that have just baked a taxonomy of their own: it registers the
    taxonomy under its own entry point, so getTaxonomy() finds it afterwards.

    Unlike loadBuiltInTaxonomyJSON(), failures are raised rather than logged --
    there is only one taxonomy here, so there is no rest of the batch to save.
    """
    bits = source if isinstance(source, dict) else getObject(source)
    _createTaxonomyFromJSON(bits)
    return getTaxonomy(bits["entryPoint"])


def _createTaxonomyFromJSON(bits: dict) -> None:
    entryPoint = bits["entryPoint"]
    if _TAXONOMIES.get(entryPoint) is not None:
        raise TaxonomyException(
            f"Already loaded taxonomy. Taxonomies loaded: {' '.join(_TAXONOMIES.keys())}"
        )

    qnameMaker = getBootstrapQNameMaker()
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
            getObject(getResource(registries, "utr.json")), qnameMaker=qnameMaker
        ),
    )
