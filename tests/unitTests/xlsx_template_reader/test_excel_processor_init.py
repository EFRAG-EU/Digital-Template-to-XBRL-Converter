from pathlib import Path

import pytest

from mireport.conversionresults import ConversionResultsBuilder
from mireport.data.disclosures import VSME_DEFAULTS
from mireport.xlsx_template_reader.processor import XlsxProcessor

SAMPLE = (
    Path(__file__).parent.parent.parent
    / "data"
    / "VSME-Digital-Template-Sample-1.2.0.xlsx"
)


def _builder() -> ConversionResultsBuilder:
    return ConversionResultsBuilder(consoleOutput=False)


class TestInitRejectsNonWorkbook:
    """The constructor takes a loaded Workbook; paths/file-likes must go
    through from_file/from_bytes and are rejected with a helpful TypeError."""

    def test_init_rejects_path(self):
        with pytest.raises(TypeError):
            XlsxProcessor(SAMPLE, _builder(), VSME_DEFAULTS)  # type: ignore[arg-type]

    def test_init_rejects_filelike(self):
        with SAMPLE.open("rb") as fh, pytest.raises(TypeError):
            XlsxProcessor(fh, _builder(), VSME_DEFAULTS)  # type: ignore[arg-type]


class TestFromBytesVsFromFile:
    @pytest.mark.slow
    def test_same_fact_count(self):
        # Each createReport() closes the reader so these must be fresh instances.
        ep_bytes = XlsxProcessor.from_bytes(
            SAMPLE.read_bytes(), _builder(), VSME_DEFAULTS
        )
        report_bytes = ep_bytes.createReport()

        ep_file = XlsxProcessor.from_file(SAMPLE, _builder(), VSME_DEFAULTS)
        report_file = ep_file.createReport()

        assert report_bytes.factCount == report_file.factCount
