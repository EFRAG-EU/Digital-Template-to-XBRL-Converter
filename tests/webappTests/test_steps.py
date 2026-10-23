"""The steps run on a converted report, through the results page."""

import threading
import time

import pytest
from cachelib.file import FileSystemCache

from digital_converter_webapp import create_app
from digital_converter_webapp.background import StepStore
from mireport.arelle.report_info import ArelleReportProcessor
from mireport.arelle.support import ArelleProcessingResult
from mireport.conversionresults import Step, StepState


@pytest.fixture
def gate():
    """The package's validation waits until this is open: it is, unless a test
    closes it. Opened again afterwards so no step is left waiting."""
    event = threading.Event()
    event.set()
    yield event
    event.set()


@pytest.fixture
def validations(monkeypatch, skipArelle, gate) -> list[str]:
    """Fake, counted validations: the package's carries an ERROR, the
    xBRL-JSON's is clean."""
    calls: list[str] = []
    fakePackage = ArelleReportProcessor.validateReportPackage

    def validateReportPackage(self, source, **kwargs):
        calls.append("package")
        assert gate.wait(timeout=60), "The test never let validation go ahead"
        return fakePackage(self, source, **kwargs)

    def validateXbrlJson(self, document):
        calls.append("xbrlJson")
        return ArelleProcessingResult()

    monkeypatch.setattr(
        ArelleReportProcessor, "validateReportPackage", validateReportPackage
    )
    monkeypatch.setattr(ArelleReportProcessor, "validateXbrlJson", validateXbrlJson)
    return calls


def test_results_page_shows_the_steps_outcome(client, upload_id, validations):
    conv_id = upload_id(client)

    page = client.get(f"/conversions/{conv_id}").get_data(as_text=True)

    assert validations == ["package", "xbrlJson"]
    assert "The generated XBRL report has validation ERRORS" in page
    assert "Arelle validation was not run (test stub)" in page


def test_lost_steps_run_again(app, client, upload_id, validations):
    conv_id = upload_id(client)
    client.get(f"/conversions/{conv_id}")
    with app.app_context():
        store = StepStore.fromFlask()
        store.forget(conv_id)

        page = client.get(f"/conversions/{conv_id}").get_data(as_text=True)

        assert validations == ["package", "xbrlJson"] * 2
        assert "The generated XBRL report has validation ERRORS" in page
        assert all(s.state is StepState.DONE for s in store.load(conv_id).values())
        assert set(store.load(conv_id)) == set(Step)


def test_finished_steps_are_not_run_again(client, upload_id, validations):
    conv_id = upload_id(client)
    client.get(f"/conversions/{conv_id}")
    client.get(f"/conversions/{conv_id}")

    assert validations == ["package", "xbrlJson"]


def test_deleting_a_conversion_forgets_its_steps(app, client, upload_id, validations):
    conv_id = upload_id(client)
    client.get(f"/conversions/{conv_id}")

    client.post(f"/delete/{conv_id}")

    with app.app_context():
        assert StepStore.fromFlask().load(conv_id) == {}


def test_status_of_a_finished_conversion(client, upload_id, validations):
    conv_id = upload_id(client)
    client.get(f"/conversions/{conv_id}")

    status = client.get(f"/conversions/{conv_id}/status").get_json()

    assert status["complete"] is True
    assert "validation ERRORS" in status["html"]["validation-status"]
    assert 'id="downloads"' in status["html"]["downloads"]
    assert "Arelle validation was not run (test stub)" in status["html"]["messages"]


def test_status_of_an_unknown_conversion(client):
    response = client.get("/conversions/no-such-conversion/status")
    assert response.status_code == 404


@pytest.fixture(scope="module")
def backgroundClient(tmp_path_factory):
    """A client of an app that runs the steps in the background, as deployed."""
    app = create_app(
        {
            "TESTING": True,
            "SESSION_CACHELIB": FileSystemCache(
                str(tmp_path_factory.mktemp("background-sessions"))
            ),
            "SECRET_KEY": "test-secret",
        }
    )
    return app.test_client()


def waitUntilComplete(client, conv_id: str) -> dict:
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        status = client.get(f"/conversions/{conv_id}/status").get_json()
        if status["complete"]:
            return status
        time.sleep(0.1)
    raise AssertionError("The steps never finished")


def test_results_page_is_shown_before_validation_finishes(
    backgroundClient, upload_id, validations, gate
):
    gate.clear()
    conv_id = upload_id(backgroundClient)

    page = backgroundClient.get(f"/conversions/{conv_id}").get_data(as_text=True)

    assert 'data-state="pending"' in page
    assert f"/conversions/{conv_id}/status" in page
    assert "Validating the generated XBRL report" in page
    status = backgroundClient.get(f"/conversions/{conv_id}/status").get_json()
    assert status["complete"] is False

    gate.set()
    status = waitUntilComplete(backgroundClient, conv_id)

    assert "validation ERRORS" in status["html"]["validation-status"]
    assert "Arelle validation was not run (test stub)" in status["html"]["messages"]
    page = backgroundClient.get(f"/conversions/{conv_id}").get_data(as_text=True)
    assert 'data-state="complete"' in page
    assert validations == ["package", "xbrlJson"]
