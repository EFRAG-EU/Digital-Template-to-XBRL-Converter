from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import ClassVar, Optional, Self

from mireport.exceptions import InlineReportException
from mireport.filesupport import ImageFileLikeAndFileName


class ColourPalette(StrEnum):
    GREEN = "#4F7F2A"
    AZURE = "#007acc"
    BLUE = "#1565C0"
    TEAL = "#00695C"
    PURPLE = "#6A1B9A"
    NAVY = "#1A237E"
    GREY = "#455A64"

    @property
    def label(self) -> str:
        return self.name.lower()

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


class DisplayMode(StrEnum):
    LIGHT = "light"
    DARK = "dark"


class InvalidReportThemeException(InlineReportException):
    pass


@dataclass
class ReportTheme:
    DEFAULT_COLOUR: ClassVar[ColourPalette] = ColourPalette.AZURE
    DEFAULT_DISPLAY_MODE: ClassVar[DisplayMode] = DisplayMode.LIGHT

    colour: ColourPalette
    displayMode: DisplayMode
    logo: Optional[ImageFileLikeAndFileName] = None
    cover_image: Optional[ImageFileLikeAndFileName] = None
    watermark: Optional[ImageFileLikeAndFileName] = None

    @classmethod
    def default(cls) -> ReportTheme:
        return cls(colour=cls.DEFAULT_COLOUR, displayMode=cls.DEFAULT_DISPLAY_MODE)

    def setColour(self, colour: ColourPalette) -> Self:
        self.colour = colour
        return self

    def setDisplayMode(self, mode: DisplayMode) -> Self:
        self.displayMode = mode
        return self

    def setLogo(self, image: ImageFileLikeAndFileName) -> Self:
        self.logo = image
        return self

    def setCoverImage(self, image: ImageFileLikeAndFileName) -> Self:
        self.cover_image = image
        return self

    def setWatermark(self, image: ImageFileLikeAndFileName) -> Self:
        self.watermark = image
        return self
