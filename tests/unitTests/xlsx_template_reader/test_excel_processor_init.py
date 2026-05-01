from pathlib import Path
from warnings import catch_warnings

import pytest
from openpyxl import load_workbook

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

    def test_from_bytes_returns_excel_processor(self):
        data = SAMPLE.read_bytes()
        ep = XlsxProcessor.from_bytes(data, _builder(), VSME_DEFAULTS)
        assert isinstance(ep, XlsxProcessor)

    def test_from_bytes_workbook_already_loaded(self):
        data = SAMPLE.read_bytes()
        ep = XlsxProcessor.from_bytes(data, _builder(), VSME_DEFAULTS)
        assert ep._reader is not None


class TestFromFile:
    def test_from_file_exists(self):
        assert callable(XlsxProcessor.from_file)

    def test_from_file_with_path(self):
        ep = XlsxProcessor.from_file(SAMPLE, _builder(), VSME_DEFAULTS)
        assert isinstance(ep, XlsxProcessor)

    def test_from_file_with_filelike(self):
        with SAMPLE.open("rb") as fh:
            ep = XlsxProcessor.from_file(fh, _builder(), VSME_DEFAULTS)
        assert isinstance(ep, XlsxProcessor)

    def test_from_file_workbook_already_loaded(self):
        ep = XlsxProcessor.from_file(SAMPLE, _builder(), VSME_DEFAULTS)
        assert ep._reader is not None


class TestInitTakesWorkbook:
    def test_init_accepts_workbook(self):
        with catch_warnings(action="ignore"):
            wb = load_workbook(SAMPLE, data_only=True, rich_text=True)
        ep = XlsxProcessor(wb, _builder(), VSME_DEFAULTS)
        assert isinstance(ep, XlsxProcessor)
        wb.close()

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
        data = SAMPLE.read_bytes()

        ep_bytes = XlsxProcessor.from_bytes(data, _builder(), VSME_DEFAULTS)
        report_bytes = ep_bytes.createReport()

        ep_file = XlsxProcessor.from_file(SAMPLE, _builder(), VSME_DEFAULTS)
        report_file = ep_file.createReport()

        assert report_bytes.factCount == report_file.factCount
