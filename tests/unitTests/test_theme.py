import pytest

from mireport.report.theme import (
    ColourPalette,
    CSSHexColour,
    DisplayMode,
    InvalidReportThemeException,
)


class TestColourPaletteParse:
    def test_valid_palette_label(self):
        assert ColourPalette.parse("azure") == ColourPalette.AZURE

    def test_label_lookup_is_case_insensitive(self):
        assert ColourPalette.parse("AZURE") == ColourPalette.AZURE
        assert ColourPalette.parse("Azure") == ColourPalette.AZURE

    def test_valid_hex(self):
        assert ColourPalette.parse("#1a2b3c") == CSSHexColour("#1a2b3c")

    def test_invalid_falls_back_to_default(self):
        assert ColourPalette.parse("notacolour") == ColourPalette.default()

    def test_empty_falls_back_to_default(self):
        assert ColourPalette.parse("") == ColourPalette.default()

    def test_invalid_falls_back_to_supplied_default(self):
        assert (
            ColourPalette.parse("bad", default=ColourPalette.TEAL) == ColourPalette.TEAL
        )


class TestColourPaletteFromLabel:
    def test_valid_label(self):
        assert ColourPalette.from_label("teal") == ColourPalette.TEAL

    def test_invalid_label_raises(self):
        with pytest.raises(InvalidReportThemeException):
            ColourPalette.from_label("notacolour")


class TestCSSHexColour:
    def test_is_valid(self):
        assert CSSHexColour.is_valid("#1a2b3c")
        assert CSSHexColour.is_valid("#FFFFFF")
        assert CSSHexColour.is_valid("#000000")

    def test_invalid(self):
        assert not CSSHexColour.is_valid("#zzz")
        assert not CSSHexColour.is_valid("1a2b3c")
        assert not CSSHexColour.is_valid("#1a2b3")
        assert not CSSHexColour.is_valid("#1a2b3cff")
        assert not CSSHexColour.is_valid("")

    def test_invalid_raises(self):
        with pytest.raises(InvalidReportThemeException):
            CSSHexColour("#zzz")

    def test_normalises_to_lowercase(self):
        assert CSSHexColour("#AABBCC") == "#aabbcc"

    def test_palette_members_are_css_hex_colour(self):
        assert isinstance(ColourPalette.AZURE, CSSHexColour)
        assert CSSHexColour.is_valid(ColourPalette.AZURE)


class TestDisplayModeParse:
    def test_valid_value(self):
        assert DisplayMode.parse("dark") == DisplayMode.DARK

    def test_invalid_falls_back_to_default(self):
        assert DisplayMode.parse("blinding") == DisplayMode.default()

    def test_empty_falls_back_to_default(self):
        assert DisplayMode.parse("") == DisplayMode.default()


class TestColourPaletteStrReprFormat:
    def test_str_returns_hex(self):
        assert str(ColourPalette.AZURE) == CSSHexColour(ColourPalette.AZURE.value)

    def test_repr_shows_name_and_value(self):
        assert repr(ColourPalette.AZURE) == f"<ColourPalette.AZURE: {ColourPalette.AZURE.value!r}>"

    def test_format_returns_hex(self):
        assert f"{ColourPalette.AZURE}" == ColourPalette.AZURE.value
