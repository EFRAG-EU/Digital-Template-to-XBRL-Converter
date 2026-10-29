from __future__ import annotations

import re
import uuid
from collections import Counter
from contextlib import contextmanager
from enum import StrEnum
from functools import lru_cache
from time import perf_counter_ns
from types import MappingProxyType
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterable, Iterator, Mapping
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
        source: str | None = None,
    ):
        self.messageText: MessageText = MessageText(messageText)
        self.severity: Severity = severity
        self.messageType: MessageType = messageType
        self.conceptQName: str | None = conceptQName
        self.excelReference: str | None = excelReference
        # The file it is about, where its section covers several.
        self.source: str | None = source

    def __str__(self) -> str:
        bits = [
            f"{self.severity.value:{Severity.maxValueWidth()}s} : {self.messageType.value:{MessageType.maxValueWidth()}s} :"
        ]
        if self.source is not None:
            bits.append(f"{self.source}:")
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
        f = stuff.get("f")
        return cls(m, s, mt, c, e, f)

    def toDict(self) -> dict:
        d = {
            "m": str(self.messageText),
            "s": self.severity.name,
            "mt": self.messageType.name,
            "c": self.conceptQName,
            "e": self.excelReference,
            "f": self.source,
        }
        return d


class Results:
    """Messages, and what they add up to: severities, RAG status and the
    user and developer views of them."""

    def __init__(self, messages: list[Message]) -> None:
        self._messages: list[Message] = messages

    @property
    def messages(self) -> list[Message]:
        return self._messages

    def __len__(self) -> int:
        return len(self.messages)

    def __bool__(self) -> bool:
        """Results with no messages are still results: without this,
        ``__len__`` would make them false."""
        return True

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

    def shownMessages(self, developer: bool = False) -> list[Message]:
        """The developer's view, or the user's."""
        return self.developerMessages if developer else self.userMessages

    def severityCounts(self, developer: bool = False) -> str:
        """How many messages of each severity, worst first ("1 error,
        2 warnings"), or "no messages"."""
        counts = Counter(m.severity for m in self.shownMessages(developer))
        return (
            ", ".join(
                _counted(counts[severity], severity.value.lower())
                for severity in sorted(counts, key=Severity.key, reverse=True)
            )
            or "no messages"
        )

    def messagesBySource(
        self, developer: bool = False
    ) -> list[tuple[str | None, list[Message]]]:
        """The messages grouped by the file they are about, in the order each
        file first comes; those about no file in particular go first."""
        groups: dict[str | None, list[Message]] = {None: []}
        for m in self.shownMessages(developer):
            groups.setdefault(m.source, []).append(m)
        return [(source, group) for source, group in groups.items() if group]


def _counted(count: int, noun: str) -> str:
    plural = "" if count == 1 or noun == "info" else "s"
    return f"{count} {noun}{plural}"


class Stage(StrEnum):
    """What a section of the results is about: a stage of the conversion, or
    the input files it reads, in the order they come."""

    CONVERSION = "conversion"
    TAXONOMY = "taxonomy"
    WORKBOOK = "workbook"
    XBRL_JSON_INPUT = "xbrlJsonInput"
    PRIOR = "prior"
    WORD = "word"
    PDF = "pdf"
    IMAGES = "images"
    REPORT = "report"
    XBRL_JSON_OUTPUT = "xbrlJsonOutput"
    PACKAGE = "package"
    XBRL_JSON = "xbrlJson"
    VIEWER = "viewer"

    @property
    def label(self) -> str:
        return _STAGE_LABELS[self]


_STAGE_LABELS: Mapping[Stage, str] = MappingProxyType(
    {
        Stage.CONVERSION: "Conversion",
        Stage.TAXONOMY: "Taxonomy",
        Stage.WORKBOOK: "Digital Template",
        Stage.XBRL_JSON_INPUT: "xBRL-JSON",
        Stage.PRIOR: "Last year's report",
        Stage.WORD: "Word documents",
        Stage.PDF: "Supplementary PDFs",
        Stage.IMAGES: "Images",
        Stage.REPORT: "Inline XBRL report",
        Stage.XBRL_JSON_OUTPUT: "xBRL-JSON report",
        Stage.PACKAGE: "XBRL report validation",
        Stage.XBRL_JSON: "xBRL-JSON validation",
        Stage.VIEWER: "Viewer",
    }
)


class Section(Results):
    """The messages of one stage of a conversion, or of one input file, and
    how long it took."""

    def __init__(
        self,
        stage: Stage,
        messages: list[Message] | None = None,
        *,
        label: str | None = None,
        source: str | None = None,
        durationNs: int | None = None,
        skipped: bool = False,
    ) -> None:
        super().__init__(messages if messages is not None else [])
        self.stage = stage
        self.label: str = label if label is not None else stage.label
        self.source = source
        self.durationNs = durationNs
        self._skipped = skipped

    @classmethod
    def notRun(cls, stage: Stage, reason: str) -> Section:
        """A stage that was not run, because of ``reason``."""
        return cls(stage, [_notRunMessage(reason)], skipped=True)

    @property
    def isSkipped(self) -> bool:
        return self._skipped

    @property
    def title(self) -> str:
        """Its label, with the file it is about if there is one."""
        return self.label if self.source is None else f"{self.label} ({self.source})"

    def toDict(self) -> dict:
        return {
            "k": self.stage.value,
            "l": self.label,
            "f": self.source,
            "t": self.durationNs,
            "m": [m.toDict() for m in self.messages],
            "s": self._skipped,
        }

    @classmethod
    def fromDict(cls, stuff: dict) -> Section:
        return cls(
            Stage(stuff["k"]),
            [Message.fromDict(m) for m in stuff["m"]],
            label=stuff["l"],
            source=stuff["f"],
            durationNs=stuff["t"],
            skipped=stuff.get("s", False),
        )


def _notRunMessage(reason: str) -> Message:
    return Message(f"Not run: {reason}", Severity.INFO, MessageType.Conversion)


class ConversionResults(Results):
    """A conversion's results, in sections; its messages are all theirs, in
    order."""

    def __init__(
        self,
        conversionId: str,
        sections: list[Section],
        cellsQueried: int,
        cellsPopulated: int,
        conversionSuccessful: bool,
    ) -> None:
        self.conversionId = conversionId
        self.sections: list[Section] = sections
        self.cellsQueried: int = cellsQueried
        self.cellsPopulated: int = cellsPopulated
        self._conversionSuccessful: bool = conversionSuccessful

    @property
    def messages(self) -> list[Message]:
        return [m for section in self.sections for m in section.messages]

    @classmethod
    def fromDict(cls, stuff: dict) -> Self:
        id = stuff["id"]
        if "sections" in stuff:
            sections = [Section.fromDict(s) for s in stuff["sections"]]
        else:
            # Stored before there were sections: its messages are one.
            m = [Message.fromDict(m) for m in stuff["m"]]
            sections = [Section(Stage.CONVERSION, m)]
        q = stuff["q"]
        p = stuff["p"]
        success = stuff["success"]
        return cls(id, sections, q, p, success)

    def toDict(self) -> dict:
        d = {
            "id": self.conversionId,
            "sections": [s.toDict() for s in self.sections],
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
        self.sections: list[Section] = []
        self._openSection: Section | None = None
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
        source: str | None = None,
    ) -> Self:
        concept_str_or_none: str | None
        if taxonomy_concept is None:
            concept_str_or_none = taxonomy_concept
        else:
            concept_str_or_none = str(taxonomy_concept)
        self._currentSection().messages.append(
            Message(
                message_text,
                severity,
                self._reclassify(severity, message_type),
                concept_str_or_none,
                excel_reference,
                source,
            )
        )
        return self

    def addDevInfoMessage(self, message: str) -> Self:
        """Say something only a developer needs to know."""
        return self.addMessage(message, Severity.INFO, MessageType.DevInfo)

    def processingContext(self, name: str) -> ProcessingContext:
        return ProcessingContext(self, name)

    def addMessages(self, messages: Iterable[Message]) -> Self:
        section = self._currentSection()
        for m in messages:
            mtype = self._reclassify(m.severity, m.messageType)
            if mtype is not m.messageType:
                m = Message(
                    m.messageText,
                    m.severity,
                    mtype,
                    m.conceptQName,
                    m.excelReference,
                    m.source,
                )
            section.messages.append(m)
        return self

    def _reclassify(self, severity: Severity, messageType: MessageType) -> MessageType:
        if (
            self.excelParsingWarningsAsDevInfo
            and severity is Severity.WARNING
            and messageType is MessageType.ExcelParsing
        ):
            return MessageType.DevInfo
        return messageType

    @contextmanager
    def section(self, stage: Stage, *, source: str | None = None) -> Iterator[Section]:
        """Put the messages added until it closes in a section of their own,
        and record how long it took. Sections don't nest: opening the one
        already open carries on in it, and opening any other is a mistake."""
        if (current := self._openSection) is not None:
            if (current.stage, current.source) != (stage, source):
                raise ValueError(
                    f"{stage.label} opened inside {current.title}: sections don't nest."
                )
            yield current
            return
        section = Section(stage, source=source)
        self.sections.append(section)
        self._openSection = section
        self.printProgress(f"Starting: [{section.title}].")
        start = perf_counter_ns()
        try:
            yield section
        finally:
            section.durationNs = perf_counter_ns() - start
            self._openSection = None
            self.printProgress(
                f"Finished: [{section.title}] in {format_time_ns(section.durationNs)}."
            )

    def skip(self, stage: Stage, reason: str) -> Self:
        """Record that ``stage`` was not run, because of ``reason``."""
        self.sections.append(Section.notRun(stage, reason))
        self.printProgress(f"Not run: [{stage.label}]: {reason}")
        return self

    def printProgress(self, message: str) -> None:
        """Say how the run is going, live, when the results are for the
        console; it isn't kept."""
        if self.consoleOutput:
            print(message)

    def _currentSection(self) -> Section:
        """The open section, else a "Conversion" one for messages outside any."""
        if self._openSection is not None:
            return self._openSection
        if not self.sections or self.sections[-1].stage is not Stage.CONVERSION:
            self.sections.append(Section(Stage.CONVERSION))
        return self.sections[-1]

    @property
    def conversionSuccessful(self) -> bool:
        return self.getOverallSeverity(withoutXBRLValidation=True) is not Severity.ERROR

    def build(self) -> ConversionResults:
        return ConversionResults(
            self.conversionId,
            list(self.sections),
            len(self.cellsPopulatedBuilder),
            len(self.cellsPopulatedBuilder),
            self.conversionSuccessful,
        )


class Step(StrEnum):
    """The work done on a converted report after conversion, in the order it is
    done: the stages scheduled in the background, each its own section."""

    PACKAGE = "package"
    XBRL_JSON = "xbrlJson"
    VIEWER = "viewer"

    @property
    def isValidation(self) -> bool:
        return self is not Step.VIEWER

    @property
    def stage(self) -> Stage:
        return Stage(self.value)

    @property
    def label(self) -> str:
        return self.stage.label

    @property
    def resultsClass(self) -> type[StepResults]:
        return ValidationResults if self.isValidation else StepResults


class StepState(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    DONE = "done"
    SKIPPED = "skipped"
    FAILED = "failed"


class StepResults(Section):
    """One step's section: its progress and, once it is finished, its
    messages."""

    def __init__(
        self,
        step: Step,
        state: StepState,
        messages: list[Message] | None = None,
        durationNs: int | None = None,
    ) -> None:
        super().__init__(step.stage, messages, durationNs=durationNs)
        self.step = step
        self.state = state

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
        """Finished, with ``detail`` (what did it, say) for developers."""
        about = [Message(detail, Severity.INFO, MessageType.DevInfo)] if detail else []
        return cls(step, StepState.DONE, [*messages, *about], durationNs)

    @classmethod
    def skipped(cls, step: Step, reason: str) -> Self:
        """Not run, because of ``reason``."""
        return cls(step, StepState.SKIPPED, [_notRunMessage(reason)])

    @classmethod
    def failed(cls, step: Step, error: Exception) -> Self:
        messageType = (
            MessageType.XbrlValidation if step.isValidation else MessageType.Conversion
        )
        message = Message(f"Failed: {error}", Severity.ERROR, messageType)
        return cls(step, StepState.FAILED, [message])

    @property
    def isFinished(self) -> bool:
        return self.state in (StepState.DONE, StepState.SKIPPED, StepState.FAILED)

    @property
    def isSkipped(self) -> bool:
        return self.state is StepState.SKIPPED

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
    download. Its sections are the conversion's, then each step's in order, and
    so are its messages."""

    def __init__(
        self,
        conversion: ConversionResults,
        steps: Mapping[Step, StepResults] = MappingProxyType({}),
    ) -> None:
        self.conversion = conversion
        self.steps: dict[Step, StepResults] = {
            step: steps[step] if step in steps else self._notYetRun(step)
            for step in Step
        }
        super().__init__(
            [
                *conversion.messages,
                *(m for results in self.steps.values() for m in results.messages),
            ]
        )

    def _notYetRun(self, step: Step) -> StepResults:
        """Pending, or never to run: a failed conversion has no report."""
        if self.conversion.conversionSuccessful:
            return step.resultsClass.pending(step)
        return step.resultsClass.skipped(step, "the conversion failed.")

    @property
    def sections(self) -> list[Section]:
        return [*self.conversion.sections, *self.steps.values()]

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
    """A whole run: how long it took goes on the results, and an
    EarlyAbortException ends it quietly. Its progress is printed when the
    results are for the console, not kept."""

    def __init__(self, resultsBuilder: ConversionResultsBuilder, name: str) -> None:
        self._resultsBuilder: ConversionResultsBuilder = resultsBuilder
        self.name: str = name
        self.succeeded: bool = False
        self.start_time: int

    def __enter__(self) -> Self:
        self.start_time = perf_counter_ns()
        self._resultsBuilder.printProgress(f'Starting: "{self.name}".')
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> bool:
        execution_time_ns = perf_counter_ns() - self.start_time

        swallow_exception: bool = False
        if exc_type is None:
            self.succeeded = True
            self._resultsBuilder.printProgress(
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
        self._resultsBuilder.printProgress(message)

    def addDevInfoMessage(self, message: str) -> None:
        self._resultsBuilder.addDevInfoMessage(message)
