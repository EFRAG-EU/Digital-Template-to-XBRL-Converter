import pytest

from mireport.xlsx_template_reader._constants import is_error_value


class TestIsErrorValue:
    @pytest.mark.parametrize(
        "value",
        ["#REF!", "#DIV/0!", "#NAME?", "#N/A", "#NULL!", "#NUM!", "#VALUE!", "#ERROR!"],
        ids=["ref", "div0", "name", "na", "null", "num", "value", "google-error"],
    )
    def test_error_values_detected(self, value: str) -> None:
        assert is_error_value(value)

    @pytest.mark.parametrize(
        "value",
        ["", "-", "Revenue", "0", "#VALUE", "REF!"],
        ids=["empty", "dash", "text", "zero", "no-bang", "no-hash"],
    )
    def test_non_error_values_not_detected(self, value: str) -> None:
        assert not is_error_value(value)
