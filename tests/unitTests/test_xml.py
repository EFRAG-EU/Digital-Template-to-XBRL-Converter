import sys

import pytest

from mireport.exceptions import BrokenNamespacePrefixException, BrokenQNameException
from mireport.xml import (
    ENUM2_NS,
    ISO4217_NS,
    UTR_NS,
    XBRLI_NS,
    NamespaceManager,
    QNameMaker,
    getBootstrapQNameMaker,
)

# Note on coverage: the "prefix is not an NCName" branch of
# QNameMaker._partsValidator is intentionally untested — it is unreachable in
# practice. Every prefix reaching it has already passed NamespaceManager.add
# validation (a valid NCName) or been rejected earlier as an unknown prefix in
# _getAndValidateParts.

XBRLI_PREFIX_NS = "http://www.xbrl.org/2003/instance"
UTR_PREFIX_NS = "http://www.xbrl.org/2009/utr"


@pytest.fixture
def namespaces() -> NamespaceManager:
    return NamespaceManager()


@pytest.fixture
def xbrli_and_utr() -> NamespaceManager:
    a = NamespaceManager()
    a.add("xbrli", XBRLI_PREFIX_NS)
    a.add("utr", UTR_PREFIX_NS)
    return a


@pytest.fixture
def qmaker(xbrli_and_utr: NamespaceManager) -> QNameMaker:
    a = QNameMaker(xbrli_and_utr)
    return a


@pytest.fixture
def ns_manager() -> NamespaceManager:
    ns = NamespaceManager()
    ns.add("foo", "http://example.com/foo")
    ns.add("bar", "http://example.org/bar")
    ns.add("test", "http://test.net/ns")
    ns.add("data", "http://data.local/ns")
    return ns


@pytest.fixture
def qname_maker(ns_manager: NamespaceManager) -> QNameMaker:
    qm = QNameMaker(ns_manager)
    return qm


class TestNamespaceManager:
    def test_add_not_uri(self, namespaces: NamespaceManager) -> None:
        namespaces.add("abc", "http://mushroom")

    def test_add_none_prefix_raises(self, namespaces: NamespaceManager) -> None:
        with pytest.raises(BrokenNamespacePrefixException):
            namespaces.add(None, "hedgehog")

    def test_bad_prefix_format_raises(self, namespaces: NamespaceManager) -> None:
        # "1bad" is not a valid NCName (cannot start with a digit).
        with pytest.raises(BrokenNamespacePrefixException):
            namespaces.add("1bad", "http://example.com/ns")

    def test_rebind_prefix_to_different_namespace_raises(
        self, namespaces: NamespaceManager
    ) -> None:
        namespaces.add("foo", "http://example.com/a")
        with pytest.raises(BrokenNamespacePrefixException):
            namespaces.add("foo", "http://example.com/b")

    def test_rebind_prefix_to_same_namespace_is_noop(
        self, namespaces: NamespaceManager
    ) -> None:
        namespaces.add("foo", "http://example.com/a")
        namespaces.add("foo", "http://example.com/a")
        assert len(namespaces._prefixToNamespaces) == 1

    def test_get_namespace_for_prefix_round_trip(
        self, namespaces: NamespaceManager
    ) -> None:
        namespaces.add("foo", "http://example.com/foo")
        assert namespaces.getNamespaceForPrefix("foo") == "http://example.com/foo"

    def test_generate_prefix(self, namespaces: NamespaceManager) -> None:
        p0 = namespaces.getOrGeneratePrefixForNamespace("http://example.com/n0")
        assert p0 == "ns0"
        n1 = "http://example.com/n1"
        namespaces.add("ns1", n1)
        p1 = namespaces.getPrefixForNamespace(n1)
        p2 = namespaces.getOrGeneratePrefixForNamespace("http://example.com/n2")
        assert p1 != p2

    def test_add_namespace_and_interning(self) -> None:
        p = NamespaceManager()
        ns = "http://example.com"
        p1 = p.getOrGeneratePrefixForNamespace(ns)
        assert p1 == p.getPrefixForNamespace(ns)
        assert len(p._prefixToNamespaces) == 1
        p.add(p1, ns)
        assert len(p._prefixToNamespaces) == 1
        p.add("p2", ns)
        assert len(p._prefixToNamespaces) == 2
        pG = p.getOrGeneratePrefixForNamespace(ns)
        assert p1 == pG
        assert p1 is pG, "String interning has been broken."


class TestIsValidQName:
    @pytest.mark.parametrize(
        "qname",
        [
            "foo:Element",
            "bar:foo-bar_123.baz",
            "test:ValidName",
            "data:another_one",
            "data:another_one.two",
        ],
        ids=["simple", "punctuation", "test-ns", "data-ns", "data-ns-dotted"],
    )
    def test_valid(self, qname_maker: QNameMaker, qname: str) -> None:
        assert qname_maker.isValidQName(qname)

    @pytest.mark.parametrize(
        "qname",
        ["testElement", "", "unknown:Thing", "1foo:Element", "foo:!badname"],
        ids=["no-colon", "empty", "unknown-prefix", "bad-prefix", "bad-localname"],
    )
    def test_invalid(self, qname_maker: QNameMaker, qname: str) -> None:
        assert not qname_maker.isValidQName(qname)


class TestQNameMaker:
    def test_fromString(self, qmaker: QNameMaker) -> None:
        qmaker.fromString("xbrli:pure")
        qmaker.fromString("utr:badger")

    def test_fromString_unknown_prefix_raises(self, qmaker: QNameMaker) -> None:
        with pytest.raises(BrokenQNameException):
            qmaker.fromString("abc:def")

    def test_fromString_none_raises(self, qmaker: QNameMaker) -> None:
        with pytest.raises(BrokenQNameException):
            qmaker.fromString(None)

    def test_fromNamespaceAndLocalName_new_namespace_generates_prefix(
        self, namespaces: NamespaceManager
    ) -> None:
        maker = QNameMaker(namespaces)
        ns = "http://example.com/brand-new"
        qn = maker.fromNamespaceAndLocalName(ns, "Widget")
        assert qn.namespace == ns
        assert qn.localName == "Widget"
        # A prefix was generated for the previously-unknown namespace.
        assert qn.prefix == "ns0"
        # A second call returns the very same cached object (tuple-key cache hit).
        assert maker.fromNamespaceAndLocalName(ns, "Widget") is qn

    def test_addNamespacePrefix_makes_prefix_resolvable(
        self, namespaces: NamespaceManager
    ) -> None:
        maker = QNameMaker(namespaces)
        with pytest.raises(BrokenQNameException):
            maker.fromString("acme:Thing")
        maker.addNamespacePrefix("acme", "http://example.com/acme")
        qn = maker.fromString("acme:Thing")
        assert qn.prefix == "acme"
        assert qn.namespace == "http://example.com/acme"

    def test_namespacePrefixesMap_contents_and_read_only(
        self, qmaker: QNameMaker
    ) -> None:
        mapping = qmaker.namespacePrefixesMap
        assert mapping["xbrli"] == XBRLI_PREFIX_NS
        assert mapping["utr"] == UTR_PREFIX_NS
        with pytest.raises(TypeError):
            mapping["new"] = "http://example.com/new"  # type: ignore[index]


class TestQName:
    def test_str(self, qmaker: QNameMaker) -> None:
        assert str(qmaker.fromString("xbrli:pure")) == "xbrli:pure"

    def test_repr(self, qmaker: QNameMaker) -> None:
        r = repr(qmaker.fromString("xbrli:pure"))
        assert r.startswith("QName(")
        assert "pure" in r and "xbrli" in r

    def test_sorting(self, qmaker: QNameMaker) -> None:
        # Sort key is (prefix, localName, namespace).
        a = qmaker.fromString("utr:apple")
        b = qmaker.fromString("utr:banana")
        c = qmaker.fromString("xbrli:apple")
        assert sorted([c, b, a]) == [a, b, c]

    def test_lt_with_non_qname_raises(self, qmaker: QNameMaker) -> None:
        with pytest.raises(TypeError):
            _ = qmaker.fromString("xbrli:pure") < "not a qname"


class TestQNameFlyweight:
    def test_flyweight_identity_fromString(self, qmaker: QNameMaker) -> None:
        assert qmaker.fromString("xbrli:pure") is qmaker.fromString("xbrli:pure")

    def test_flyweight_identity_cross_factory(self, qmaker: QNameMaker) -> None:
        # fromString and fromNamespaceAndLocalName must return the same cached
        # object for an equivalent QName.
        from_str = qmaker.fromString("xbrli:pure")
        from_parts = qmaker.fromNamespaceAndLocalName(XBRLI_NS, "pure")
        assert from_str is from_parts

    def test_localName_is_interned(self, qmaker: QNameMaker) -> None:
        qn = qmaker.fromString("xbrli:pure")
        assert qn.localName is sys.intern("pure"), "localName has not been interned."

    def test_cross_maker_value_equality(self, xbrli_and_utr: NamespaceManager) -> None:
        # Two independent makers (separate flyweight caches) produce distinct
        # instances that nonetheless compare equal and hash equal, because
        # sys.intern is global.
        a = QNameMaker(xbrli_and_utr).fromString("xbrli:pure")
        other_ns = NamespaceManager()
        other_ns.add("xbrli", XBRLI_PREFIX_NS)
        b = QNameMaker(other_ns).fromString("xbrli:pure")
        assert a is not b
        assert a == b
        assert hash(a) == hash(b)

    def test_hash_is_lazy_then_cached(self, qmaker: QNameMaker) -> None:
        qn = qmaker.fromString("xbrli:pure")
        assert qn._hash is None, "Hash should not be computed until first use."
        first = hash(qn)
        assert qn._hash == first, "Hash should be cached after first use."
        assert hash(qn) == first, "Cached hash should be stable."

    def test_eq_consistency(self, qmaker: QNameMaker) -> None:
        a = qmaker.fromString("xbrli:pure")
        b = qmaker.fromString("xbrli:pure")
        assert a == b and hash(a) == hash(b)
        assert a != "xbrli:pure"  # non-QName -> NotImplemented -> False


class TestGetBootstrapQNameMaker:
    def test_returns_qname_maker(self) -> None:
        assert isinstance(getBootstrapQNameMaker(), QNameMaker)

    def test_binds_exactly_the_core_namespaces(self) -> None:
        mapping = getBootstrapQNameMaker().namespacePrefixesMap
        assert dict(mapping) == {
            "iso4217": ISO4217_NS,
            "utr": UTR_NS,
            "xbrli": XBRLI_NS,
            "enum2": ENUM2_NS,
        }

    @pytest.mark.parametrize(
        "qname",
        ["iso4217:EUR", "utr:tCO2e", "xbrli:item", "enum2:enumerationItemType"],
        ids=["iso4217", "utr", "xbrli", "enum2"],
    )
    def test_core_prefixes_resolve(self, qname: str) -> None:
        assert getBootstrapQNameMaker().isValidQName(qname)
