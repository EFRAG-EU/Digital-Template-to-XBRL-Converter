import argparse
import logging
from typing import Iterable, Optional

from babel import Locale, UnknownLocaleError
from babel.numbers import format_decimal, get_decimal_symbol

L = logging.getLogger(__name__)

EU_LOCALES = {
    # One standard locale per official EU language
    "bg-BG",  # Bulgarian
    "hr-HR",  # Croatian
    "cs-CZ",  # Czech
    "da-DK",  # Danish
    "nl-NL",  # Dutch
    "en-IE",  # English (Ireland)
    "et-EE",  # Estonian
    "fi-FI",  # Finnish
    "fr-FR",  # French
    "de-DE",  # German
    "el-GR",  # Greek
    "hu-HU",  # Hungarian
    "ga-IE",  # Irish
    "it-IT",  # Italian
    "lv-LV",  # Latvian
    "lt-LT",  # Lithuanian
    "mt-MT",  # Maltese
    "pl-PL",  # Polish
    "pt-PT",  # Portuguese
    "ro-RO",  # Romanian
    "sk-SK",  # Slovak
    "sl-SI",  # Slovenian
    "es-ES",  # Spanish
    "sv-SE",  # Swedish
    # Additional variants in multilingual EU countries
    "nl-BE",  # Dutch (Belgium)
    "fr-BE",  # French (Belgium)
    "de-BE",  # German (Belgium)
    "sv-FI",  # Swedish (Finland)
    "fr-LU",  # French (Luxembourg)
    "de-LU",  # German (Luxembourg)
    "en-MT",  # English (Malta)
    "el-CY",  # Greek (Cyprus)
    "de-AT",  # German (Austria)
}


def argparse_locale(s: str) -> Locale:
    try:
        s = s.replace("-", "_")
        return Locale.parse(s)
    except (UnknownLocaleError, ValueError):
        raise argparse.ArgumentTypeError(f"Invalid locale: {s}")


def get_locale_from_str(s: str) -> Optional[Locale]:
    """Parse a locale string into a Locale object, normalizing to underscore format. Fallback to None on error."""
    try:
        s = s.replace("-", "_")
        return Locale.parse(s)
    except (UnknownLocaleError, ValueError):
        return None


def get_locale_list(code_list: Iterable[str]) -> list[dict[str, str]]:
    locales: list[dict[str, str]] = []
    code_list = frozenset(code_list)

    max_code_length = max(len(code) for code in code_list)
    for code in code_list:
        try:
            # Normalize to Babel's preferred format
            normalized_code = code.replace("-", "_")
            loc = Locale.parse(normalized_code)

            language = loc.get_language_name(loc)
            territory = loc.get_territory_name(loc)
            if not language or not territory:
                L.warning(f"Locale {code} has no language or territory name.")
                continue

            display_code = code.ljust(max_code_length)
            label = f"{language} ({territory}) [{display_code}]"

            locales.append({"code": normalized_code, "label": label})
        except Exception:
            L.exception("Error parsing locale")
            continue
    locales.sort(key=lambda x: x["label"].casefold())
    return locales


def localise_and_format_number(
    number: float | int | str,
    decimal_places: int,
    locale: Optional[Locale] = None,
) -> str:
    """Format a number with optional locale, enforcing decimal places."""

    if decimal_places < 0:
        # Scaling to nearest 10, 100, etc. is not supported (fallback to 0)
        decimal_places = 0

    # Normalise number to float for formatting
    match number:
        case float() | int():
            value = float(number)
        case str():
            value = float(number.replace(",", ""))
        case _:
            raise TypeError(
                f"Unsupported type {type(number).__name__} for numeric formatting"
            )

    if locale:
        pattern = "#,##0." + "0" * decimal_places if decimal_places > 0 else "#,##0"
        return format_decimal(
            value, format=pattern, locale=locale, decimal_quantization=True
        )
    else:
        return f"{value:,.{decimal_places}f}"


def decimal_symbol(locale: Optional[Locale] = None) -> str:
    """Return the decimal symbol for the given locale."""
    if locale:
        return get_decimal_symbol(locale)
    else:
        return "."
