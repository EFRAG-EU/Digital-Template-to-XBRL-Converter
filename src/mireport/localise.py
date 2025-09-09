import argparse
import logging
from decimal import Decimal
from typing import Iterable, Optional

from babel import Locale, UnknownLocaleError
from babel.numbers import format_decimal, get_decimal_symbol

from mireport.typealiases import DecimalPlaces

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
    number: float | int | str | Decimal,
    decimal_places: DecimalPlaces,
    locale: Optional[Locale] = None,
) -> str:
    """
    Format a number with optional locale, supporting 'INF' for unlimited precision.

    In the 'INF' case, the output preserves the exact decimal representation
    (no rounding or truncation, including trailing zeros) and applies locale-aware
    grouping and decimal point symbols if a locale is provided. For finite decimal
    places, applies locale-aware formatting with specified precision.

    Args:
        number: The number to format (float, int, str, or Decimal).
        decimal_places: Number of decimal places or 'INF' for unlimited precision.
        locale: Optional locale (string or babel.core.Locale) for formatting.

    Returns:
        Formatted string representation of the number.

    Raises:
        TypeError: If the number type is unsupported.
        ValueError: If the string input cannot be converted to Decimal.
    """
    # Normalize number to Decimal for safe formatting (avoids float artifacts)
    try:
        match number:
            case Decimal():
                value = number
            case int() | float():
                value = Decimal(str(number))  # Preserves .0 for floats like -1200.0
            case str():
                value = Decimal(number.replace(",", "").replace(" ", ""))
            case _:
                raise TypeError(
                    f"Unsupported type {type(number).__name__} for numeric formatting"
                )
    except ValueError as e:
        raise ValueError(f"Invalid numeric string: {number}") from e

    if decimal_places == "INF":
        if locale:
            return format_decimal(value, locale=locale, decimal_quantization=False)
        # No locale: use standard thousands separator, preserve full precision
        return f"{value:,}"

    # Handle negative decimal places (fallback to 0)
    if decimal_places < 0:
        decimal_places = 0

    # Normal finite case
    if locale:
        pattern = "#,##0." + "0" * decimal_places if decimal_places > 0 else "#,##0"
        return format_decimal(
            value, format=pattern, locale=locale, decimal_quantization=True
        )
    else:
        # Use standard Python formatting without locale
        return f"{value:,.{decimal_places}f}"


def decimal_symbol(locale: Optional[Locale] = None) -> str:
    """Return the decimal symbol for the given locale."""
    if locale:
        return get_decimal_symbol(locale)
    else:
        return "."


def getBestSupportedLanguage(
    requestedLanguage: str,
    supportedLanguages: frozenset[str],
    defaultLanguage: str | None,
) -> str | None:
    """
    Return the best supported language included with the taxonomy for the given requested language.

    @requestedLanguage: Should be as specified in BCP 47. For example, "fr-CH", "en-us", "de".
    @supportedLanguages: set of supported languages to choose from
    @defaultLanguage: Language to pick from supported languages if no close match to requested language can be found, or None if there is no default.
    """
    if defaultLanguage is not None and defaultLanguage not in supportedLanguages:
        raise ValueError(
            f"Default language must either be None or one of the supported languages {supportedLanguages=}"
        )

    if not requestedLanguage:
        return defaultLanguage

    requestedLanguage = requestedLanguage.strip().lower().replace("_", "-")

    # Perfect:
    # e.g. we want en-gb and the taxonomy supports [en-gb, fr], so we return en-gb
    # e.g. we want fr and the taxonomy supports fr, so we return fr
    if requestedLanguage in supportedLanguages:
        return requestedLanguage

    # Good:
    # e.g. we want en-gb and the taxonomy supports [en, fr], so we return en
    base_requested_lang = requestedLanguage.partition("-")[0]
    if base_requested_lang in supportedLanguages:
        return base_requested_lang

    # Fuzzy:
    # e.g. we want en-gb and the taxonomy supports [en-us, fr], so we return
    # en-us
    #
    # e.g. we want en and the taxonomy supports [en-us, fr], so we return
    # en-us
    for supported_lang in sorted(supportedLanguages, reverse=True):
        base_supported_lang, sep, _ = supported_lang.partition("-")
        if not sep:
            # No hyphen, so no base language
            continue
        if (
            base_supported_lang == base_requested_lang
            or base_supported_lang == requestedLanguage
        ):
            return supported_lang

    # No perfect, good or fuzzy match, default it is.
    return defaultLanguage
