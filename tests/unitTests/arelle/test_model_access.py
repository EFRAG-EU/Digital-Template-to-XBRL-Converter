"""Unit tests for the typed Arelle model-access facade.

Success paths that require genuine Arelle lxml-backed objects (ModelConcept,
ModelResource) are covered end-to-end by
tests/integrationTests/test_taxonomy_info_regeneration.py; these tests cover
the narrowing/consistency logic using lightweight stubs.
"""

from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import MagicMock

import pytest
from arelle import XbrlConst
from arelle.ModelDtsObject import ModelConcept, ModelRoleType
from arelle.ModelValue import QName
from arelle.ModelXbrl import ModelXbrl

from mireport.arelle.model_access import (
    ConceptRelationship,
    ConceptRelationshipSet,
    ValidatedModel,
    qnameOf,
)
from mireport.arelle.support import ArelleModelInconsistency


def qn(local: str = "Thing", ns: str = "https://example.com/vsme") -> QName:
    return QName("vsme", ns, local)


class StubType:
    def __init__(self, qname: QName | None) -> None:
        self.qname = qname


class StubConcept:
    def __init__(
        self,
        qname: QName | None = None,
        *,
        isItem: bool = True,
        type: StubType | None = None,
        typeQname: QName | None = None,
        baseXbrliTypeQname: QName | None = None,
        baseXsdType: str = "anyType",
        typedDomainElement: Any = None,
    ) -> None:
        self.qname = qname
        self.isItem = isItem
        self.type = type
        self.typeQname = typeQname
        self.baseXbrliTypeQname = baseXbrliTypeQname
        self.baseXsdType = baseXsdType
        self.typedDomainElement = typedDomainElement


class StubRel:
    def __init__(
        self,
        toModelObject: Any = None,
        *,
        consecutiveLinkrole: str | None = "https://example.com/elr",
        isUsable: bool = True,
        preferredLabel: str | None = None,
        contextElement: str | None = None,
        isClosed: bool = False,
        order: float = 1.0,
        arcrole: str | None = "https://example.com/arcrole",
        linkrole: str = "https://example.com/elr",
    ) -> None:
        self.arcrole = arcrole
        self.linkrole = linkrole
        self.toModelObject = toModelObject
        self.consecutiveLinkrole = consecutiveLinkrole
        self.isUsable = isUsable
        self.preferredLabel = preferredLabel
        self.contextElement = contextElement
        self.isClosed = isClosed
        self.order = order


class StubRelSet:
    def __init__(
        self,
        linkrole: Any = "https://example.com/elr",
        roots: list[Any] | None = None,
        fromMap: dict[int, list[StubRel]] | None = None,
        toMap: dict[int, list[StubRel]] | None = None,
    ) -> None:
        self.linkrole = linkrole
        self.rootConcepts = roots if roots is not None else []
        self._fromMap = fromMap or {}
        self._toMap = toMap or {}

    def fromModelObject(self, obj: Any) -> list[StubRel]:
        return self._fromMap.get(id(obj), [])

    def toModelObject(self, obj: Any) -> list[StubRel]:
        return self._toMap.get(id(obj), [])


class StubModelXbrl:
    def __init__(
        self,
        relSets: dict[tuple[Any, Any], StubRelSet] | None = None,
        baseSets: dict[tuple[Any, Any, Any, Any], Any] | None = None,
        qnameConcepts: dict[QName, Any] | None = None,
        roleTypes: dict[str, list[Any]] | None = None,
    ) -> None:
        self._relSets = relSets or {}
        self.baseSets = baseSets or {}
        self.qnameConcepts = qnameConcepts or {}
        self.roleTypes = roleTypes or {}
        self.relationshipSetCalls: list[tuple[Any, Any]] = []

    def relationshipSet(self, arcrole: Any, linkrole: Any = None) -> StubRelSet:
        self.relationshipSetCalls.append((arcrole, linkrole))
        return self._relSets[(arcrole, linkrole)]


def makeModel(stub: StubModelXbrl) -> ValidatedModel:
    return ValidatedModel(cast(ModelXbrl, stub))


class TestQnameOf:
    def test_returns_qname(self) -> None:
        q = qn()
        assert qnameOf(cast(ModelConcept, StubConcept(q))) is q

    def test_raises_on_missing_qname(self) -> None:
        with pytest.raises(ArelleModelInconsistency):
            qnameOf(cast(ModelConcept, StubConcept(None)))

    def test_accepts_missing_prefix(self) -> None:
        # A prefix-less QName just means the source document used a default
        # namespace declaration; canonicalisation assigns a prefix later.
        q = QName(None, "https://ns", "Thing")
        assert qnameOf(cast(ModelConcept, StubConcept(q))) is q

    def test_raises_on_missing_namespace(self) -> None:
        with pytest.raises(
            ArelleModelInconsistency, match='elementFormDefault="qualified"'
        ):
            qnameOf(cast(ModelConcept, StubConcept(QName("vsme", None, "Thing"))))

    def test_error_includes_concept_context(self) -> None:
        with pytest.raises(ArelleModelInconsistency, match="of concept"):
            qnameOf(cast(ModelConcept, StubConcept(QName("vsme", None, "Thing"))))


class TestConceptRelationship:
    def test_raises_on_none_target(self) -> None:
        with pytest.raises(ArelleModelInconsistency):
            ConceptRelationship.fromArelle(cast(Any, StubRel(toModelObject=None)))

    def test_raises_on_non_concept_target(self) -> None:
        with pytest.raises(ArelleModelInconsistency):
            ConceptRelationship.fromArelle(
                cast(Any, StubRel(toModelObject=StubConcept(qn())))
            )

    def test_raises_on_none_arcrole(self) -> None:
        # ModelRelationship.arcrole is typed str | None (a bare xlink:arc has
        # no required xlink:arcrole), but ConceptRelationship.arcrole is not
        # optional -- guard it the same way consecutiveLinkrole is guarded
        # above, rather than assigning None into a str field.
        target = MagicMock(spec=ModelConcept)
        target.qname = qn()
        with pytest.raises(ArelleModelInconsistency):
            ConceptRelationship.fromArelle(
                cast(Any, StubRel(toModelObject=target, arcrole=None))
            )

    def test_carries_arcrole_through(self) -> None:
        # isinstance(target, ModelConcept) must hold for _asConcept() to accept
        # it -- StubConcept doesn't satisfy that (see test_raises_on_non_concept_
        # target above), so use a spec'd Mock, which does.
        target = MagicMock(spec=ModelConcept)
        target.qname = qn()
        rel = ConceptRelationship.fromArelle(
            cast(Any, StubRel(toModelObject=target, arcrole=XbrlConst.notAll))
        )
        assert rel.arcrole == XbrlConst.notAll


class TestConceptRelationshipSet:
    ARCROLE = XbrlConst.domainMember
    ELR = "https://example.com/elr"

    def makeSet(
        self, relSet: StubRelSet
    ) -> tuple[ConceptRelationshipSet, StubModelXbrl]:
        stub = StubModelXbrl(relSets={(self.ARCROLE, self.ELR): relSet})
        model = makeModel(stub)
        return model.conceptRelationshipSet(self.ARCROLE, self.ELR), stub

    def test_linkrole_returns_str(self) -> None:
        crs, _ = self.makeSet(StubRelSet(linkrole=self.ELR))
        assert crs.linkrole == self.ELR

    def test_linkrole_raises_on_non_str(self) -> None:
        crs, _ = self.makeSet(StubRelSet(linkrole=None))
        with pytest.raises(ArelleModelInconsistency):
            _ = crs.linkrole

    def test_root_concepts_empty(self) -> None:
        crs, _ = self.makeSet(StubRelSet(roots=[]))
        assert crs.rootConcepts() == []

    def test_root_concepts_raises_on_non_concept(self) -> None:
        crs, _ = self.makeSet(StubRelSet(roots=[StubConcept(qn())]))
        with pytest.raises(ArelleModelInconsistency):
            crs.rootConcepts()

    def test_relationships_from_empty(self) -> None:
        crs, _ = self.makeSet(StubRelSet())
        assert crs.relationshipsFrom(cast(ModelConcept, StubConcept(qn()))) == []

    def test_relationships_from_raises_on_bad_target(self) -> None:
        source = StubConcept(qn("Parent"))
        relSet = StubRelSet(
            fromMap={id(source): [StubRel(toModelObject=StubConcept(qn("Child")))]}
        )
        crs, _ = self.makeSet(relSet)
        with pytest.raises(ArelleModelInconsistency):
            crs.relationshipsFrom(cast(ModelConcept, source))

    def test_has_relationships(self) -> None:
        source = StubConcept(qn("Parent"))
        target = StubConcept(qn("Child"))
        relSet = StubRelSet(
            fromMap={id(source): [StubRel(toModelObject=target)]},
            toMap={id(target): [StubRel(toModelObject=target)]},
        )
        crs, _ = self.makeSet(relSet)
        assert crs.hasRelationshipsFrom(cast(ModelConcept, source)) is True
        assert crs.hasRelationshipsFrom(cast(ModelConcept, target)) is False
        assert crs.hasRelationshipsTo(cast(ModelConcept, target)) is True
        assert crs.hasRelationshipsTo(cast(ModelConcept, source)) is False

    def test_consecutive_set_same_linkrole_returns_self(self) -> None:
        crs, _stub = self.makeSet(StubRelSet(linkrole=self.ELR))
        rel = ConceptRelationship(
            target=cast(ModelConcept, StubConcept(qn())),
            targetQName=qn(),
            arcrole=self.ARCROLE,
            consecutiveLinkrole=self.ELR,
            isUsable=True,
            preferredLabel=None,
            contextElement=None,
            isClosed=False,
        )
        assert crs.consecutiveSet(rel) is crs

    def test_consecutive_set_new_linkrole_builds_new_set(self) -> None:
        otherElr = "https://example.com/other-elr"
        relSet = StubRelSet(linkrole=self.ELR)
        otherRelSet = StubRelSet(linkrole=otherElr)
        stub = StubModelXbrl(
            relSets={
                (self.ARCROLE, self.ELR): relSet,
                (self.ARCROLE, otherElr): otherRelSet,
            }
        )
        model = makeModel(stub)
        crs = model.conceptRelationshipSet(self.ARCROLE, self.ELR)
        rel = ConceptRelationship(
            target=cast(ModelConcept, StubConcept(qn())),
            targetQName=qn(),
            arcrole=self.ARCROLE,
            consecutiveLinkrole=otherElr,
            isUsable=True,
            preferredLabel=None,
            contextElement=None,
            isClosed=False,
        )
        consecutive = crs.consecutiveSet(rel)
        assert consecutive is not crs
        assert consecutive.linkrole == otherElr
        assert (self.ARCROLE, otherElr) in stub.relationshipSetCalls


class TestLinkrolesFor:
    ARCROLE = XbrlConst.parentChild
    LINKQNAME = qn("link")
    ARCQNAME = qn("arc")

    def test_filters_and_dedups(self) -> None:
        elr1 = "https://example.com/elr1"
        elr2 = "https://example.com/elr2"
        baseSets: dict[tuple[Any, Any, Any, Any], Any] = {
            (self.ARCROLE, elr1, self.LINKQNAME, self.ARCQNAME): [],
            # aggregate entries with None components must be ignored
            (self.ARCROLE, elr1, None, None): [],
            (self.ARCROLE, None, self.LINKQNAME, self.ARCQNAME): [],
            # other arcroles must be ignored
            (XbrlConst.summationItem, elr2, self.LINKQNAME, self.ARCQNAME): [],
            (self.ARCROLE, elr2, self.LINKQNAME, self.ARCQNAME): [],
            # duplicate (different arc qname) must not repeat elr1
            (self.ARCROLE, elr1, self.LINKQNAME, qn("otherArc")): [],
        }
        model = makeModel(StubModelXbrl(baseSets=baseSets))
        assert model.linkrolesFor(self.ARCROLE) == [elr1, elr2]

    def test_multiple_arcroles(self) -> None:
        elr = "https://example.com/elr"
        baseSets: dict[tuple[Any, Any, Any, Any], Any] = {
            (XbrlConst.all, elr, self.LINKQNAME, self.ARCQNAME): [],
        }
        model = makeModel(StubModelXbrl(baseSets=baseSets))
        assert model.linkrolesFor(XbrlConst.all, XbrlConst.notAll) == [elr]
        assert model.linkrolesFor(XbrlConst.notAll) == []


class TestBaseSetsInDTS:
    LINKQNAME = qn("link")
    ARCQNAME = qn("arc")

    def test_filters_and_dedups(self) -> None:
        elr1 = "https://example.com/elr1"
        elr2 = "https://example.com/elr2"
        baseSets: dict[tuple[Any, Any, Any, Any], Any] = {
            (XbrlConst.parentChild, elr1, self.LINKQNAME, self.ARCQNAME): [],
            # aggregate entries with None components must be ignored
            (XbrlConst.parentChild, elr1, None, None): [],
            (XbrlConst.parentChild, None, self.LINKQNAME, self.ARCQNAME): [],
            (XbrlConst.summationItem, elr2, self.LINKQNAME, self.ARCQNAME): [],
            # duplicate (different arc qname) must not repeat (parentChild, elr1)
            (XbrlConst.parentChild, elr1, self.LINKQNAME, qn("otherArc")): [],
        }
        model = makeModel(StubModelXbrl(baseSets=baseSets))
        assert model.baseSetsInDTS() == [
            (XbrlConst.parentChild, elr1),
            (XbrlConst.summationItem, elr2),
        ]

    def test_no_base_sets_is_empty(self) -> None:
        model = makeModel(StubModelXbrl(baseSets={}))
        assert model.baseSetsInDTS() == []


class TestValidatedModel:
    def test_concept_returns(self) -> None:
        q = qn()
        concept = StubConcept(q)
        model = makeModel(StubModelXbrl(qnameConcepts={q: concept}))
        assert model.concept(q) is concept

    def test_concept_raises_on_unknown(self) -> None:
        model = makeModel(StubModelXbrl())
        with pytest.raises(ArelleModelInconsistency):
            model.concept(qn("Unknown"))

    def test_concept_count(self) -> None:
        q = qn()
        model = makeModel(StubModelXbrl(qnameConcepts={q: StubConcept(q)}))
        assert model.conceptCount == 1

    def test_item_concepts_yields_and_skips(self) -> None:
        itemQName = qn("Item")
        nonItemQName = qn("NonItem")
        xbrliQName = QName("xbrli", XbrlConst.xbrli, "item")
        xbrldtQName = QName("xbrldt", XbrlConst.xbrldt, "hypercubeItem")
        item = StubConcept(itemQName)
        model = makeModel(
            StubModelXbrl(
                qnameConcepts={
                    itemQName: item,
                    nonItemQName: StubConcept(nonItemQName, isItem=False),
                    xbrliQName: StubConcept(xbrliQName),
                    xbrldtQName: StubConcept(xbrldtQName),
                }
            )
        )
        assert list(model.itemConcepts()) == [(itemQName, item)]

    def test_item_concepts_raises_on_missing_qname(self) -> None:
        q = qn()
        model = makeModel(StubModelXbrl(qnameConcepts={q: StubConcept(None)}))
        with pytest.raises(ArelleModelInconsistency):
            list(model.itemConcepts())

    def test_type_qnames_of(self) -> None:
        typeQName = qn("myType")
        baseQName = QName("xbrli", XbrlConst.xbrli, "stringItemType")
        concept = StubConcept(
            qn(), type=StubType(typeQName), baseXbrliTypeQname=baseQName
        )
        model = makeModel(StubModelXbrl())
        assert model.typeQNamesOf(cast(ModelConcept, concept)) == (
            typeQName,
            baseQName,
        )

    def test_type_qnames_of_accepts_missing_prefix(self) -> None:
        typeQName = QName(None, "https://ns", "myType")
        concept = StubConcept(
            qn(), type=StubType(typeQName), baseXbrliTypeQname=qn("base")
        )
        model = makeModel(StubModelXbrl())
        assert model.typeQNamesOf(cast(ModelConcept, concept))[0] is typeQName

    @pytest.mark.parametrize(
        "type_,base",
        [
            (None, qn("base")),
            (StubType(None), qn("base")),
            (StubType(qn("myType")), None),
            # all QNames must have a namespace (prefixes are optional; they
            # get assigned during canonicalisation)
            (StubType(qn("myType")), QName("xbrli", None, "stringItemType")),
        ],
    )
    def test_type_qnames_of_raises(
        self, type_: StubType | None, base: QName | None
    ) -> None:
        concept = StubConcept(qn(), type=type_, baseXbrliTypeQname=base)
        model = makeModel(StubModelXbrl())
        with pytest.raises(ArelleModelInconsistency):
            model.typeQNamesOf(cast(ModelConcept, concept))

    def test_type_qnames_of_typed_domain_element_resolves_a_named_xsd_type(
        self,
    ) -> None:
        # A taxonomy-defined named type (e.g. a restriction of xs:string):
        # the declared type is its own name; the base comes from
        # element.baseXsdType (Arelle's own XSD-only derivation walk, never
        # an xbrli:*ItemType -- a typed domain element can never be of xbrli
        # item type in the first place, XBRL Dimensions 1.0 3.1.9.2 forbids
        # it -- which is why baseXbrliTypeQname is the wrong tool here).
        typeQName = qn("SiteIdentifierType")
        concept = StubConcept(qn(), typeQname=typeQName, baseXsdType="string")
        model = makeModel(StubModelXbrl())
        dataType, baseDataType = model.typeQNamesOfTypedDomainElement(
            cast(ModelConcept, concept)
        )
        assert dataType == typeQName
        assert baseDataType.localName == "string"
        assert baseDataType.namespaceURI == XbrlConst.xsd

    def test_type_qnames_of_typed_domain_element_resolves_a_bare_xsd_primitive(
        self,
    ) -> None:
        # type="xs:string" directly (not a taxonomy-defined named type) is
        # exactly how both ESRS's and VSME's own typed-dimension domain
        # elements are declared.
        concept = StubConcept(
            qn(),
            typeQname=QName("xsd", XbrlConst.xsd, "string"),
            baseXsdType="string",
        )
        model = makeModel(StubModelXbrl())
        dataType, baseDataType = model.typeQNamesOfTypedDomainElement(
            cast(ModelConcept, concept)
        )
        assert dataType == baseDataType
        assert dataType.localName == "string"
        assert dataType.namespaceURI == XbrlConst.xsd

    def test_type_qnames_of_typed_domain_element_falls_back_when_untyped(
        self,
    ) -> None:
        # No type attribute and no inline type at all (concept.typeQname is
        # None) is legal XML Schema (implicit xs:anyType) -- not observed in
        # ESRS or VSME, but handled for completeness.
        concept = StubConcept(qn(), typeQname=None, baseXsdType="anyType")
        model = makeModel(StubModelXbrl())
        dataType, baseDataType = model.typeQNamesOfTypedDomainElement(
            cast(ModelConcept, concept)
        )
        assert dataType.localName == "anyType"
        assert dataType.namespaceURI == XbrlConst.xsd
        assert baseDataType.localName == "anyType"
        assert baseDataType.namespaceURI == XbrlConst.xsd

    def test_typed_domain_element_of(self) -> None:
        domainElement = StubConcept(qn("myDomain"))
        concept = StubConcept(qn(), typedDomainElement=domainElement)
        model = makeModel(StubModelXbrl())
        assert model.typedDomainElementOf(cast(ModelConcept, concept)) is cast(
            ModelConcept, domainElement
        )

    def test_typed_domain_element_of_raises_when_missing(self) -> None:
        concept = StubConcept(qn(), typedDomainElement=None)
        model = makeModel(StubModelXbrl())
        with pytest.raises(ArelleModelInconsistency):
            model.typedDomainElementOf(cast(ModelConcept, concept))

    def test_role_type_returns_single_match(self) -> None:
        roleUri = "https://example.com/role"
        roleType = object()
        model = makeModel(StubModelXbrl(roleTypes={roleUri: [roleType]}))
        assert model.roleType(roleUri) is cast(ModelRoleType, roleType)

    @pytest.mark.parametrize("matches", [[], [object(), object()]], ids=["none", "two"])
    def test_role_type_raises_on_wrong_cardinality(self, matches: list[Any]) -> None:
        roleUri = "https://example.com/role"
        model = makeModel(StubModelXbrl(roleTypes={roleUri: matches}))
        with pytest.raises(ArelleModelInconsistency):
            model.roleType(roleUri)

    def test_role_definition_of_a_declared_role(self) -> None:
        roleUri = "https://example.com/role"
        roleType = SimpleNamespace(definition="[100] General information")
        model = makeModel(StubModelXbrl(roleTypes={roleUri: [roleType]}))
        assert model.roleDefinition(roleUri) == "[100] General information"

    @pytest.mark.parametrize("definition", [None, ""], ids=["none", "empty"])
    def test_role_definition_absent_is_none(self, definition: str | None) -> None:
        roleUri = "https://example.com/role"
        roleType = SimpleNamespace(definition=definition)
        model = makeModel(StubModelXbrl(roleTypes={roleUri: [roleType]}))
        assert model.roleDefinition(roleUri) is None

    def test_role_definition_of_undeclared_role_is_none(self) -> None:
        # e.g. the standard link role, which has no roleType.
        model = makeModel(StubModelXbrl(roleTypes={}))
        assert model.roleDefinition("http://www.xbrl.org/2003/role/link") is None

    def test_resource_relationships_from_raises_on_non_resource(self) -> None:
        concept = StubConcept(qn())
        relSet = StubRelSet(
            fromMap={id(concept): [StubRel(toModelObject=StubConcept(qn("Label")))]}
        )
        stub = StubModelXbrl(relSets={(XbrlConst.conceptLabel, None): relSet})
        model = makeModel(stub)
        with pytest.raises(ArelleModelInconsistency):
            list(
                model.resourceRelationshipsFrom(
                    cast(ModelConcept, concept), XbrlConst.conceptLabel
                )
            )

    def test_resource_relationships_from_empty(self) -> None:
        concept = StubConcept(qn())
        stub = StubModelXbrl(relSets={(XbrlConst.conceptLabel, None): StubRelSet()})
        model = makeModel(stub)
        assert (
            list(
                model.resourceRelationshipsFrom(
                    cast(ModelConcept, concept), XbrlConst.conceptLabel
                )
            )
            == []
        )
