import json
import logging
from dataclasses import dataclass
from typing import Any, MutableMapping, NamedTuple, Optional, Self

from arelle.FileSource import FileNamedBytesIO
from arelle.logging.handlers.LogToXmlHandler import LogToXmlHandler
from arelle.ModelValue import QName

from mireport.conversionresults import Message, MessageType, Severity
from mireport.exceptions import MIReportException
from mireport.filesupport import FilelikeAndFileName

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
