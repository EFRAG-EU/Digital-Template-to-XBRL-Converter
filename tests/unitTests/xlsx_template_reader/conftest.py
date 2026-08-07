from pathlib import Path

import pytest

from mireport.conversionresults import ConversionResultsBuilder
from mireport.data.disclosures import VSME_DEFAULTS
from mireport.taxonomy import loadBuiltInTaxonomyJSON
from mireport.xlsx_template_reader.processor import XlsxProcessor
from mireport.xlsx_template_reader.util import loadExcelFromPathOrFileLike

SAMPLE = (
    Path(__file__).parent.parent.parent
    / "data"
    / "VSME-Digital-Template-Sample-1.2.0.xlsx"
)


def _builder() -> ConversionResultsBuilder:
    return ConversionResultsBuilder(consoleOutput=False)


@pytest.fixture(scope="session", autouse=True)
def _load_taxonomies():
    loadBuiltInTaxonomyJSON()


@pytest.fixture(scope="module")
def sample_workbook():
    wb = loadExcelFromPathOrFileLike(SAMPLE)
    yield wb
    wb.close()


@pytest.fixture(scope="module")
def processor_from_path():
    ep = XlsxProcessor.from_file(SAMPLE, _builder(), VSME_DEFAULTS)
    yield ep


@pytest.fixture(scope="module")
def processor_from_bytes():
    ep = XlsxProcessor.from_bytes(SAMPLE.read_bytes(), _builder(), VSME_DEFAULTS)
    yield ep


@pytest.fixture(scope="module")
def processor_from_filelike():
    with SAMPLE.open("rb") as fh:
        ep = XlsxProcessor.from_file(fh, _builder(), VSME_DEFAULTS)
    yield ep
