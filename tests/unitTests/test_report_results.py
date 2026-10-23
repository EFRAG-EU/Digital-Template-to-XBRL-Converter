"""StepResults, ValidationResults and ReportResults: a conversion and the steps
run on its report afterwards, as the results page and the downloads see them."""

import pytest

from mireport.conversionresults import (
    ConversionResults,
    Message,
    MessageType,
    ReportResults,
    Section,
    Severity,
    Stage,
    Step,
    StepResults,
    StepState,
    ValidationResults,
)


def message(severity: Severity, messageType=MessageType.XbrlValidation) -> Message:
    return Message(f"{severity} message", severity, messageType)


def converted(*messages: Message) -> ConversionResults:
    successful = all(m.severity is not Severity.ERROR for m in messages)
    return ConversionResults(
        "id", [Section(Stage.CONVERSION, list(messages))], 0, 0, successful
    )


def validated(step: Step, *messages: Message) -> ValidationResults:
    return ValidationResults.done(step, messages, 1_000_000)


def bothValidated(*packageMessages: Message) -> dict[Step, StepResults]:
    return {
        Step.PACKAGE: validated(Step.PACKAGE, *packageMessages),
        Step.XBRL_JSON: validated(Step.XBRL_JSON),
    }


class TestStepResults:
    @pytest.mark.parametrize(
        "results",
        [
            ValidationResults.pending(Step.PACKAGE),
            ValidationResults.running(Step.XBRL_JSON),
            validated(Step.PACKAGE, message(Severity.WARNING)),
            ValidationResults.failed(Step.XBRL_JSON, ValueError("boom")),
            StepResults.done(Step.VIEWER, [], 5),
        ],
        ids=lambda r: f"{r.step}-{r.state}",
    )
    def test_dict_round_trip_keeps_class_and_contents(self, results: StepResults):
        back = StepResults.fromDict(results.toDict())

        assert type(back) is type(results)
        assert (back.step, back.state, back.durationNs) == (
            results.step,
            results.state,
            results.durationNs,
        )
        assert [m.toDict() for m in back.messages] == [
            m.toDict() for m in results.messages
        ]

    def test_failed_is_an_error_and_finished(self):
        failed = ValidationResults.failed(Step.PACKAGE, ValueError("boom"))

        assert failed.isFinished
        assert not failed.isValid
        [error] = failed.userMessages
        assert error.messageText == "Failed: boom"
        assert error.messageType is MessageType.XbrlValidation

    def test_true_without_messages(self):
        assert ValidationResults.running(Step.PACKAGE)

    def test_viewer_failure_is_not_a_validation_message(self):
        [error] = StepResults.failed(Step.VIEWER, OSError("disk")).messages
        assert error.messageType is MessageType.Conversion

    @pytest.mark.parametrize("state", [StepState.PENDING, StepState.RUNNING])
    def test_unfinished_is_not_valid(self, state: StepState):
        assert not ValidationResults(Step.PACKAGE, state).isValid


class TestReportResults:
    def test_missing_steps_are_pending(self):
        results = ReportResults(converted())

        assert all(s.state is StepState.PENDING for s in results.steps.values())
        assert isinstance(results.steps[Step.PACKAGE], ValidationResults)
        assert type(results.steps[Step.VIEWER]) is StepResults

    def test_a_running_step_stays_running(self):
        """A step with no messages yet is still the one given."""
        running = ValidationResults.running(Step.PACKAGE)
        results = ReportResults(converted(), {Step.PACKAGE: running})
        assert results.steps[Step.PACKAGE] is running

    def test_pending_validation_has_no_rag_yet_but_allows_downloads(self):
        results = ReportResults(converted(), {Step.PACKAGE: validated(Step.PACKAGE)})

        assert results.validationPending
        assert results.validationRAG is None
        assert results.downloadsAllowed
        assert not results.isComplete

    @pytest.mark.parametrize(
        ("severity", "colour", "allowed"),
        [
            (Severity.INFO, "green", True),
            (Severity.WARNING, "amber", True),
            (Severity.ERROR, "red", False),
        ],
    )
    def test_finished_validation_sets_rag_and_downloads(
        self, severity: Severity, colour: str, allowed: bool
    ):
        results = ReportResults(converted(), bothValidated(message(severity)))

        assert not results.validationPending
        assert results.validationRAG is not None
        assert results.validationRAG[colour]
        assert results.downloadsAllowed is allowed

    def test_an_error_disables_downloads_before_the_rest_finish(self):
        results = ReportResults(
            converted(),
            {Step.PACKAGE: validated(Step.PACKAGE, message(Severity.ERROR))},
        )

        assert results.validationPending
        assert results.hasValidationErrors
        assert not results.downloadsAllowed

    def test_failed_conversion_is_complete_without_steps(self):
        results = ReportResults(
            converted(message(Severity.ERROR, MessageType.Conversion))
        )

        assert results.conversionRAG["red"]
        assert not results.validationPending
        assert not results.downloadsAllowed
        assert results.isComplete

    def test_complete_once_every_step_finishes(self):
        steps = bothValidated() | {Step.VIEWER: StepResults.done(Step.VIEWER, [], 1)}
        assert ReportResults(converted(), steps).isComplete

    def test_conversion_rag_ignores_validation(self):
        results = ReportResults(converted(), bothValidated(message(Severity.ERROR)))
        assert results.conversionRAG["green"]

    def test_messages_are_conversions_then_steps_in_order(self):
        conversionMessage = message(Severity.WARNING, MessageType.Conversion)
        packageMessage = message(Severity.ERROR)
        jsonMessage = message(Severity.WARNING)
        results = ReportResults(
            converted(conversionMessage),
            {
                Step.XBRL_JSON: validated(Step.XBRL_JSON, jsonMessage),
                Step.PACKAGE: validated(Step.PACKAGE, packageMessage),
            },
        )

        assert results.userMessages == [conversionMessage, packageMessage, jsonMessage]

    def test_steps_are_sections_after_the_conversions(self):
        conversion = converted(message(Severity.WARNING, MessageType.Conversion))
        package = validated(Step.PACKAGE, message(Severity.ERROR))
        results = ReportResults(conversion, {Step.PACKAGE: package})

        assert [s.stage for s in results.sections] == [
            Stage.CONVERSION,
            Stage.PACKAGE,
            Stage.XBRL_JSON,
            Stage.VIEWER,
        ]
        assert results.sections[1] is package
        assert results.sections[1].label == "XBRL report validation"
        assert results.sections[1].durationNs == 1_000_000

    def test_a_pending_step_is_an_empty_section(self):
        [*_, viewer] = ReportResults(converted()).sections

        assert isinstance(viewer, Section)
        assert viewer.stage is Stage.VIEWER
        assert viewer.state is StepState.PENDING
        assert viewer.messages == []
        assert viewer.durationNs is None

    def test_done_keeps_its_detail_for_developers_only(self):
        done = ValidationResults.done(Step.PACKAGE, [], 5, detail="Using Arelle")

        assert done.userMessages == []
        [detail] = done.developerMessages
        assert (detail.messageText, detail.messageType) == (
            "Using Arelle",
            MessageType.DevInfo,
        )
