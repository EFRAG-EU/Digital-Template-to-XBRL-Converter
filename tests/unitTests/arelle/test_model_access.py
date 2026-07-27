"""Unit tests for the typed Arelle model-access facade.

Success paths that require genuine Arelle lxml-backed objects (ModelConcept,
ModelResource) are covered end-to-end by
tests/integrationTests/test_taxonomy_info_regeneration.py; these tests cover
the narrowing/consistency logic using lightweight stubs.
"""

from typing import Any, cast

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
        baseXbrliTypeQname: QName | None = None,
        typedDomainElement: Any = None,
    ) -> None:
        self.qname = qname
        self.isItem = isItem
        self.type = type
        self.baseXbrliTypeQname = baseXbrliTypeQname
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
        arcrole: str = "https://example.com/arcrole",
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

    def test_typed_domain_qname_of(self) -> None:
        domainQName = qn("myDomain")
        concept = StubConcept(qn(), typedDomainElement=StubConcept(domainQName))
        model = makeModel(StubModelXbrl())
        assert model.typedDomainQNameOf(cast(ModelConcept, concept)) is domainQName

    @pytest.mark.parametrize(
        "typedDomainElement", [None, StubConcept(None)], ids=["missing", "no-qname"]
    )
    def test_typed_domain_qname_of_raises(self, typedDomainElement: Any) -> None:
        concept = StubConcept(qn(), typedDomainElement=typedDomainElement)
        model = makeModel(StubModelXbrl())
        with pytest.raises(ArelleModelInconsistency):
            model.typedDomainQNameOf(cast(ModelConcept, concept))

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
