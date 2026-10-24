"""A converted Word document and reporting what mammoth said about it."""

from __future__ import annotations

import re
from collections import Counter
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

# mammoth's wording for a style it has no mapping for, or that the document
# doesn't define. Only the formatting is lost; the report styles text its own way.
_STYLE_MESSAGE = re.compile(
    r"^Unrecognised \w+ style: | style with ID .* was referenced but not defined"
)


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
        """Report the conversion, then mammoth's messages, each distinct one
        once and each about this document (its ``source``). Style messages are
        developer information; the rest, which may mean lost content, are one
        warning for the document."""
        builder.addMessage(
            "Converted."
            if taxonomy_concept is None
            else f"Converted for {taxonomy_concept.qname}.",
            Severity.INFO,
            MessageType.Conversion,
            taxonomy_concept=taxonomy_concept,
            source=self.filename,
        )
        styles = Counter(m for m in self.messages if _STYLE_MESSAGE.search(m))
        others = Counter(m for m in self.messages if not _STYLE_MESSAGE.search(m))
        for message, count in styles.items():
            builder.addMessage(
                _counted(message, count),
                Severity.INFO,
                MessageType.DevInfo,
                taxonomy_concept=taxonomy_concept,
                source=self.filename,
            )
        if others:
            builder.addMessage(
                "Some content may not appear as it does in Word: "
                + "; ".join(_counted(m, count) for m, count in others.items()),
                Severity.WARNING,
                MessageType.Conversion,
                taxonomy_concept=taxonomy_concept,
                source=self.filename,
            )


def _counted(message: str, count: int) -> str:
    return message if count == 1 else f"{message} ({count} times)"
