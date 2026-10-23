from __future__ import annotations

import re
import uuid
from enum import StrEnum
from functools import lru_cache
from time import perf_counter_ns
from types import MappingProxyType
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping
    from collections.abc import Set as AbstractSet
    from types import TracebackType
    from typing import Self

from markupsafe import Markup

from mireport.exceptions import EarlyAbortException
from mireport.stringutil import format_time_ns, str_to_markupsafe
from mireport.taxonomy import Concept
from mireport.xml import QName


class MessageText(str):
    """str subclass that renders as HTML-safe (escaped, newlines as <br />) in Jinja2 templates."""

    def __html__(self) -> Markup:
        return str_to_markupsafe(self)


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
    def fromLogLevelString(cls, level: str, *, default: Self | None = None) -> Self:
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


# N.B. frozen so they are safe to use as argument defaults, which are evaluated
# once and then shared by every call.
ALL_MESSAGE_TYPES: frozenset[MessageType] = frozenset(MessageType.all())
ALL_SEVERITIES: frozenset[Severity] = frozenset(Severity.all())


class Message:
    def __init__(
        self,
        messageText: str,
        severity: Severity,
        messageType: MessageType,
        conceptQName: str | None = None,
        excelReference: str | None = None,
    ):
        self.messageText: MessageText = MessageText(messageText)
        self.severity: Severity = severity
        self.messageType: MessageType = messageType
        self.conceptQName: str | None = conceptQName
        self.excelReference: str | None = excelReference

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
            "m": str(self.messageText),
            "s": self.severity.name,
            "mt": self.messageType.name,
            "c": self.conceptQName,
            "e": self.excelReference,
        }
        return d


class Results:
    """Messages, and what they add up to: severities, RAG status and the
    user and developer views of them."""

    def __init__(self, messages: list[Message]) -> None:
        self.messages: list[Message] = messages

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
        wantedMessageTypes: AbstractSet[MessageType] = ALL_MESSAGE_TYPES,
        wantedMessageSeverities: AbstractSet[Severity] = ALL_SEVERITIES,
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
        )


class ConversionResults(Results):
    def __init__(
        self,
        conversionId: str,
        messages: list[Message],
        cellsQueried: int,
        cellsPopulated: int,
        conversionSuccessful: bool,
    ) -> None:
        super().__init__(messages)
        self.conversionId = conversionId
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
        self,
        conversionId: str | None = None,
        consoleOutput: bool = False,
        *,
        excelParsingWarningsAsDevInfo: bool = False,
    ) -> None:
        if conversionId is not None:
            self.conversionId = conversionId
        else:
            self.conversionId = str(uuid.uuid4())
        self.messages: list[Message] = []
        self.cellsQueriedBuilder: set[tuple[str, int, int]] = set()
        self.cellsPopulatedBuilder: set[tuple[str, int, int]] = set()
        self.consoleOutput = consoleOutput
        # A deployment can keep Excel parsing warnings out of the user's view
        # (for a demo, say): they become developer messages, still warnings.
        self.excelParsingWarningsAsDevInfo = excelParsingWarningsAsDevInfo

    def addCellQueries(self, delta: Iterable[tuple[str, int, int]]) -> Self:
        self.cellsQueriedBuilder.update(delta)
        return self

    def addCellsWithData(self, delta: Iterable[tuple[str, int, int]]) -> Self:
        self.cellsPopulatedBuilder.update(delta)
        return self

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
        taxonomy_concept: QName | Concept | None = None,
        excel_reference: str | None = None,
    ) -> Self:
        concept_str_or_none: str | None
        if taxonomy_concept is None:
            concept_str_or_none = taxonomy_concept
        else:
            concept_str_or_none = str(taxonomy_concept)
        self.messages.append(
            Message(
                message_text,
                severity,
                self._reclassify(severity, message_type),
                concept_str_or_none,
                excel_reference,
            )
        )
        return self

    def processingContext(self, name: str) -> ProcessingContext:
        return ProcessingContext(self, name)

    def addMessages(self, messages: Iterable[Message]) -> Self:
        for m in messages:
            mtype = self._reclassify(m.severity, m.messageType)
            if mtype is not m.messageType:
                m = Message(
                    m.messageText, m.severity, mtype, m.conceptQName, m.excelReference
                )
            self.messages.append(m)
        return self

    def _reclassify(self, severity: Severity, messageType: MessageType) -> MessageType:
        if (
            self.excelParsingWarningsAsDevInfo
            and severity is Severity.WARNING
            and messageType is MessageType.ExcelParsing
        ):
            return MessageType.DevInfo
        return messageType

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


class Step(StrEnum):
    """The work done on a converted report after conversion, in the order it is
    done."""

    PACKAGE = "package"
    XBRL_JSON = "xbrlJson"
    VIEWER = "viewer"

    @property
    def isValidation(self) -> bool:
        return self is not Step.VIEWER

    @property
    def label(self) -> str:
        return _STEP_LABELS[self]

    @property
    def resultsClass(self) -> type[StepResults]:
        return ValidationResults if self.isValidation else StepResults


_STEP_LABELS: Mapping[Step, str] = MappingProxyType(
    {
        Step.PACKAGE: "XBRL report validation",
        Step.XBRL_JSON: "xBRL-JSON validation",
        Step.VIEWER: "Viewer",
    }
)


class StepState(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"


class StepResults(Results):
    """One step's progress and, once it is finished, its messages."""

    def __init__(
        self,
        step: Step,
        state: StepState,
        messages: list[Message] | None = None,
        durationNs: int | None = None,
    ) -> None:
        super().__init__(messages if messages is not None else [])
        self.step = step
        self.state = state
        self.durationNs = durationNs

    @classmethod
    def pending(cls, step: Step) -> Self:
        return cls(step, StepState.PENDING)

    @classmethod
    def running(cls, step: Step) -> Self:
        return cls(step, StepState.RUNNING)

    @classmethod
    def done(
        cls,
        step: Step,
        messages: Iterable[Message],
        durationNs: int,
        *,
        detail: str = "",
    ) -> Self:
        finished = Message(
            f"Finished: [{step.label}] in {format_time_ns(durationNs)}. {detail}".rstrip(),
            Severity.INFO,
            MessageType.Progress,
        )
        return cls(step, StepState.DONE, [*messages, finished], durationNs)

    @classmethod
    def skipped(cls, step: Step, reason: str) -> Self:
        """Done without doing anything, because of ``reason``."""
        message = Message(
            f"Skipped: [{step.label}]. {reason}", Severity.INFO, MessageType.Progress
        )
        return cls(step, StepState.DONE, [message])

    @classmethod
    def failed(cls, step: Step, error: Exception) -> Self:
        messageType = (
            MessageType.XbrlValidation if step.isValidation else MessageType.Conversion
        )
        message = Message(f"{step.label} failed: {error}", Severity.ERROR, messageType)
        return cls(step, StepState.FAILED, [message])

    @property
    def isFinished(self) -> bool:
        return self.state in (StepState.DONE, StepState.FAILED)

    def toDict(self) -> dict:
        return {
            "step": self.step.value,
            "state": self.state.value,
            "m": [m.toDict() for m in self.messages],
            "t": self.durationNs,
        }

    @staticmethod
    def fromDict(stuff: dict) -> StepResults:
        """The results ``toDict`` gave, as the class that suits their step."""
        step = Step(stuff["step"])
        return step.resultsClass(
            step,
            StepState(stuff["state"]),
            [Message.fromDict(m) for m in stuff["m"]],
            stuff["t"],
        )


class ValidationResults(StepResults):
    """A validation step: its messages are those of the validator."""

    @property
    def isValid(self) -> bool:
        return self.isFinished and not self.hasErrors()


class ReportResults(Results):
    """A conversion and the steps run on its report afterwards, as one: what
    the results page shows and what decides whether the report is offered for
    download. Its messages are the conversion's, then each step's in order."""

    def __init__(
        self,
        conversion: ConversionResults,
        steps: Mapping[Step, StepResults] = MappingProxyType({}),
    ) -> None:
        self.conversion = conversion
        self.steps: dict[Step, StepResults] = {
            step: steps[step] if step in steps else step.resultsClass.pending(step)
            for step in Step
        }
        super().__init__(
            [
                *conversion.messages,
                *(m for results in self.steps.values() for m in results.messages),
            ]
        )

    @property
    def conversionId(self) -> str:
        return self.conversion.conversionId

    @property
    def conversionSuccessful(self) -> bool:
        return self.conversion.conversionSuccessful

    @property
    def conversionRAG(self) -> Mapping[str, bool]:
        return self.conversion.getRAG(withoutXBRLValidation=True)

    @property
    def validations(self) -> list[StepResults]:
        return [results for step, results in self.steps.items() if step.isValidation]

    @property
    def validationPending(self) -> bool:
        return self.conversionSuccessful and not all(
            v.isFinished for v in self.validations
        )

    @property
    def validationRAG(self) -> Mapping[str, bool] | None:
        """The validations' RAG status, or None until they have all finished."""
        if self.validationPending:
            return None
        return Results([m for v in self.validations for m in v.messages]).getRAG()

    @property
    def hasValidationErrors(self) -> bool:
        return any(v.hasErrors() for v in self.validations)

    @property
    def downloadsAllowed(self) -> bool:
        """Converted, and nothing found invalid so far: a download asked for
        while validation is still pending waits for it."""
        return self.conversionSuccessful and not self.hasValidationErrors

    @property
    def isComplete(self) -> bool:
        """Nothing more will change: every step has finished, or the conversion
        failed and so has none."""
        return not self.conversionSuccessful or all(
            s.isFinished for s in self.steps.values()
        )


class ProcessingContext:
    def __init__(self, resultsBuilder: ConversionResultsBuilder, name: str) -> None:
        self._resultsBuilder: ConversionResultsBuilder = resultsBuilder
        self.name: str = name
        self.succeeded: bool = False
        self.start_time: int
        self.current_section_start_time: int
        self.current_section_name: str | None = None
        self.console = self._resultsBuilder.consoleOutput

    def __enter__(self) -> Self:
        self.start_time = self.current_section_start_time = perf_counter_ns()
        self._logProgress(f'Starting: "{self.name}".')
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
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

    def mark(self, newSectionName: str | None = None, additionalInfo: str = "") -> None:
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
