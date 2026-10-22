"""A converted Word document and reporting what mammoth said about it."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from markupsafe import Markup

from mireport.conversionresults import (
    ConversionResultsBuilder,
    MessageType,
    Severity,
)

if TYPE_CHECKING:
    from mireport.taxonomy import Concept


@dataclass(frozen=True)
class DocxConversion:
    """A Word document as HTML, with mammoth's messages about it."""

    filename: str
    html: Markup
    messages: list[str]

    def addTo(
        self,
        builder: ConversionResultsBuilder,
        *,
        taxonomy_concept: Concept | None = None,
    ) -> None:
        """Report what mammoth could not convert as warnings."""
        for message in self.messages:
            builder.addMessage(
                f"Word document {self.filename}: {message}",
                Severity.WARNING,
                MessageType.Conversion,
                taxonomy_concept=taxonomy_concept,
            )
