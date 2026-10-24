"""StepStore and runSteps: the steps run on a converted report, kept where every
process serving the app can see them."""

import pytest
from cachelib.file import FileSystemCache

from digital_converter_webapp.background import StepStore, runSteps
from mireport.arelle.support import ArelleProcessingResult
from mireport.conversionresults import (
    Message,
    MessageType,
    Severity,
    Step,
    StepResults,
    StepState,
    ValidationResults,
)
from mireport.filesupport import FilelikeAndFileName

PACKAGE = FilelikeAndFileName(fileContent=b"zip", filename="report.zip")
XBRL_JSON = FilelikeAndFileName(fileContent=b"{}", filename="report.json")
VIEWER = FilelikeAndFileName(fileContent=b"<html/>", filename="report_viewer.html")


@pytest.fixture
def cache(tmp_path):
    return FileSystemCache(str(tmp_path))


@pytest.fixture
def store(cache):
    return StepStore(cache, 3600)


class FakeArelle:
    """Records what it was asked to validate; ``failing`` methods raise."""

    def __init__(self, *failing: str) -> None:
        self.failing = failing
        self.calls: list[str] = []

    def _run(self, name: str) -> ArelleProcessingResult:
        self.calls.append(name)
        if name in self.failing:
            raise RuntimeError(f"{name} broke")
        return ArelleProcessingResult()

    def validateReportPackage(self, source):
        assert source is PACKAGE
        return self._run("validateReportPackage")

    def validateXbrlJson(self, document):
        assert document is XBRL_JSON
        return self._run("validateXbrlJson")

    def generateInlineViewer(self, source):
        assert source is PACKAGE
        result = self._run("generateInlineViewer")
        result._viewer = VIEWER
        return result


ALL_CALLS = ["validateReportPackage", "validateXbrlJson", "generateInlineViewer"]


class TestStepStore:
    def test_round_trips_step_results(self, store):
        warned = ValidationResults.done(
            Step.PACKAGE,
            [Message("careful", Severity.WARNING, MessageType.XbrlValidation)],
            7,
        )
        store.save("c1", warned)

        loaded = store.load("c1")

        assert list(loaded) == [Step.PACKAGE]
        assert isinstance(loaded[Step.PACKAGE], ValidationResults)
        assert loaded[Step.PACKAGE].toDict() == warned.toDict()

    def test_conversions_are_kept_apart(self, store):
        store.save("c1", StepResults.running(Step.VIEWER))
        assert store.load("c2") == {}

    def test_one_claim_across_processes(self, cache):
        """Two stores on one cache stand in for two processes."""
        first, second = StepStore(cache, 3600), StepStore(cache, 3600)

        assert first.claim("c1")
        assert not second.claim("c1")
        assert second.claim("c2")

        first.release("c1")
        assert second.claim("c1")


class TestRunSteps:
    def test_runs_every_step_in_order_and_releases_the_claim(self, store):
        arelle = FakeArelle()
        assert store.claim("c1")

        runSteps(store, "c1", PACKAGE, XBRL_JSON, arelle)

        assert arelle.calls == ALL_CALLS
        steps = store.load("c1")
        assert [s.state for s in steps.values()] == [StepState.DONE] * len(Step)
        assert steps[Step.PACKAGE].isValid
        assert [m.messageText for m in steps[Step.VIEWER].userMessages] == [
            "Viewer built."
        ]
        assert store.loadViewer("c1") == (VIEWER, {})
        assert store.claim("c1")

    def test_skips_steps_already_finished(self, store):
        store.save("c1", ValidationResults.done(Step.PACKAGE, [], 1))
        arelle = FakeArelle()

        runSteps(store, "c1", PACKAGE, XBRL_JSON, arelle)

        assert arelle.calls == ALL_CALLS[1:]

    def test_reruns_a_step_left_running(self, store):
        store.save("c1", ValidationResults.running(Step.PACKAGE))
        arelle = FakeArelle()

        runSteps(store, "c1", PACKAGE, XBRL_JSON, arelle)

        assert arelle.calls == ALL_CALLS

    def test_a_failing_step_fails_alone(self, store):
        arelle = FakeArelle("validateReportPackage")

        runSteps(store, "c1", PACKAGE, XBRL_JSON, arelle)

        steps = store.load("c1")
        assert steps[Step.PACKAGE].state is StepState.FAILED
        [error] = steps[Step.PACKAGE].userMessages
        assert error.messageText == "Failed: validateReportPackage broke"
        assert steps[Step.PACKAGE].label == "XBRL report validation"
        assert steps[Step.XBRL_JSON].state is StepState.DONE
        assert arelle.calls == ["validateReportPackage", "validateXbrlJson"]

    def test_no_viewer_once_validation_finds_errors(self, store):
        """The report isn't offered at all, so neither is its viewer."""
        runSteps(store, "c1", PACKAGE, XBRL_JSON, FakeArelle("validateXbrlJson"))

        viewer = store.load("c1")[Step.VIEWER]
        assert viewer.state is StepState.SKIPPED
        assert [m.messageText for m in viewer.userMessages] == [
            "Not run: validation found errors."
        ]
        assert store.loadViewer("c1") is None

    def test_forget_drops_the_viewer_too(self, store):
        runSteps(store, "c1", PACKAGE, XBRL_JSON, FakeArelle())

        store.forget("c1")

        assert store.load("c1") == {}
        assert store.loadViewer("c1") is None
