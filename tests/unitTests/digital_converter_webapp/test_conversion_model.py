from datetime import timedelta

import pytest

from digital_converter_webapp.conversion import Conversion, ConversionStore
from digital_converter_webapp.forms import first_str
from digital_converter_webapp.template_helpers import format_timedelta
from mireport.filesupport import FilelikeAndFileName


def _make(id: str = "abc") -> Conversion:
    return Conversion({"id": id})


class TestConversion:
    def test_round_trips_excel_through_primitives(self) -> None:
        conv = _make()
        upload = FilelikeAndFileName(fileContent=b"xxxx", filename="r.xlsx")
        conv.excel = upload

        assert isinstance(conv.raw["excel"], tuple)
        assert conv.raw["excel"] == (b"xxxx", "r.xlsx")

        reloaded = Conversion({"id": "abc", "excel": list(conv.raw["excel"])})
        assert reloaded.excel is not None
        assert reloaded.excel.filename == "r.xlsx"
        assert reloaded.excel.fileContent == b"xxxx"

    def test_external_values_round_trip(self) -> None:
        conv = _make()
        conv.external_values = {
            "ns:foo": FilelikeAndFileName(fileContent=b"doc", filename="a.docx")
        }
        reloaded = Conversion({"id": "x", "external_values": conv.raw["external_values"]})
        assert reloaded.has_external_values
        assert reloaded.external_values["ns:foo"].filename == "a.docx"

    def test_migration_outcome_setter_accepts_strenum_and_str(self) -> None:
        from digital_converter_webapp.migration import MigrationOutcome

        conv = _make()
        conv.migration_outcome = MigrationOutcome.MIGRATION_OPTIONAL
        assert conv.raw["migration_outcome"] == "migration_optional"
        conv.migration_outcome = "success"
        assert conv.migration_outcome == "success"

    def test_partial_fact_concepts_setter_copies(self) -> None:
        conv = _make()
        src = [{"qname": "n:q", "label": "Q"}]
        conv.partial_fact_concepts = src
        src[0]["label"] = "mutated"
        assert conv.partial_fact_concepts[0]["label"] == "Q"

    def test_excel_setter_rejects_wrong_type(self) -> None:
        conv = _make()
        with pytest.raises(TypeError):
            conv.excel = "not a file"  # type: ignore[assignment]


class TestConversionStore:
    def test_create_get_contains_delete(self) -> None:
        sess: dict = {}
        store = ConversionStore(sess)
        assert "abc" not in store
        conv = store.create("abc")
        assert isinstance(conv, Conversion)
        assert conv.id == "abc"
        assert "abc" in store
        fetched = store.get("abc")
        assert fetched is not None and fetched.id == "abc"
        store.delete("abc")
        assert "abc" not in store
        assert store.get("missing") is None

    def test_active_filters_internals_and_migration_required(self) -> None:
        sess: dict = {
            "_permanent": True,
            "csrf_token": "x",
            "captcha_answer": 5,
            "ok": {"id": "ok"},
            "old": {"id": "old", "migration_outcome": "migration_required"},
        }
        store = ConversionStore(sess)
        active = store.active()
        assert set(active.keys()) == {"ok"}
        assert store.has_active() is True

    def test_delete_active_keeps_internals(self) -> None:
        sess: dict = {
            "_permanent": True,
            "csrf_token": "x",
            "a": {"id": "a"},
            "b": {"id": "b"},
        }
        store = ConversionStore(sess)
        store.delete_active()
        assert set(sess) == {"_permanent", "csrf_token"}


class TestFirstStr:
    def test_returns_first_non_empty_trimmed(self) -> None:
        assert first_str({"a": "", "b": "  hi ", "c": "x"}, "a", "b", "c") == "hi"
        assert first_str({"a": "  "}, "a") == ""
        assert first_str({}, "missing") == ""


class TestFormatTimedelta:
    @pytest.mark.parametrize(
        "td, expected",
        [
            (timedelta(0), ""),
            (timedelta(seconds=5), "5 seconds"),
            (timedelta(minutes=1), "1 minute"),
            (timedelta(minutes=2, seconds=3), "2 minutes 3 seconds"),
            (timedelta(hours=1), "1 hour"),
            (timedelta(hours=3, minutes=30), "3 hours 30 minutes"),
            (timedelta(days=1), "1 day"),
            (timedelta(days=2, hours=4, minutes=5, seconds=6), "2 days 4 hours 5 minutes 6 seconds"),
        ],
    )
    def test_format(self, td: timedelta, expected: str) -> None:
        assert format_timedelta(td) == expected
