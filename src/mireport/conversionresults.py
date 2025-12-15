import re
import uuid
from collections.abc import Iterable
from enum import StrEnum
from functools import lru_cache
from time import perf_counter_ns
from types import MappingProxyType, TracebackType
from typing import Mapping, Optional, Self, Type

from mireport.exceptions import EarlyAbortException
from mireport.stringutil import format_time_ns
from mireport.taxonomy import Concept
from mireport.xml import QName


class Severity(StrEnum):
    ERROR = "Error"
    WARNING = "Warning"
    INFO = "Info"

    @classmethod
    def all(cls) -> set[Self]:
        return set(cls.__members__.values())

    @classmethod
    @lru_cache(1)
    def maxValueWidth(cls) -> int:
        return max(len(s.value) for s in cls.__members__.values())

    @classmethod
    @lru_cache(32)
    def fromLogLevelString(cls, level: str, *, default: Optional[Self] = None) -> Self:
        # return cls.__members__.get(level.title(), cls(cls.WARNING.value))
        lower_lookup = {k.lower(): v for k, v in cls.__members__.items()}
        level_lower = level.lower()

        if (attempt1 := lower_lookup.get(level_lower)) is not None:
            return cls(attempt1.value)

        if (
            attempt2 := next(
                (
                    a
                    for a in lower_lookup
                    for word in re.split(r"\W+", level_lower)
                    if a == word
                ),
                None,
            )
        ) is not None:
            return cls(lower_lookup[attempt2].value)

        return default or cls(cls.WARNING.value)

    @property
    def rank(self) -> int:
        match self:
            case Severity.INFO:
                return 0
            case Severity.WARNING:
                return 1
            case Severity.ERROR:
                return 2
        raise ValueError("Unknown Severity: no rank available.")

    @classmethod
    def key(cls, severity: Self) -> int:
        return severity.rank


class MessageType(StrEnum):
    DevInfo = "Dev Info"
    ExcelParsing = "Excel Parsing"
    Conversion = "Conversion"
    XbrlValidation = "XBRL Validation"
    Progress = "Progress Status"

    @classmethod
    def all(cls) -> set[Self]:
        return set(cls.__members__.values())

    @classmethod
    def allExcept(cls, *mtypes: Self) -> set[Self]:
        wanted = cls.all()
        wanted.difference_update(mtypes)
        return wanted

    @classmethod
    @lru_cache(1)
    def maxValueWidth(cls) -> int:
        return max(len(s.value) for s in cls.__members__.values())


class Message:
    def __init__(
        self,
        messageText: str,
        severity: Severity,
        messageType: MessageType,
        conceptQName: Optional[str] = None,
        excelReference: Optional[str] = None,
    ):
        self.messageText: str = messageText
        self.severity: Severity = severity
        self.messageType: MessageType = messageType
        self.conceptQName: Optional[str] = conceptQName
        self.excelReference: Optional[str] = excelReference

    def __str__(self) -> str:
        bits = [
            f"{self.severity.value:{Severity.maxValueWidth()}s} : {self.messageType.value:{MessageType.maxValueWidth()}s} :"
        ]
        bits.append(self.messageText)
        if self.excelReference is not None:
            bits.append(f"(Excel: {self.excelReference})")
        if self.conceptQName is not None:
            bits.append(f"(taxonomy concept: {self.conceptQName})")

        return " ".join(bits)

    @classmethod
    def fromDict(cls, stuff: dict) -> Self:
        m = stuff["m"]
        s = Severity[stuff["s"]]
        mt = MessageType[stuff["mt"]]
        c = stuff["c"]
        e = stuff["e"]
        return cls(m, s, mt, c, e)

    def toDict(self) -> dict:
        d = {
            "m": self.messageText,
            "s": self.severity.name,
            "mt": self.messageType.name,
            "c": self.conceptQName,
            "e": self.excelReference,
        }
        return d


class ConversionResults:
    def __init__(
        self,
        conversionId: str,
        messages: list[Message],
        cellsQueried: int,
        cellsPopulated: int,
        conversionSuccessful: bool,
    ) -> None:
        self.conversionId = conversionId
        self.messages: list[Message] = messages
        self.cellsQueried: int = cellsQueried
        self.cellsPopulated: int = cellsPopulated
        self._conversionSuccessful: bool = conversionSuccessful

    @classmethod
    def fromDict(cls, stuff: dict) -> Self:
        id = stuff["id"]
        m = [Message.fromDict(m) for m in stuff["m"]]
        q = stuff["q"]
        p = stuff["p"]
        success = stuff["success"]
        return cls(id, m, q, p, success)

    def toDict(self) -> dict:
        d = {
            "id": self.conversionId,
            "m": [m.toDict() for m in self.messages],
            "q": self.cellsQueried,
            "p": self.cellsPopulated,
            "success": self._conversionSuccessful,
        }
        return d

    def __len__(self) -> int:
        return len(self.messages)

    def hasErrors(self) -> bool:
        return any(m.severity is Severity.ERROR for m in self.userMessages)

    def hasWarnings(self) -> bool:
        return any(m.severity is Severity.WARNING for m in self.userMessages)

    def hasErrorsOrWarnings(self) -> bool:
        wanted = frozenset((Severity.ERROR, Severity.WARNING))
        return any(m.severity in wanted for m in self.userMessages)

    def getOverallSeverity(
        self, *, withoutXBRLValidation: bool = False, justXBRLValidation: bool = False
    ) -> Severity:
        if withoutXBRLValidation and justXBRLValidation:
            raise ValueError(
                "Invalid argument combination: 'withoutXBRLValidation' and 'justXBRLValidation' cannot both be True."
            )

        if justXBRLValidation:
            wanted_mt = {MessageType.XbrlValidation}
        else:
            wanted_mt = MessageType.allExcept(MessageType.DevInfo, MessageType.Progress)
            if withoutXBRLValidation:
                wanted_mt.discard(MessageType.XbrlValidation)

        candidates = {
            c.severity for c in self.getMessages(wantedMessageTypes=wanted_mt)
        }
        return max(candidates, default=Severity.INFO, key=Severity.key)

    def getRAG(
        self, *, withoutXBRLValidation: bool = False, justXBRLValidation: bool = False
    ) -> Mapping[str, bool]:
        overallSeverity = self.getOverallSeverity(
            withoutXBRLValidation=withoutXBRLValidation,
            justXBRLValidation=justXBRLValidation,
        )
        return MappingProxyType(
            {
                "red": overallSeverity is Severity.ERROR,
                "amber": overallSeverity is Severity.WARNING,
                "green": overallSeverity is Severity.INFO,
            }
        )

    def hasMessages(self, userOnly: bool = False) -> bool:
        if userOnly:
            return bool(self.userMessages)
        return bool(self.messages)

    def getMessages(
        self,
        *,
        wantedMessageTypes: set[MessageType] = MessageType.all(),
        wantedMessageSeverities: set[Severity] = Severity.all(),
    ) -> list[Message]:
        messages = [
            m
            for m in self.messages
            if m.severity in wantedMessageSeverities
            and m.messageType in wantedMessageTypes
        ]
        return messages

    @property
    def developerMessages(self) -> list[Message]:
        return self.getMessages()

    @property
    def userMessages(self) -> list[Message]:
        return self.getMessages(
            wantedMessageTypes=MessageType.allExcept(
                MessageType.DevInfo, MessageType.Progress
            ),
            wantedMessageSeverities=Severity.all(),
        )

    @property
    def numCellQueries(self) -> int:
        return self.cellsQueried

    @property
    def numCellsPopulated(self) -> int:
        return self.cellsPopulated

    @property
    def conversionSuccessful(self) -> bool:
        return self._conversionSuccessful

    @property
    def isXbrlValid(self) -> bool:
        has_xbrl_messages = bool(
            self.getMessages(wantedMessageTypes={MessageType.XbrlValidation})
        )
        return (
            has_xbrl_messages
            and self.getOverallSeverity(justXBRLValidation=True) is not Severity.ERROR
        )


class ConversionResultsBuilder(ConversionResults):
    def __init__(
        self, conversionId: Optional[str] = None, consoleOutput: bool = False
    ) -> None:
        if conversionId is not None:
            self.conversionId = conversionId
        else:
            self.conversionId = str(uuid.uuid4())
        self.messages: list[Message] = list()
        self.cellsQueriedBuilder: set[tuple[str, int, int]] = set()
        self.cellsPopulatedBuilder: set[tuple[str, int, int]] = set()
        self.consoleOutput = consoleOutput

    def addCellQueries(self, delta: Iterable[tuple[str, int, int]]) -> None:
        self.cellsQueriedBuilder.update(delta)

    def addCellsWithData(self, delta: Iterable[tuple[str, int, int]]) -> None:
        self.cellsPopulatedBuilder.update(delta)

    @property
    def numCellQueries(self) -> int:
        return len(self.cellsQueriedBuilder)

    @property
    def numCellsPopulated(self) -> int:
        return len(self.cellsPopulatedBuilder)

    def addMessage(
        self,
        message_text: str,
        severity: Severity,
        message_type: MessageType,
        *,
        taxonomy_concept: Optional[QName | Concept] = None,
        excel_reference: Optional[str] = None,
    ) -> None:
        concept_str_or_none: Optional[str]
        if taxonomy_concept is None:
            concept_str_or_none = taxonomy_concept
        else:
            concept_str_or_none = str(taxonomy_concept)
        self.messages.append(
            Message(
                message_text,
                severity,
                message_type,
                concept_str_or_none,
                excel_reference,
            )
        )

    def processingContext(self, name: str) -> "ProcessingContext":
        return ProcessingContext(self, name)

    def addMessages(self, messages: Iterable[Message]) -> None:
        self.messages.extend(messages)

    @property
    def conversionSuccessful(self) -> bool:
        return self.getOverallSeverity(withoutXBRLValidation=True) is not Severity.ERROR

    def build(self) -> ConversionResults:
        return ConversionResults(
            self.conversionId,
            self.messages,
            len(self.cellsPopulatedBuilder),
            len(self.cellsPopulatedBuilder),
            self.conversionSuccessful,
        )


class ProcessingContext:
    def __init__(self, resultsBuilder: ConversionResultsBuilder, name: str) -> None:
        self._resultsBuilder: ConversionResultsBuilder = resultsBuilder
        self.name: str = name
        self.succeeded: bool = False
        self.start_time: int
        self.current_section_start_time: int
        self.current_section_name: Optional[str] = None
        self.console = self._resultsBuilder.consoleOutput

    def __enter__(self) -> Self:
        self.start_time = self.current_section_start_time = perf_counter_ns()
        self._logProgress(f'Starting: "{self.name}".')
        return self

    def __exit__(
        self,
        exc_type: Optional[Type[BaseException]],
        exc_value: Optional[BaseException],
        traceback: Optional[TracebackType],
    ) -> bool:
        self.mark()
        execution_time_ns = perf_counter_ns() - self.start_time

        swallow_exception: bool = False
        if exc_type is None:
            self.succeeded = True
            self._logProgress(
                f'Finished: "{self.name}" in {format_time_ns(execution_time_ns)}.'
            )
        elif exc_type is not None and issubclass(exc_type, EarlyAbortException):
            self.succeeded = False
            self._logProgress(
                f'Processing of "{self.name}" aborted after {format_time_ns(execution_time_ns)}.',
            )
            swallow_exception = True
        else:
            # add message / log exc_value?
            self.succeeded = False
            self._logProgress(
                f'Processing of "{self.name}" finished abnormally after {format_time_ns(execution_time_ns)}.',
                Severity.ERROR,
            )
        return swallow_exception

    def _logProgress(self, message: str, severity: Severity = Severity.INFO) -> None:
        self._resultsBuilder.addMessage(message, severity, MessageType.Progress)
        if self.console:
            print(message)

    def addDevInfoMessage(self, message: str) -> None:
        self._resultsBuilder.addMessage(message, Severity.INFO, MessageType.DevInfo)

    def mark(
        self, newSectionName: Optional[str] = None, additionalInfo: str = ""
    ) -> None:
        now = perf_counter_ns()
        if self.current_section_name is not None:
            execution_time_ns = now - self.current_section_start_time
            self._logProgress(
                f"Finished: [{self.current_section_name}] in {format_time_ns(execution_time_ns)}."
            )

        if newSectionName is not None:
            self.current_section_name = newSectionName
            self.current_section_start_time = now
            self._logProgress(
                f"Starting: [{self.current_section_name}]. {additionalInfo}"
            )
        return
