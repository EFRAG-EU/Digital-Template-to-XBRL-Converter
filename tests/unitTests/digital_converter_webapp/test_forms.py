"""Upload form limits and parsing helpers."""

from digital_converter_webapp.forms import (
    IMAGE_UPLOAD_FIELDS,
    MAX_FILE_SIZE,
    MAX_PDF_COUNT,
    first_str,
)


class TestFirstStr:
    def test_first_non_empty_wins(self) -> None:
        form = {"a": "", "b": "second", "c": "third"}
        assert first_str(form, "a", "b", "c") == "second"

    def test_values_are_trimmed(self) -> None:
        assert first_str({"a": "  padded  "}, "a") == "padded"

    def test_whitespace_only_is_skipped(self) -> None:
        assert first_str({"a": "   ", "b": "x"}, "a", "b") == "x"

    def test_nothing_found_is_empty_string(self) -> None:
        assert first_str({}, "a", "b") == ""


def test_limits_are_sane() -> None:
    assert MAX_FILE_SIZE == 16 * 2**20
    assert MAX_PDF_COUNT > 0


def test_image_fields_map_to_distinct_session_keys() -> None:
    fields = [f for f, _ in IMAGE_UPLOAD_FIELDS]
    keys = [k for _, k in IMAGE_UPLOAD_FIELDS]
    assert fields == ["logo", "cover", "background"]
    assert len(set(keys)) == len(keys)
