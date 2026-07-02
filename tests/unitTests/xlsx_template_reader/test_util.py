"""Tests for xlsx_template_reader.util helpers."""

from datetime import date, datetime

import pytest
from openpyxl import Workbook

from mireport.xlsx_template_reader.util import get_decimal_places, getDateFromValue


def _cell(number_format: str):
    wb = Workbook()
    assert wb.active is not None
    cell = wb.active.cell(row=1, column=1)
    cell.value = 1
    cell.number_format = number_format
    return cell


@pytest.mark.parametrize(
    "number_format,expected",
    [
        ("0.00", 2),
        ("#,##0.000", 3),
        ("0.0%", 1),
        ("0.000%", 3),
        ("0.00E+00", 2),
        ("General", "INF"),
        ("0", "INF"),
        ("#,##0", "INF"),
    ],
)
def test_get_decimal_places(number_format, expected):
    assert get_decimal_places(_cell(number_format)) == expected


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
