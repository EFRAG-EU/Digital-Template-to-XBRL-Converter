import json
import logging
from collections.abc import Mapping, MutableMapping
from dataclasses import dataclass
from typing import Any, NamedTuple, Optional, Self

from arelle.FileSource import FileNamedBytesIO
from arelle.logging.handlers.LogToXmlHandler import LogToXmlHandler
from arelle.ModelValue import QName
from arelle.ModelXbrl import ModelXbrl

from mireport.conversionresults import Message, MessageType, Severity
from mireport.exceptions import MIReportException
from mireport.filesupport import FilelikeAndFileName
from mireport.xml import QName as MireportQName
from mireport.xml import QNameMaker, getBootsrapQNameMaker

L = logging.getLogger(__name__)


def fileLikeToArelleFileSource(
    fileLike: FilelikeAndFileName,
) -> FileNamedBytesIO:
    """Convert a FilelikeAndFileName to an Arelle FileNamedBytesIO."""
    return FileNamedBytesIO(
        fileName=fileLike.filename, initial_bytes=fileLike.fileContent
    )


class ArelleRelatedException(MIReportException):
    """Exception to wrap any exception that come from calling in to Arelle."""

    pass


class VersionInformationTuple(NamedTuple):
    name: str
    version: str

    def __str__(self) -> str:
        return f"{self.name} (version {self.version})"


@dataclass
class ArelleVersionHolder:
    arelle: VersionInformationTuple
    ixbrlViewer: VersionInformationTuple

    def __str__(self) -> str:
        return f"{self.arelle!s}, with {self.ixbrlViewer!s}"


class ArelleProcessingResult:
    """Holds the results of processing an XBRL file with Arelle."""

    _INTERESTING_LOG_MESSAGES = (
        "validated in",
        "loaded in",
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

            if code == "info" and text.startswith("Option "):
                # this is a debug message about an option being set
                # we don't want to show these in the report
                continue

            match code:
                case "info" | "":
                    if "" == code or any(
                        a in text
                        for a in ArelleProcessingResult._INTERESTING_LOG_MESSAGES
                    ):
                        self._validationMessages.append(
                            Message(
                                messageText=text,
                                severity=Severity.INFO,
                                messageType=MessageType.DevInfo,
                            )
                        )
                case _:
                    messageText = f"[{code}] {text}"
                    self._validationMessages.append(
                        Message(
                            messageText=messageText,
                            severity=Severity.fromLogLevelString(level),
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

    def __init__(self, qnameMaker: QNameMaker) -> None:
        self.qnameMaker = qnameMaker

    @classmethod
    def bootstrap(cls, arelle_model: ModelXbrl) -> Self:
        qnameMaker = getBootsrapQNameMaker()
        qnameMaker.addNamespacePrefix(
            "dtr-types", "http://www.xbrl.org/dtr/type/2024-01-31"
        )
        for prefix, namespace in arelle_model.prefixedNamespaces.items():
            if namespace.startswith("http://www.xbrl.org/dtr/"):
                match namespace:
                    case "http://www.xbrl.org/dtr/type/2022-03-31":
                        prefix = "dtr-types-2022"
                    case "http://www.xbrl.org/dtr/type/2020-01-21":
                        prefix = "dtr-types-2020"
            qnameMaker.addNamespacePrefix(prefix, namespace)
        return cls(qnameMaker)

    def convert(self, qname: QName) -> MireportQName:
        correct_prefix = (
            qname.prefix is not None
            and self.qnameMaker.nsManager.prefixIsKnown(qname.prefix)
            and self.qnameMaker.nsManager.getNamespaceForPrefix(qname.prefix)
            == qname.namespaceURI
        )

        if correct_prefix:
            return self.qnameMaker.fromString(str(qname))
        else:
            wanted_prefix = qname.prefix
            if (
                wanted_prefix is not None
                and not self.qnameMaker.nsManager.prefixIsKnown(wanted_prefix)
            ):
                self.qnameMaker.addNamespacePrefix(wanted_prefix, qname.namespaceURI)

            return self.qnameMaker.fromNamespaceAndLocalName(
                qname.namespaceURI, qname.localName
            )

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
