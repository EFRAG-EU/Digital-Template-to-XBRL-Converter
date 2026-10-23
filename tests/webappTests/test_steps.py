"""The steps run on a converted report, through the results page."""

import pytest

from digital_converter_webapp.background import StepStore
from mireport.arelle.report_info import ArelleReportProcessor
from mireport.arelle.support import ArelleProcessingResult
from mireport.conversionresults import Step, StepState


@pytest.fixture
def validations(monkeypatch, skipArelle) -> list[str]:
    """Fake, counted validations: the package's carries an ERROR, the
    xBRL-JSON's is clean."""
    calls: list[str] = []
    fakePackage = ArelleReportProcessor.validateReportPackage

    def validateReportPackage(self, source, **kwargs):
        calls.append("package")
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
