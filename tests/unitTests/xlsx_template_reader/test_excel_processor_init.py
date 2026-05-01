from pathlib import Path

import pytest

from mireport.conversionresults import ConversionResultsBuilder
from mireport.xlsx_template_reader.processor import VSME_DEFAULTS, XlsxProcessor

SAMPLE = (
    Path(__file__).parent.parent.parent
    / "data"
    / "VSME-Digital-Template-Sample-1.2.0.xlsx"
)


def _builder() -> ConversionResultsBuilder:
    return ConversionResultsBuilder(consoleOutput=False)


class TestFromBytes:
    def test_from_bytes_exists(self):
        assert callable(XlsxProcessor.from_bytes)

    def test_from_bytes_returns_excel_processor(self, processor_from_bytes):
        assert isinstance(processor_from_bytes, XlsxProcessor)

    def test_from_bytes_workbook_already_loaded(self, processor_from_bytes):
        assert processor_from_bytes._reader is not None


class TestFromFile:
    def test_from_file_exists(self):
        assert callable(XlsxProcessor.from_file)

    def test_from_file_with_path(self, processor_from_path):
        assert isinstance(processor_from_path, XlsxProcessor)

    def test_from_file_with_filelike(self, processor_from_filelike):
        assert isinstance(processor_from_filelike, XlsxProcessor)

    def test_from_file_workbook_already_loaded(self, processor_from_path):
        assert processor_from_path._reader is not None


class TestInitTakesWorkbook:
    def test_init_accepts_workbook(self, sample_workbook):
        ep = XlsxProcessor(sample_workbook, _builder(), VSME_DEFAULTS)
        assert isinstance(ep, XlsxProcessor)

    def test_init_rejects_path(self):
        with pytest.raises(TypeError):
            XlsxProcessor(SAMPLE, _builder(), VSME_DEFAULTS)  # type: ignore[arg-type]

    def test_init_rejects_filelike(self):
        with SAMPLE.open("rb") as fh:
            with pytest.raises(TypeError):
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
