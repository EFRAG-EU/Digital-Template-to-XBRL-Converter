"""Concise message emission for the xlsx template reader.

Messenger wraps a ConversionResultsBuilder so call sites name the severity as
the method and can pass a CellRangeMetadata directly as the Excel reference:

    self._msg.error(
        f"Named range {name} is broken.",
        MessageType.ExcelParsing,
        ref=range_meta,          # or a pre-built reference string
        concept=concept,
    )
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Optional, Union

if TYPE_CHECKING:
    from mireport.taxonomy import Concept, QName

from mireport.conversionresults import ConversionResultsBuilder, MessageType, Severity
from mireport.xlsx_template_reader._ranges import CellRangeMetadata

ExcelRef = Union[str, CellRangeMetadata, None]


class Messenger:
    """Severity-named message methods over a ConversionResultsBuilder."""

    __slots__ = ("_results",)

    def __init__(self, results: ConversionResultsBuilder) -> None:
        self._results = results

    def info(
        self,
        message: str,
        message_type: MessageType,
        *,
        ref: ExcelRef = None,
        concept: Optional[Concept | QName] = None,
    ) -> None:
        self._add(message, Severity.INFO, message_type, ref, concept)

    def warning(
        self,
        message: str,
        message_type: MessageType,
        *,
        ref: ExcelRef = None,
        concept: Optional[Concept | QName] = None,
    ) -> None:
        self._add(message, Severity.WARNING, message_type, ref, concept)

    def error(
        self,
        message: str,
        message_type: MessageType,
        *,
        ref: ExcelRef = None,
        concept: Optional[Concept | QName] = None,
    ) -> None:
        self._add(message, Severity.ERROR, message_type, ref, concept)

    def _add(
        self,
        message: str,
        severity: Severity,
        message_type: MessageType,
        ref: ExcelRef,
        concept: Optional[Concept | QName],
    ) -> None:
        excel_reference: Optional[str]
        if ref is None or isinstance(ref, str):
            excel_reference = ref
        else:
            excel_reference = ref.excelRef()
        self._results.addMessage(
            message,
            severity,
            message_type,
            taxonomy_concept=concept,
            excel_reference=excel_reference,
        )
