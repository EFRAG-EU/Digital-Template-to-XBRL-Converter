"""Conversion / ConversionStore: the typed view over the session's per-id dicts."""

import pytest

from digital_converter_webapp.conversion import (
    MIGRATION_REQUIRED,
    Conversion,
    ConversionStore,
)
from digital_converter_webapp.migration import MigrationOutcome
from mireport.filesupport import FilelikeAndFileName


def _make(id: str = "abc", onChange=None) -> Conversion:
    return Conversion({"id": id}, onChange)


def _file(content: bytes = b"xxxx", name: str = "f.bin") -> FilelikeAndFileName:
    return FilelikeAndFileName(fileContent=content, filename=name)


def test_migration_required_matches_the_enum() -> None:
    # conversion.py repeats the value to avoid a circular import; keep them tied.
    assert MIGRATION_REQUIRED == MigrationOutcome.MIGRATION_REQUIRED


class TestConversion:
    def test_round_trips_excel_through_primitives(self) -> None:
        conv = _make()
        conv.excel = _file(b"xxxx", "r.xlsx")

        assert isinstance(conv.raw["excel"], tuple)
        assert conv.raw["excel"] == (b"xxxx", "r.xlsx")

        # The session backend hands NamedTuples back as lists.
        reloaded = Conversion({"id": "abc", "excel": list(conv.raw["excel"])})
        assert reloaded.excel is not None
        assert reloaded.excel.filename == "r.xlsx"
        assert reloaded.excel.fileContent == b"xxxx"

    def test_missing_file_is_none(self) -> None:
        conv = _make()
        assert conv.excel is None
        assert conv.zip is None
        assert conv.viewer is None
        assert conv.json is None
        assert conv.migrated_excel is None

    def test_setting_a_file_to_none_removes_it(self) -> None:
        conv = _make()
        conv.migrated_excel = _file()
        conv.migrated_excel = None
        assert "migrated_excel" not in conv

    def test_file_setter_rejects_wrong_type(self) -> None:
        conv = _make()
        with pytest.raises(TypeError):
            conv.excel = "not a file"  # type: ignore[assignment]

    def test_external_values_round_trip(self) -> None:
        conv = _make()
        conv.external_values = {"ns:foo": _file(b"doc", "a.docx")}
        reloaded = Conversion(
            {
                "id": "x",
                "external_values": {
                    k: list(v) for k, v in conv.raw["external_values"].items()
                },
            }
        )
        assert reloaded.has_external_values
        assert reloaded.external_values["ns:foo"].filename == "a.docx"

    def test_pdfs_come_back_as_files_in_order(self) -> None:
        conv = _make()
        assert conv.pdfs == []
        conv.pdfs = [_file(b"1", "a.pdf"), _file(b"2", "b.pdf")]

        reloaded = Conversion({"id": "x", "pdfs": [list(p) for p in conv.raw["pdfs"]]})
        assert [(p.filename, p.fileContent) for p in reloaded.pdfs] == [
            ("a.pdf", b"1"),
            ("b.pdf", b"2"),
        ]

    def test_no_pdfs_leaves_no_key(self) -> None:
        conv = _make()
        conv.pdfs = [_file(name="a.pdf")]
        conv.pdfs = []
        assert "pdfs" not in conv

    def test_viewer_members_round_trip(self) -> None:
        conv = _make()
        conv.viewer_members = {"annex1.xhtml": _file(b"<html/>", "annex1.xhtml")}
        reloaded = Conversion(
            {
                "id": "x",
                "viewer_members": {
                    k: list(v) for k, v in conv.raw["viewer_members"].items()
                },
            }
        )
        assert list(reloaded.viewer_members) == ["annex1.xhtml"]
        assert reloaded.viewer_members["annex1.xhtml"].fileContent == b"<html/>"

    def test_cached_output_only_for_known_output_keys(self) -> None:
        conv = _make()
        conv.zip = _file(b"z", "r.zip")
        conv.raw["style_palette"] = ["not", "a file"]
        assert conv.cachedOutput("zip") is not None
        assert conv.cachedOutput("style_palette") is None
        assert conv.cachedOutput("json") is None

    def test_images_by_session_key(self) -> None:
        conv = _make()
        assert not conv.hasImage("image_logo")
        conv.setImage("image_logo", _file(b"png", "logo.png"))
        assert conv.hasImage("image_logo")
        image = conv.image("image_logo")
        assert image is not None and image.filename == "logo.png"

    def test_migration_outcome_setter_accepts_strenum_and_str(self) -> None:
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
        assert conv.has_partial_fact_concepts

    def test_locale_str_cleared_by_falsy_value(self) -> None:
        conv = _make()
        conv.locale_str = "en-GB"
        assert conv.locale_str == "en-GB"
        conv.locale_str = ""
        assert conv.locale_str is None
        assert "locale_str" not in conv

    def test_migration_results_flag(self) -> None:
        conv = _make()
        assert not conv.has_migration_results
        conv.migration_error = "boom"
        assert conv.has_migration_results
        conv.migration_error = None
        assert not conv.has_migration_results
        conv.migrated_excel = _file()
        assert conv.has_migration_results

    def test_defaults_when_unset(self) -> None:
        conv = _make("id-1")
        assert conv.id == "id-1"
        assert conv.date == ""
        assert conv.style_palette == ""
        assert conv.style_mode == ""
        assert conv.report_template_version == ""
        assert conv.migration_outcome == ""
        assert conv.successful is False
        assert not conv.has_zip
        assert not conv.has_results
        assert conv.results is None
        assert conv.migration_issues == []


class TestOnChange:
    """Nested writes aren't noticed by the session, so setters must say so."""

    def test_setters_notify(self) -> None:
        calls: list[int] = []
        conv = _make(onChange=lambda: calls.append(1))
        conv.date = "2026-01-01"
        conv.excel = _file()
        conv.pdfs = [_file(name="a.pdf")]
        conv.viewer_members = {}
        conv.partial_fact_concepts = []
        conv.successful = True
        conv.migration_outcome = "success"
        assert len(calls) == 7

    def test_reads_do_not_notify(self) -> None:
        calls: list[int] = []
        conv = _make(onChange=lambda: calls.append(1))
        _ = (conv.excel, conv.pdfs, conv.viewer_members, conv.date, conv.results)
        assert calls == []

    def test_store_passes_the_callback_to_conversions(self) -> None:
        calls: list[int] = []
        store = ConversionStore({}, lambda: calls.append(1))
        store.create("a").date = "d"
        assert calls == [1]


class TestConversionStore:
    def test_create_then_get(self) -> None:
        store = ConversionStore({})
        created = store.create("a")
        assert created.id == "a"
        fetched = store.get("a")
        assert fetched is not None and fetched.id == "a"
        assert "a" in store

    def test_create_does_not_replace_an_existing_conversion(self) -> None:
        store = ConversionStore({})
        store.create("a").date = "first"
        assert store.create("a").date == "first"

    def test_get_unknown_is_none(self) -> None:
        assert ConversionStore({}).get("nope") is None
        assert "nope" not in ConversionStore({})

    def test_session_internals_are_not_conversions(self) -> None:
        session = {
            "_permanent": True,
            "csrf_token": "tok",
            "captcha_answers": {"x": 3},
            "real": {"id": "real"},
        }
        store = ConversionStore(session)
        assert list(store) == ["real"]
        assert store.get("captcha_answers") is None
        assert "captcha_answers" not in store
        store.delete("captcha_answers")
        assert "captcha_answers" in session

    def test_aborted_migration_is_hidden_from_the_listing_but_still_found(
        self,
    ) -> None:
        store = ConversionStore({})
        store.create("ok")
        store.create("old").migration_outcome = MigrationOutcome.MIGRATION_REQUIRED
        assert list(store.active()) == ["ok"]
        assert store.hasActive()
        assert store.get("old") is not None

    def test_delete_and_delete_active(self) -> None:
        store = ConversionStore({})
        store.create("a")
        store.create("b")
        store.create("old").migration_outcome = MigrationOutcome.MIGRATION_REQUIRED
        store.delete("a")
        assert "a" not in store
        store.deleteActive()
        assert list(store.active()) == []
        assert not store.hasActive()
        assert "old" in store  # the migration page still needs it

    def test_delete_unknown_is_harmless(self) -> None:
        ConversionStore({}).delete("nope")
