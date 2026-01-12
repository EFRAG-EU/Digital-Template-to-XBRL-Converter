import json
import logging
from collections.abc import Mapping, MutableMapping
from dataclasses import dataclass
from typing import Any, Optional, Self

from arelle.logging.handlers.LogToXmlHandler import LogToXmlHandler
from arelle.ModelValue import QName
from arelle.ModelXbrl import ModelXbrl

from mireport.conversionresults import Message, MessageType, Severity
from mireport.exceptions import MIReportException
from mireport.filesupport import FilelikeAndFileName
from mireport.version import VersionInformationTuple
from mireport.xml import QName as MireportQName
from mireport.xml import QNameMaker, getBootsrapQNameMaker

L = logging.getLogger(__name__)


class ArelleRelatedException(MIReportException):
    """Exception to wrap any exception that come from calling in to Arelle."""

    pass


@dataclass
class ArelleVersionHolder:
    arelle: VersionInformationTuple
    ixbrlViewer: VersionInformationTuple

    def __str__(self) -> str:
        return f"{self.arelle!s}, with {self.ixbrlViewer!s}"


class ArelleProcessingResult:
    """Holds the results of processing an XBRL file with Arelle."""

    _INTERESTING_LOG_MESSAGE_FRAGMENTS = (
        "validated in",
        "loaded in",
    )

    _UNINTERESTING_LOG_MESSAGE_PREFIXES = (
        "Activation of package",
        "Activation of plug-in",
        "Option ",
    )

    def __init__(self, jsonMessages: str, textLogLines: list[str]):
        self._validationMessages: list[Message] = []
        self._textLogLines: list[str] = textLogLines
        self._viewer: Optional[FilelikeAndFileName] = None
        self._xbrlJson: Optional[FilelikeAndFileName] = None
        self.__importArelleMessages(jsonMessages)

    def __importArelleMessages(self, json_str: str) -> None:
        wantDebug = L.isEnabledFor(logging.DEBUG)
        records: list[dict] = json.loads(json_str)["log"]
        for r in records:
            code: str = r.get("code", "")
            level: str = r.get("level", "")
            text: str = r.get("message", {}).get("text", "")
            fact: Optional[str] = r.get("message", {}).get("fact")

            if wantDebug:
                L.debug(f"{code=} {level=} {text=} {fact=}")

            match code:
                case "info" | "":
                    if "" == code or any(
                        a in text for a in self._INTERESTING_LOG_MESSAGE_FRAGMENTS
                    ):
                        self._validationMessages.append(
                            Message(
                                messageText=text,
                                severity=Severity.INFO,
                                messageType=MessageType.DevInfo,
                            )
                        )
                    elif text.startswith(self._UNINTERESTING_LOG_MESSAGE_PREFIXES):
                        if wantDebug:
                            L.debug(
                                f"Ignoring uninteresting Arelle log message: {code=} {level=} {text=} {fact=}"
                            )
                    else:
                        L.warning(
                            f"Unexpected Arelle log message: {code=} {level=} {text=} {fact=}"
                        )
                case _:
                    messageText = f"[{code}] {text}"
                    level_severity = Severity.fromLogLevelString(
                        level, default=Severity.WARNING
                    )
                    code_severity = Severity.fromLogLevelString(
                        code, default=Severity.INFO
                    )
                    severity = max(level_severity, code_severity, key=Severity.key)
                    self._validationMessages.append(
                        Message(
                            messageText=messageText,
                            severity=severity,
                            messageType=MessageType.XbrlValidation,
                            conceptQName=fact,
                        )
                    )

    @classmethod
    def fromLogToXmlHandler(cls, logHandler: LogToXmlHandler) -> Self:
        json = logHandler.getJson(clearLogBuffer=False)
        logLines = logHandler.getLines(clearLogBuffer=False)
        logHandler.clearLogBuffer()
        return cls(json, logLines)

    @property
    def viewer(self) -> FilelikeAndFileName:
        if self._viewer is not None:
            return self._viewer
        raise ArelleRelatedException("No viewer stored/retrieved.")

    @property
    def has_viewer(self) -> bool:
        return self._viewer is not None

    @property
    def xBRL_JSON(self) -> FilelikeAndFileName:
        if self._xbrlJson is not None:
            return self._xbrlJson
        raise ArelleRelatedException("No JSON stored/retrieved.")

    @property
    def messages(self) -> list[Message]:
        return list(self._validationMessages)

    @property
    def logLines(self) -> list[str]:
        return list(self._textLogLines)

    def addException(self, exception: Exception, message: Optional[str] = None) -> None:
        text = f"{exception.__class__.__name__}: {exception}"
        if message:
            text = f"{message}. {text}"
        self._validationMessages.append(
            Message(
                messageText=text,
                severity=Severity.ERROR,
                messageType=MessageType.XbrlValidation,
            )
        )


class ArelleObjectJSONEncoder(json.JSONEncoder):
    def default(self, o: Any) -> Any:
        if isinstance(o, QName):
            return str(o)
        # Let the base class default method raise the TypeError
        return super().default(o)

    @staticmethod
    def tidyKeys(obj: Any) -> Any:
        """default(obj) only works on objects not keys so use this method to
        preprocess your JSON payload and convert QName keys to str keys."""
        if isinstance(obj, MutableMapping):
            keys = list(obj.keys())
            for k in keys:
                new_k = k
                if isinstance(k, QName):
                    new_k = str(k)
                new_value = ArelleObjectJSONEncoder.tidyKeys(obj.pop(k))
                obj[new_k] = new_value
        elif isinstance(obj, (list, tuple)):
            for item in obj:
                ArelleObjectJSONEncoder.tidyKeys(item)
        return obj


class ArelleQNameCanonicaliser:
    """
    Convert Arelle QNames to mireport.xml.QNames. This is needed as Arelle QNames
    have the same prefix linked to multiple namespace URIs (prefixes are per XML source document).
    mireport.xml.QNames have a unique prefix for each namespace URI (prefixes are unique per taxonomy).
    """

    VANITY_NAMESPACE_PREFIX_MAP: Mapping[str, str] = {
        "http://www.xbrl.org/dtr/type/2024-01-31": "dtr-types",
        "http://www.xbrl.org/dtr/type/2022-03-31": "dtr-types-2022",
        "http://www.xbrl.org/dtr/type/2020-01-21": "dtr-types-2020",
    }

    def __init__(self, qnameMaker: QNameMaker) -> None:
        self.qnameMaker = qnameMaker

    @classmethod
    def bootstrap(cls, arelle_model: ModelXbrl) -> Self:
        qnameMaker = getBootsrapQNameMaker()

        # Bootstrap using Arelle's existing prefix list, but strip out any
        # ambiguous prefixes. That is those prefixes which are bound to more
        # than one namespace (allowed because the bindings appear in different
        # XML documents). This is not necessary (we fall back to generating new
        # prefixes rather than using an incorrect one) but lets us lazily work
        # out which prefix wins in the event of a clash based on the
        # concepts/types we're actually using later in convert().
        expected: dict[str, str] = dict(arelle_model.prefixedNamespaces)
        potentially_inconsistent: frozenset[tuple[str, str]] = frozenset(
            (prefix, qname.namespaceURI)
            for qname in arelle_model.qnameConcepts.keys()
            | arelle_model.qnameTypes.keys()
            if (prefix := qname.prefix)
        )
        for prefix, namespace in potentially_inconsistent:
            if (expectedNS := expected.get(prefix)) and expectedNS != namespace:
                del expected[prefix]

        for prefix, namespace in expected.items():
            qnameMaker.addNamespacePrefix(prefix, namespace)
        return cls(qnameMaker)

    def convert(self, qname: QName) -> MireportQName:
        wanted_prefix = qname.prefix
        namespace = qname.namespaceURI

        # Use our own vanity prefixes in preference to the taxonomy defined
        # prefixes.
        #
        # For example, various editions of the DTR can end up used in different
        # xml documents with a prefix of "dtr-types". Rather than have an
        # arbitrary one be "dtr-types" and the others being ns0, ns1 etc., make
        # sure the latest edition we know about of the dtr gets the "dtr-types"
        # prefix and the others get sensible variants of that.
        #
        # Anything we don't know about in VANITY_NAMESPACE_PREFIX_MAP will
        # either have its taxonomy defined prefix or get a generated ns0, ns1
        # prefix
        wanted_prefix = self.VANITY_NAMESPACE_PREFIX_MAP.get(namespace) or wanted_prefix

        # If the wanted prefix has not yet been used, add it so that it can be used
        if wanted_prefix and not self.qnameMaker.nsManager.prefixIsKnown(wanted_prefix):
            self.qnameMaker.addNamespacePrefix(wanted_prefix, namespace)

        return self.qnameMaker.fromNamespaceAndLocalName(namespace, qname.localName)

    def getNamespacePrefixMap(self) -> MutableMapping[str, str]:
        """Get a mapping of namespace URI to prefix."""
        # needs to be mutable so JSON encoder can work with it
        return dict(self.qnameMaker.namespacePrefixesMap)

    def convert_recursive(self, obj: Any) -> Any:
        """Recursively convert all QNames in a data structure to MireportQNames."""
        if isinstance(obj, QName):
            return str(self.convert(obj))
        elif isinstance(obj, Mapping):
            return {
                self.convert_recursive(k): self.convert_recursive(v)
                for k, v in obj.items()
            }
        elif isinstance(obj, (list, tuple)):
            return type(obj)(self.convert_recursive(item) for item in obj)
        else:
            return obj
