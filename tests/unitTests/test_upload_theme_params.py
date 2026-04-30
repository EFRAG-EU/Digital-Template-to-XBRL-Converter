import pytest

from mireport.report.theme import ColourPalette, DisplayMode, ReportTheme

from efrag_digital_converter import _parse_theme_form_params


def test_valid_palette_accepted():
    palette, _ = _parse_theme_form_params({"style_palette": "purple"})
    assert palette == "purple"


def test_invalid_palette_falls_back_to_default():
    palette, _ = _parse_theme_form_params({"style_palette": "notacolour"})
    assert palette == ReportTheme.DEFAULT_COLOUR.label


def test_missing_palette_defaults():
    palette, _ = _parse_theme_form_params({})
    assert palette == ReportTheme.DEFAULT_COLOUR.label


@pytest.mark.parametrize("label", ColourPalette.labels())
def test_all_valid_palette_labels_accepted(label):
    palette, _ = _parse_theme_form_params({"style_palette": label})
    assert palette == label


def test_valid_mode_dark_accepted():
    _, mode = _parse_theme_form_params({"style_mode": "dark"})
    assert mode == "dark"


def test_valid_mode_light_accepted():
    _, mode = _parse_theme_form_params({"style_mode": "light"})
    assert mode == "light"


def test_invalid_mode_falls_back_to_default():
    _, mode = _parse_theme_form_params({"style_mode": "blinding"})
    assert mode == ReportTheme.DEFAULT_DISPLAY_MODE.value


def test_missing_mode_defaults():
    _, mode = _parse_theme_form_params({})
    assert mode == ReportTheme.DEFAULT_DISPLAY_MODE.value
