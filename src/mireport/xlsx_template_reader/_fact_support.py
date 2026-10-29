"""Shared fact-assembly helpers used by simple-fact and table-fact creation."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from decimal import Decimal
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from mireport.report import InlineReport
    from mireport.report.factbuilder import FactBuilder
    from mireport.taxonomy import Concept, Taxonomy
    from mireport.xlsx_template_reader._config import ConverterConfig
    from mireport.xlsx_template_reader._constants import CellType
    from mireport.xlsx_template_reader._messages import Messenger
    from mireport.xlsx_template_reader._ranges import XbrlConceptCellRangeMetadata
    from mireport.xlsx_template_reader._reader import WorkbookReader

from mireport.conversionresults import MessageType
from mireport.exceptions import AmbiguousComponentException, InlineReportException
from mireport.localise import Rounding
from mireport.xlsx_template_reader._enumerations import resolveMemberByLabel
from mireport.xlsx_template_reader.util import get_decimal_places, get_display_scale

L = logging.getLogger(__name__)


def resolveMemberWithMessages(
    msg: Messenger,
    taxonomy: Taxonomy,
    config: ConverterConfig,
    text: str,
    eeConcept: Concept,
    holder: XbrlConceptCellRangeMetadata,
    cell: CellType | None,
    *,
    displayValue: str | None = None,
    warnOnExactMatch: bool = False,
) -> Concept | None:
    """Resolve cell text to an EE domain member via the full label chain
    (exact -> configured alias -> closest match), emitting the standard
    workaround warnings.

    Returns None silently when nothing matches: the not-found messages differ
    per call site, so callers emit their own. displayValue is what messages
    quote when the resolved text was preprocessed (e.g. a stripped prefix);
    warnOnExactMatch forces the workaround warning for such preprocessed hits.
    """
    shown = displayValue if displayValue is not None else text
    try:
        match = resolveMemberByLabel(taxonomy, config, text, ee_concept=eeConcept)
    except AmbiguousComponentException as exc:
        msg.warning(
            f"Ambiguous cell value '{shown}' when reporting {eeConcept.qname}; "
            f"matching domain members: "
            f"{', '.join(str(c.qname) for c in exc.candidates)}.",
            MessageType.Conversion,
            concept=eeConcept,
            ref=holder.excelRef(cell),
        )
        return None
    if match is None:
        return None
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


def applyPresetDimensions(
    factBuilder: FactBuilder,
    presets: Mapping[Concept, Concept],
    report: InlineReport,
    reader: WorkbookReader,
) -> None:
    """Set the dimensions a Concept_Member name presets (unless the member is the
    default) and, where a range is named for the member, the member's named period."""
    for dim, member in presets.items():
        default = report.taxonomy.getDimensionDefault(dim)
        if default is None or member != default:
            factBuilder.setExplicitDimension(dim, member)

        namedPeriod = member.qname.localName
        if reader.getDefinedName(namedPeriod) is not None and report.hasNamedPeriod(
            namedPeriod
        ):
            factBuilder.setNamedPeriod(namedPeriod)


def addFactToReport(
    report: InlineReport,
    msg: Messenger,
    factBuilder: FactBuilder,
    holder: XbrlConceptCellRangeMetadata,
) -> bool:
    try:
        factBuilder.setProvenance(holder)
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
    value: object | None = None,
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

    # The cell's value is reported whole, to the 15 significant digits Excel ever shows (no float
    # noise: 0.1 + 0.2 is 0.3). The format's places are its decimals, and the report shows it rounded
    # to them as Excel does, half away from zero. A percentage is shown x100, so its fraction has two
    # more; a cell shown in thousands or millions is shown so too, with the scale that undoes it.
    exact = Decimal(f"{value:.15g}") if isinstance(value, float) else Decimal(value)
    fb.setRounding(Rounding.HALF_UP)
    if cell_is_percentage:
        fb.setPercentageFact(exact, decimals if decimals == "INF" else decimals + 2)
        return
    scale = get_display_scale(cell)
    shown = exact.scaleb(-scale)
    fb.setValue(int(shown) if shown == shown.to_integral() else float(shown))
    fb.setDecimals(decimals if decimals == "INF" else decimals - scale)
    if scale:
        fb.setScale(scale)
