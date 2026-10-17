"""validateReportPackage(getReportAsJson=True) must be validation plus the
xBRL-JSON in one Arelle session, so callers needing both pay for one."""

from __future__ import annotations

from pathlib import Path

import pytest

from mireport.arelle.report_info import ArelleReportProcessor
from mireport.arelle.support import ArelleProcessingResult
from mireport.conversionresults import ConversionResultsBuilder, Severity
from mireport.data.disclosures import VSME_DEFAULTS
from mireport.filesupport import FilelikeAndFileName
from mireport.taxonomy import loadBuiltInTaxonomyJSON
from mireport.xlsx_template_reader.processor import XlsxProcessor

SAMPLE = Path("tests/data/VSME-Digital-Template-Sample-1.2.0.xlsx")

pytestmark = pytest.mark.slow


@pytest.fixture(scope="module")
def package() -> FilelikeAndFileName:
    loadBuiltInTaxonomyJSON()
    processor = XlsxProcessor.from_file(
        SAMPLE.open("rb"), ConversionResultsBuilder(), VSME_DEFAULTS, outputLocale=None
    )
    return processor.createReport().getInlineReportPackage()


@pytest.fixture(scope="module")
def arelle() -> ArelleReportProcessor:
    return ArelleReportProcessor(workOffline=False)


@pytest.fixture(scope="module")
def validated(
    arelle: ArelleReportProcessor, package: FilelikeAndFileName
) -> ArelleProcessingResult:
    return arelle.validateReportPackage(package)


@pytest.fixture(scope="module")
def validatedWithJson(
    arelle: ArelleReportProcessor, package: FilelikeAndFileName
) -> ArelleProcessingResult:
    return arelle.validateReportPackage(package, getReportAsJson=True)


def _errorsAndWarnings(result: ArelleProcessingResult) -> set[str]:
    return {
        m.messageText
        for m in result.messages
        if m.severity in (Severity.ERROR, Severity.WARNING)
    }


def test_json_is_not_produced_unless_asked_for(
    validated: ArelleProcessingResult,
) -> None:
    assert not validated.has_json


def test_json_is_produced_when_asked_for(
    validatedWithJson: ArelleProcessingResult,
) -> None:
    assert validatedWithJson.has_json, validatedWithJson.log_lines
    assert validatedWithJson.xbrl_json.filename.endswith(".json")


def test_the_same_session_still_validates(
    validated: ArelleProcessingResult, validatedWithJson: ArelleProcessingResult
) -> None:
    assert not validatedWithJson.has_exceptions, validatedWithJson.log_lines
    assert _errorsAndWarnings(validatedWithJson) == _errorsAndWarnings(validated)
