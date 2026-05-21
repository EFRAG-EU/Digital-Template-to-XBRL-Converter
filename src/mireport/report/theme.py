from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from enum import ReprEnum, StrEnum
from typing import TYPE_CHECKING, ClassVar, Optional

if TYPE_CHECKING:
    from typing import Self

from mireport.exceptions import InlineReportException
from mireport.filesupport import ImageFileLikeAndFileName

L = logging.getLogger(__name__)


class InvalidReportThemeException(InlineReportException):
    pass


class CSSHexColour(str):
    """A validated 6-digit hex colour string. Base type for ColourPalette members."""

    _HEX_RE: ClassVar[re.Pattern[str]] = re.compile(r"#[0-9A-Fa-f]{6}")

    def __new__(cls, value: str) -> CSSHexColour:
        if not cls.is_valid(value):
            raise InvalidReportThemeException(
                f"Colour must be a 6-digit hex code (e.g. #1a2b3c), got '{value}'."
            )
        return super().__new__(cls, value.lower())

    @classmethod
    def is_valid(cls, value: str) -> bool:
        return bool(cls._HEX_RE.fullmatch(value))

    @property
    def label(self) -> str:
        return "custom"


class ColourPalette(CSSHexColour, ReprEnum):
    GREEN = "#4f7f2a"
    AZURE = "#007acc"
    BLUE = "#1565c0"
    TEAL = "#00695c"
    PURPLE = "#6a1b9a"
    NAVY = "#1a237e"
    GREY = "#455a64"

    @property
    def label(self) -> str:
        return self.name.lower()

    @classmethod
    def default(cls) -> ColourPalette:
        return cls.AZURE

    @classmethod
    def labels(cls) -> list[str]:
        return [m.label for m in cls]

    @classmethod
    def from_label(cls, label: str) -> ColourPalette:
        try:
            return cls[label.upper()]
        except KeyError:
            raise InvalidReportThemeException(
                f"Colour must be one of {cls.labels()}, got '{label}'."
            )

    @classmethod
    def parse(cls, value: str, default: CSSHexColour | None = None) -> CSSHexColour:
        """Return a CSSHexColour from a palette label, hex string, or default."""
        try:
            return cls[value.upper()]
        except KeyError:
            pass
        if CSSHexColour.is_valid(value):
            return CSSHexColour(value)
        fallback = default if default is not None else cls.default()
        L.debug(f"Unrecognised colour {value!r} — falling back to {fallback}")
        return fallback


class DisplayMode(StrEnum):
    LIGHT = "light"
    DARK = "dark"

    @classmethod
    def default(cls) -> DisplayMode:
        return cls.LIGHT

    @classmethod
    def parse(cls, value: str) -> DisplayMode:
        try:
            return cls(value)
        except ValueError:
            return cls.default()


@dataclass
class ReportTheme:
    DEFAULT_COLOUR: ClassVar[ColourPalette] = ColourPalette.default()
    DEFAULT_DISPLAY_MODE: ClassVar[DisplayMode] = DisplayMode.default()

    colour: CSSHexColour
    displayMode: DisplayMode
    background_image: Optional[ImageFileLikeAndFileName] = None
    cover_image: Optional[ImageFileLikeAndFileName] = None
    logo_image: Optional[ImageFileLikeAndFileName] = None

    @classmethod
    def default(cls) -> ReportTheme:
        return cls(colour=cls.DEFAULT_COLOUR, displayMode=cls.DEFAULT_DISPLAY_MODE)

    def setColour(self, colour: CSSHexColour) -> Self:
        self.colour = colour
        return self

    def setDisplayMode(self, mode: DisplayMode) -> Self:
        self.displayMode = mode
        return self

    def setLogoImage(self, image: ImageFileLikeAndFileName) -> Self:
        self.logo_image = image
        return self

    def setCoverImage(self, image: ImageFileLikeAndFileName) -> Self:
        self.cover_image = image
        return self

    def setBackgroundImage(self, image: ImageFileLikeAndFileName) -> Self:
        self.background_image = image
        return self
