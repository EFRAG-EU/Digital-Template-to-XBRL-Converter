from datetime import date, datetime
from pathlib import Path

import pytest

from mireport.conversionresults import ConversionResultsBuilder
from mireport.xlsx_template_reader._reader import (
    _IGNORED_DEFINED_NAME_PREFIXES,
    WorkbookReader,
    getDateFromValue,
    loadExcelFromPathOrFileLike,
)

SAMPLE = (
    Path(__file__).parent.parent.parent
    / "data"
    / "VSME-Digital-Template-Sample-1.2.0.xlsx"
)


def _results() -> ConversionResultsBuilder:
    return ConversionResultsBuilder(consoleOutput=False)


@pytest.fixture(scope="module")
def reader():
    wb = loadExcelFromPathOrFileLike(SAMPLE)
    yield WorkbookReader(wb, _results())
    wb.close()


class TestWorkbookReaderImport:
    def test_workbook_reader_importable(self):
        assert callable(WorkbookReader)

    def test_get_date_from_value_importable(self):
        assert callable(getDateFromValue)


class TestWorkbookReaderInit:
    def test_accepts_workbook_and_results(self, reader):
        assert reader is not None

    def test_has_get_single_cell(self):
        assert callable(WorkbookReader.getSingleCell)

    def test_has_get_single_value(self):
        assert hasattr(WorkbookReader, "getSingleValue")

    def test_has_get_single_string_value(self):
        assert hasattr(WorkbookReader, "getSingleStringValue")

    def test_has_get_single_date_value(self):
        assert hasattr(WorkbookReader, "getSingleDateValue")


class TestWorkbookReaderGetSingleStringValue:
    def test_template_name_returns_string(self, reader):
        val = reader.getSingleStringValue("template_version")
        assert isinstance(val, str)

    def test_missing_name_returns_fallback(self, reader):
        val = reader.getSingleStringValue("this_does_not_exist_xyz", fallbackValue="FB")
        assert val == "FB"


class TestWorkbookReaderUnusedAPI:
    def test_unused_defined_names_populated_on_init(self, reader):
        assert len(reader.unused_defined_names) > 0

    def test_excluded_prefixes_absent(self, reader):
        for dn in reader.unused_defined_names:
            assert not dn.name.startswith(_IGNORED_DEFINED_NAME_PREFIXES)

    def test_discard_unused(self):
        wb = loadExcelFromPathOrFileLike(SAMPLE)
        r = WorkbookReader(wb, _results())
        dn = next(iter(r.unused_defined_names))
        r.discard_unused(dn)
        assert dn not in r.unused_defined_names
        wb.close()


class TestGetDateFromValue:
    def test_date_passthrough(self):
        d = date(2024, 12, 31)
        assert getDateFromValue(d) == d

    def test_datetime_converted_to_date(self):
        assert getDateFromValue(datetime(2024, 6, 15, 10, 30)) == date(2024, 6, 15)

    def test_iso_string(self):
        assert getDateFromValue("2024-03-01") == date(2024, 3, 1)

    def test_slash_string_dmy(self):
        assert getDateFromValue("31/12/2023") == date(2023, 12, 31)

    def test_unsupported_string_raises(self):
        with pytest.raises(ValueError):
            getDateFromValue("not a date at all")

    def test_unsupported_type_raises(self):
        with pytest.raises(TypeError):
            getDateFromValue(42)
