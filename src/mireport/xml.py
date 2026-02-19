import itertools
import re
import sys
from collections.abc import Mapping
from types import MappingProxyType
from typing import Any, NamedTuple

from mireport.exceptions import BrokenNamespacePrefixException, BrokenQNameException

NCNAME_RE = re.compile(r"([a-zA-Z_][\w.-]*)")

# XML schema defines a QNAME as NCNAME:NCNAME or just an unprefixed NCNAME.
# We've no need to support the bare NCNAME version so we don't as it makes
# things much simpler.
QNAME_RE = re.compile(rf"{NCNAME_RE.pattern}:{NCNAME_RE.pattern}")

ISO4217_NS = sys.intern("http://www.xbrl.org/2003/iso4217")
UTR_NS = sys.intern("http://www.xbrl.org/2009/utr")
XBRLI_NS = sys.intern("http://www.xbrl.org/2003/instance")
ENUM2_NS = sys.intern("http://xbrl.org/2020/extensible-enumerations-2.0")


class _QNameTuple(NamedTuple):
    prefix: str
    localName: str
    namespace: str


class _NSPrefixTuple(NamedTuple):
    prefix: str
    namespace: str


class NamespaceManager:
    """Prefix and namespace are stored in sys.intern() form as they appear lots and are checked lots in XBRL.
    intern() means we can use identiy checking ("is") rather than equality checking ("==") providing both sides have been intern()d"""

    def __init__(self) -> None:
        self.__prefixCounter = itertools.count(0)
        self._prefixToNamespaces: dict[str, str] = {}

    def getNamespaceForPrefix(self, prefix: str) -> str:
        return self._prefixToNamespaces[prefix]

    def getPrefixForNamespace(self, namespace: str) -> str:
        return next(
            (
                prefix
                for prefix, n in self._prefixToNamespaces.items()
                if n == namespace
            ),
        )

    def _validate(self, prefix: str, namespace: str) -> _NSPrefixTuple:
        """Validates the namespace and prefix and intern()s them."""
        if not (namespace and namespace.startswith(("https://", "http://"))):
            # TODO: use a proper URI / URN validator.
            raise BrokenNamespacePrefixException(
                f"Namespace does not look valid: {namespace}"
            )
        if not (prefix and NCNAME_RE.fullmatch(prefix)):
            raise BrokenNamespacePrefixException(
                f"Prefix {prefix} does not look like an NCName."
            )
        prefix = sys.intern(prefix)
        namespace = sys.intern(namespace)
        return _NSPrefixTuple(prefix=prefix, namespace=namespace)

    def add(self, prefix: str, namespace: str) -> str:
        prefix, namespace = self._validate(prefix, namespace)
        old_ns = self._prefixToNamespaces.get(prefix)
        if old_ns is None:
            # Good, safe to add this one then.
            pass
        elif old_ns is namespace:
            # trying to add the exact same prefix/namespace combination is
            # pointless but fine
            return prefix
        elif old_ns is not namespace:
            raise BrokenNamespacePrefixException(
                f"Unable to add namespace prefix binding for '{prefix}': already bound to existing namespace: '{old_ns}'; attempted namespace '{namespace}'."
            )
        self._prefixToNamespaces[prefix] = namespace
        return prefix

    def getOrGeneratePrefixForNamespace(self, namespace: str) -> str:
        try:
            return self.getPrefixForNamespace(namespace)
        except StopIteration:
            while (
                new_prefix := f"ns{next(self.__prefixCounter)}"
            ) in self._prefixToNamespaces:
                pass
            new_prefix = self.add(new_prefix, namespace)
        return new_prefix


class QName:
    """
    Core class representing a QName. Stores the local name, namespace and
    prefix.

    Do not instantiate directly but instead create a :class:`QNameMaker`
    using :method:`myQnameMaker = getBootstrapQNameMaker()` and then call
    :meth:`myQnameMaker.fromString(qname_string)`.
    """

    __slots__ = ("localName", "namespace", "prefix")

    def __init__(
        self,
        q: _QNameTuple,
    ):
        self.namespace = sys.intern(q.namespace)
        self.prefix = sys.intern(q.prefix)
        self.localName = q.localName

    def __key(self) -> tuple[str, str, str]:
        # compare on localname first for speed
        return (self.localName, self.prefix, self.namespace)

    def __sortKey(self) -> tuple[str, str, str]:
        # compare as a human would expect (prefixA:localB comes after prefixA:localA)
        return (self.prefix, self.localName, self.namespace)

    def __hash__(self) -> int:
        return hash(self.__key())

    def __eq__(self, other: Any) -> bool:
        if self is other:
            return True
        if isinstance(other, QName):
            return self.__key() == other.__key()
        return NotImplemented

    def __lt__(self, other: object) -> bool:
        if isinstance(other, QName):
            return self.__sortKey() < other.__sortKey()
        return NotImplemented

    def __str__(self) -> str:
        return f"{self.prefix}:{self.localName}"

    def __repr__(self) -> str:
        return f"QName{self.__key()}"


class QNameMaker:
    def __init__(self, nsManager: NamespaceManager):
        self._nsManager = nsManager

    def _getAndValidateParts(self, /, qname: str) -> _QNameTuple:
        if not (qname and len(parts := qname.split(":", 1)) == 2):
            raise BrokenQNameException(
                f'QName does not look format ("prefix:part") valid: "{qname}"'
            )
        prefix, localName = parts
        try:
            namespace = self._nsManager.getNamespaceForPrefix(prefix)
        except KeyError as k:
            raise BrokenQNameException(f"QName {qname} has an unknown prefix.") from k
        q = _QNameTuple(prefix=prefix, localName=localName, namespace=namespace)
        self._partsValidator(q)
        return q

    def _partsValidator(self, /, q: _QNameTuple) -> None:
        if not NCNAME_RE.fullmatch(q.prefix):
            raise BrokenQNameException(
                f"QName prefix {q.prefix} does not look like an NCName."
            )

        if not NCNAME_RE.fullmatch(q.localName):
            raise BrokenQNameException(
                f"QName local name {q.localName} does not look like an NCName."
            )
        return None

    def isValidQName(self, /, qname: str) -> bool:
        try:
            self._getAndValidateParts(qname)
            return True
        except (KeyError, BrokenQNameException):
            return False

    def fromString(self, /, qname: str) -> QName:
        q = self._getAndValidateParts(qname)
        return QName(q)

    def fromNamespaceAndLocalName(self, /, namespace: str, localName: str) -> QName:
        prefix = self._nsManager.getOrGeneratePrefixForNamespace(namespace)
        q = _QNameTuple(prefix=prefix, localName=localName, namespace=namespace)
        self._partsValidator(q)
        return QName(q)

    def addNamespacePrefix(self, prefix: str, namespace: str) -> None:
        self._nsManager.add(prefix, namespace)

    @property
    def namespacePrefixesMap(self) -> Mapping[str, str]:
        """Get a mapping of prefix to namespace for all known prefixes."""
        return MappingProxyType(self._nsManager._prefixToNamespaces)


def getBootstrapQNameMaker() -> QNameMaker:
    """Get a QNameMaker configured with the bare minimum necessary namespaces configured on it for mireport to work correctly."""
    # Only put the namespaces here we *must* have working for our code to work.
    # Namespaces such as dtr-types (which there are many versions of) are a good
    # example of something *not* to put here.
    boot = NamespaceManager()
    boot.add("iso4217", ISO4217_NS)
    boot.add("utr", UTR_NS)
    boot.add("xbrli", XBRLI_NS)
    boot.add("enum2", ENUM2_NS)
    return QNameMaker(boot)
