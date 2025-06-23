import pytest

from mireport.exceptions import BrokenNamespacePrefixException, BrokenQNameException
from mireport.xml import NamespaceManager, QNameMaker


@pytest.fixture
def namespaces() -> NamespaceManager:
    return NamespaceManager()


@pytest.fixture
def xbrli_and_utr() -> NamespaceManager:
    a = NamespaceManager()
    a.add("xbrli", "http://www.xbrl.org/2003/instance")
    a.add("utr", "http://www.xbrl.org/2009/utr")
    return a


@pytest.fixture
def qmaker(xbrli_and_utr: NamespaceManager) -> QNameMaker:
    a = QNameMaker(xbrli_and_utr)
    return a


def test_add_not_uri(namespaces: NamespaceManager) -> None:
    namespaces.add("abc", "http://mushroom")


def test_add_none_str(namespaces: NamespaceManager) -> None:
    with pytest.raises(BrokenNamespacePrefixException):
        namespaces.add(None, "hedgehog")


def test_expected_qname(qmaker: QNameMaker) -> None:
    with pytest.raises(BrokenQNameException):
        qmaker.fromString("abc:def")


def test_unprefixed_qname(qmaker: QNameMaker) -> None:
    with pytest.raises(BrokenQNameException):
        qmaker.fromString(None)


def test_generate_prefix(namespaces: NamespaceManager) -> None:
    p0 = namespaces.getOrGeneratePrefixForNamespace("http://example.com/n0")
    assert p0 == "ns0"
    n1 = "http://example.com/n1"
    namespaces.add("ns1", n1)
    p1 = namespaces.getPrefixForNamespace(n1)
    p2 = namespaces.getOrGeneratePrefixForNamespace("http://example.com/n2")
    assert p1 != p2


def test_qname_fromString(qmaker: QNameMaker) -> None:
    qmaker.fromString("xbrli:pure")
    qmaker.fromString("utr:badger")


def test_add_namespace(xbrli_and_utr: NamespaceManager) -> None:
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


def test_valid_qname(qname_maker: QNameMaker) -> None:
    assert qname_maker.isValidQName("foo:Element")


def test_valid_qname_with_punctuation(qname_maker: QNameMaker) -> None:
    assert qname_maker.isValidQName("bar:foo-bar_123.baz")


def test_invalid_qname_missing_colon(qname_maker: QNameMaker) -> None:
    assert not qname_maker.isValidQName("testElement")


def test_invalid_qname_empty_string(qname_maker: QNameMaker) -> None:
    assert not qname_maker.isValidQName("")


def test_invalid_qname_unknown_prefix(qname_maker: QNameMaker) -> None:
    assert not qname_maker.isValidQName("unknown:Thing")


def test_invalid_qname_bad_prefix_format(qname_maker: QNameMaker) -> None:
    assert not qname_maker.isValidQName("1foo:Element")


def test_invalid_qname_bad_local_name_format(qname_maker: QNameMaker) -> None:
    assert not qname_maker.isValidQName("foo:!badname")


def test_valid_qname_all_test_ns(qname_maker: QNameMaker) -> None:
    assert qname_maker.isValidQName("test:ValidName")
    assert qname_maker.isValidQName("data:another_one")
    assert qname_maker.isValidQName("data:another_one.two")
