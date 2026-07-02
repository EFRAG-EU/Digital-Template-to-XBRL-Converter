"""Shared fact-assembly helpers used by simple-fact and table-fact creation."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from mireport.report import InlineReport
    from mireport.report.factbuilder import FactBuilder
    from mireport.taxonomy import Concept, Taxonomy
    from mireport.xlsx_template_reader._config import ConverterConfig
    from mireport.xlsx_template_reader._constants import CellType
    from mireport.xlsx_template_reader._messages import Messenger
    from mireport.xlsx_template_reader._ranges import XbrlConceptCellRangeMetadata

from mireport.conversionresults import MessageType
from mireport.exceptions import InlineReportException
from mireport.xlsx_template_reader._enumerations import resolveMemberByLabel
from mireport.xlsx_template_reader.util import get_decimal_places

L = logging.getLogger(__name__)


def resolveMemberWithMessages(
    msg: Messenger,
    taxonomy: Taxonomy,
    config: ConverterConfig,
    text: str,
    eeConcept: Concept,
    holder: XbrlConceptCellRangeMetadata,
    cell: Optional[CellType],
    *,
    displayValue: Optional[str] = None,
    warnOnExactMatch: bool = False,
) -> Optional[Concept]:
    """Resolve cell text to an EE domain member via the full label chain
    (exact -> configured alias -> closest match), emitting the standard
    workaround warnings.

    Returns None silently when nothing matches: the not-found messages differ
    per call site, so callers emit their own. displayValue is what messages
    quote when the resolved text was preprocessed (e.g. a stripped prefix);
    warnOnExactMatch forces the workaround warning for such preprocessed hits.
    """
    match = resolveMemberByLabel(taxonomy, config, text, ee_concept=eeConcept)
    if match is None:
        return None
    shown = displayValue if displayValue is not None else text
    if match.closestLabel is not None:
        msg.warning(
            f"Using closest match EE concept when reporting {eeConcept.qname}. Cell value '{shown}'. Chosen EE domain member: {match.concept.qname} with label: '{match.closestLabel}'",
            MessageType.Conversion,
            concept=eeConcept,
            ref=holder.excelRef(cell),
        )
    elif match.viaConfiguredAlias or warnOnExactMatch:
        msg.warning(
            f"Workaround performed for EE member label mismatch when reporting {eeConcept.qname}. Cell value '{shown}'. Concept label '{match.concept.getStandardLabel()}'",
            MessageType.DevInfo,
            concept=eeConcept,
            ref=holder.excelRef(cell),
        )
    return match.concept


def addFactToReport(
    report: InlineReport,
    msg: Messenger,
    factBuilder: FactBuilder,
    holder: XbrlConceptCellRangeMetadata,
) -> bool:
    try:
        report.addFact(factBuilder.buildFact())
        return True
    except InlineReportException as i:
        msg.warning(
            f"Unable to add fact. Encountered error: {i}",
            MessageType.Conversion,
            ref=holder,
        )
    return False


def processNumeric(
    msg: Messenger,
    holder: XbrlConceptCellRangeMetadata,
    cell: CellType,
    fb: FactBuilder,
    value: Optional[object] = None,
) -> None:
    """Apply the cell's numeric formatting (decimals, percentage) to the builder."""
    if value is None:
        if cell.value is None:
            msg.error(
                f"Cell value is None for {holder.definedName.name}. Unable to process numeric value.",
                MessageType.DevInfo,
                ref=holder.excelRef(cell),
            )
            return
        else:
            value = cell.value

    if isinstance(value, bool) or not isinstance(value, int | float):
        msg.error(
            f"Cell value {value=} {type(value)} is not numeric for {holder.definedName.name}. Unable to process numeric value.",
            MessageType.DevInfo,
            ref=holder.excelRef(cell),
        )
        return

    decimals = get_decimal_places(cell)

    cell_is_percentage = "%" in cell.number_format
    if fb.concept is not None:
        concept_is_percentage = "percentItemType" == fb.concept.dataType.localName
        if cell_is_percentage != concept_is_percentage:
            msg.warning(
                f"Cell number format and XBRL Taxonomy data type disagree about percentages. Cell number format '{cell.number_format}'. Concept data type {fb.concept.dataType}.",
                MessageType.DevInfo,
                concept=fb.concept,
                ref=holder.excelRef(cell),
            )

    if cell_is_percentage:
        fb.setPercentageValue(value, decimals, inputIsDecimalForm=True)
    else:
        fb.setDecimals(decimals)
