import pytest

from mireport.stringutil import (
    format_bytes,
    format_time_ns,
    normalizeLabelText,
    stripLabelSuffix,
    unicodeDashNormalization,
    unicodeSpaceNormalize,
    xml_clean,
)

# ── unicodeDashNormalization ──────────────────────────────────────────────


class TestUnicodeDashNormalization:
    @pytest.mark.parametrize(
        "input_label, expected",
        [
            ("hello\N{EM DASH}world", "hello-world"),
            ("hello\N{EN DASH}world", "hello-world"),
            ("hello-world", "hello-world"),
            ("  \N{EM DASH}leading", "-leading"),
            ("trailing\N{EN DASH}  ", "trailing-"),
            ("\N{EM DASH}\N{EN DASH}", "--"),
        ],
        ids=[
            "em-dash",
            "en-dash",
            "plain-hyphen-unchanged",
            "leading-whitespace-stripped",
            "trailing-whitespace-stripped",
            "consecutive-dashes",
        ],
    )
    def test_dash_replacement(self, input_label: str, expected: str) -> None:
        assert unicodeDashNormalization(input_label) == expected

    def test_empty_string(self) -> None:
        assert unicodeDashNormalization("") == ""

    def test_no_dashes_no_whitespace(self) -> None:
        assert unicodeDashNormalization("Revenue") == "Revenue"


# ── unicodeSpaceNormalize ─────────────────────────────────────────────────


class TestUnicodeSpaceNormalize:
    @pytest.mark.parametrize(
        "input_text, expected",
        [
            ("hello\N{NO-BREAK SPACE}world", "hello world"),
            ("hello\N{EN SPACE}world", "hello world"),
            ("hello\N{THIN SPACE}world", "hello world"),
            ("hello\N{IDEOGRAPHIC SPACE}world", "hello world"),
            ("hello\N{NARROW NO-BREAK SPACE}world", "hello world"),
            ("hello world", "hello world"),
        ],
        ids=[
            "no-break-space",
            "en-space",
            "thin-space",
            "ideographic-space",
            "narrow-no-break-space",
            "regular-space-unchanged",
        ],
    )
    def test_space_normalization(self, input_text: str, expected: str) -> None:
        assert unicodeSpaceNormalize(input_text) == expected

    def test_empty_string(self) -> None:
        assert unicodeSpaceNormalize("") == ""

    def test_multiple_special_spaces(self) -> None:
        text = f"a\N{NO-BREAK SPACE}b\N{EM SPACE}c\N{THIN SPACE}d"
        assert unicodeSpaceNormalize(text) == "a b c d"


# ── normalizeLabelText ────────────────────────────────────────────────────


class TestNormalizeLabelText:
    @pytest.mark.parametrize(
        "input_text, expected",
        [
            ("  Revenue  ", "Revenue"),
            ("Net   profit", "Net profit"),
            ("hello\N{EM DASH}world", "hello-world"),
            ("  hello\N{EN DASH}world  ", "hello-world"),
            ("\t tab \n newline \r return", "tab newline return"),
            ("hello\N{NO-BREAK SPACE}world", "hello world"),
        ],
        ids=[
            "leading-trailing-whitespace",
            "internal-whitespace-collapse",
            "em-dash-normalised",
            "combined-whitespace-and-dash",
            "control-whitespace-collapsed",
            "no-break-space-kept-as-word-separator",
        ],
    )
    def test_normalization(self, input_text: str, expected: str) -> None:
        assert normalizeLabelText(input_text) == expected

    def test_empty_string(self) -> None:
        assert normalizeLabelText("") == ""

    def test_already_normal(self) -> None:
        assert normalizeLabelText("Revenue") == "Revenue"

    def test_only_whitespace(self) -> None:
        assert normalizeLabelText("   \t\n  ") == ""


# ── stripLabelSuffix ─────────────────────────────────────────────────────


class TestStripLabelSuffix:
    @pytest.mark.parametrize(
        "input_text, expected",
        [
            ("Revenue [total]", "Revenue"),
            ("Cost of sales [abstract]", "Cost of sales"),
            ("Revenue", "Revenue"),
            ("Revenue [total] extra [sub]", "Revenue [total] extra"),
            ("[only suffix]", "[only suffix]"),
            ("", ""),
            ("Label [tag]  ", "Label"),
        ],
        ids=[
            "simple-suffix",
            "suffix-with-abstract",
            "no-bracket-unchanged",
            "strips-last-bracket-only",
            "entire-text-is-bracket-unchanged",
            "empty-string",
            "trailing-space-strips-last-bracket",
        ],
    )
    def test_suffix_stripping(self, input_text: str, expected: str) -> None:
        assert stripLabelSuffix(input_text) == expected

    def test_bracket_at_start_with_content_after(self) -> None:
        # "[tag] Revenue" → rpartition finds "[" before "tag] Revenue"
        # before = "", sep = "[", after = "tag] Revenue"
        # stripped = "" → falsy → returns original
        assert stripLabelSuffix("[tag] Revenue") == "[tag] Revenue"

    def test_nested_brackets(self) -> None:
        assert stripLabelSuffix("A [B] C [D]") == "A [B] C"


# ── format_time_ns ───────────────────────────────────────────────────────


class TestFormatTimeNs:
    @pytest.mark.parametrize(
        "ns, expected",
        [
            (0, "0 ns"),
            (500, "500 ns"),
            (999, "999 ns"),
            (1_000, "1 µs"),
            (1_500, "1 µs"),
            (999_999, "999 µs"),
            (1_000_000, "1 ms"),
            (999_999_999, "999 ms"),
            (1_000_000_000, "1.0 s"),
            (59_000_000_000, "59.0 s"),
            (60_000_000_000, "1.0 minutes"),
            (3_540_000_000_000, "59.0 minutes"),
            (3_600_000_000_000, "1.0 hours"),
            (85_680_000_000_000, "23.8 hours"),
            (86_400_000_000_000, "1.0 days"),
            (172_800_000_000_000, "2.0 days"),
        ],
        ids=[
            "zero",
            "nanoseconds",
            "max-ns",
            "microseconds",
            "microseconds-truncated",
            "max-µs",
            "milliseconds",
            "max-ms",
            "seconds",
            "just-under-1-min",
            "minutes",
            "just-under-1-hour",
            "hours",
            "just-under-1-day",
            "days",
            "two-days",
        ],
    )
    def test_formatting(self, ns: int, expected: str) -> None:
        assert format_time_ns(ns) == expected


# ── format_bytes ──────────────────────────────────────────────────────────


class TestFormatBytes:
    @pytest.mark.parametrize(
        "num_bytes, expected",
        [
            (0, "0 B"),
            (512, "512 B"),
            (1023, "1023 B"),
            (1024, "1 KiB"),
            (2048, "2 KiB"),
            (1_048_575, "1023 KiB"),
            (1_048_576, "1 MiB"),
            (1_073_741_823, "1023 MiB"),
            (1_073_741_824, "1.0 GiB"),
            (2_147_483_648, "2.0 GiB"),
        ],
        ids=[
            "zero",
            "bytes",
            "max-bytes",
            "one-kib",
            "two-kib",
            "max-kib",
            "one-mib",
            "max-mib",
            "one-gib",
            "two-gib",
        ],
    )
    def test_formatting(self, num_bytes: int, expected: str) -> None:
        assert format_bytes(num_bytes) == expected


# ── xml_clean ─────────────────────────────────────────────────────────────


class TestXmlClean:
    @pytest.mark.parametrize(
        "input_text, expected",
        [
            ("&", "&amp;"),
            ("<", "&lt;"),
            (">", "&gt;"),
            ("'", "&apos;"),
            ('"', "&quot;"),
            ("a\tb\nc\rd\ve\f", "abcde"),
            ("<b>hello & 'world'</b>", "&lt;b&gt;hello &amp; &apos;world&apos;&lt;/b&gt;"),
        ],
        ids=[
            "ampersand",
            "less-than",
            "greater-than",
            "apostrophe",
            "quote",
            "control-chars-removed",
            "mixed-xml-content",
        ],
    )
    def test_escaping(self, input_text: str, expected: str) -> None:
        assert xml_clean(input_text) == expected

    def test_empty_string(self) -> None:
        assert xml_clean("") == ""

    def test_plain_text_unchanged(self) -> None:
        assert xml_clean("hello world") == "hello world"
