"""Shared fact-assembly helpers used by simple-fact and table-fact creation."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from mireport.report import InlineReport
    from mireport.report.factbuilder import FactBuilder
    from mireport.xlsx_template_reader._constants import CellType
    from mireport.xlsx_template_reader._messages import Messenger
    from mireport.xlsx_template_reader._ranges import XbrlConceptCellRangeMetadata

from mireport.conversionresults import MessageType
from mireport.exceptions import InlineReportException
from mireport.xlsx_template_reader.util import get_decimal_places

L = logging.getLogger(__name__)


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
