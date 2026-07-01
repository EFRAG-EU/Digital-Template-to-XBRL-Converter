from __future__ import annotations

import logging
from collections import defaultdict
from datetime import date
from typing import (
    TYPE_CHECKING,
    Optional,
)

if TYPE_CHECKING:
    from openpyxl.worksheet.worksheet import Worksheet

    from mireport.taxonomy import Concept, Taxonomy

from openpyxl import Workbook
from openpyxl.workbook.defined_name import DefinedName
from openpyxl.worksheet.cell_range import CellRange

from mireport.conversionresults import ConversionResultsBuilder, MessageType, Severity
from mireport.xlsx_template_reader._bindings import (
    CellRangeMetadata,
    TableBinding,
    WorkbookBindings,
    XbrlConceptCellRangeMetadata,
)
from mireport.xlsx_template_reader._cell_iteration import (
    getEffectiveCellRangeDimensions,
)
from mireport.xlsx_template_reader._constants import (
    EXCEL_PLACEHOLDER_VALUE,
    IGNORED_DEFINED_NAME_PREFIXES,
    CellType,
    CellValueType,
)
from mireport.xlsx_template_reader._resolvers import (
    ExcelCellBindingContext,
    ExternalValuesResolver,
    FootnoteTableResolver,
    XBRLTableResolver,
)
from mireport.xlsx_template_reader.util import (
    conceptsToText,
    excelDefinedNameRef,
    getDateFromValue,
)

L = logging.getLogger(__name__)


class WorkbookReader:
    """Ergonomic cell-level access to an openpyxl Workbook.

    Carries the workbook and results builder, with internal tracking of
    unused named ranges.
    """

    def __init__(
        self,
        workbook: Workbook,
        results: ConversionResultsBuilder,
    ) -> None:
        self._workbook = workbook
        self._unused: set[DefinedName] = {
            dn
            for dn in workbook.defined_names.values()
            if (name := dn.name) and not name.startswith(IGNORED_DEFINED_NAME_PREFIXES)
        }
        self._results = results

    def close(self) -> None:
        self._workbook.close()

    def getDefinedName(self, name: str) -> Optional[DefinedName]:
        return self._workbook.defined_names.get(name)

    @property
    def unused_defined_names(self) -> frozenset[DefinedName]:
        return frozenset(self._unused)

    def build_bindings(self, taxonomy: Taxonomy, defaults: dict) -> WorkbookBindings:
        """Scrape named ranges from the workbook and return a WorkbookBindings."""
        concept_map: dict = {}
        unit_map: dict = {}
        preset_dims: defaultdict = defaultdict(dict)

        results = self._results

        for dn in self.unused_defined_names:
            concept = taxonomy.getConceptForName(dn.name)

            # TODO FIXME Temporary fix for the VSME taxonomy
            if dn.name == "IdentifierOfSitesInBiodiversitySensitiveAreasTypedAxis":
                concept = taxonomy.getConceptForName("IdentifierOfSiteTypedAxis")
            # TODO FIXME Temporary fix for the VSME taxonomy

            if concept is not None:
                if (crh := self._createCellRangeMetadata(dn)) is not None:
                    concept_map[dn] = (
                        XbrlConceptCellRangeMetadata.fromCellRangeMetadata(
                            crh, concept=concept
                        )
                    )
            elif "_" in dn.name:
                conceptName, _, memberName = dn.name.partition("_")
                if "unit" == memberName:
                    if (
                        concept := taxonomy.getConceptForName(conceptName)
                    ) is not None and (
                        crh := self._createCellRangeMetadata(dn)
                    ) is not None:
                        unit_map[concept] = (
                            XbrlConceptCellRangeMetadata.fromCellRangeMetadata(
                                crh, concept
                            )
                        )
                        self._unused.discard(dn)
                else:
                    concept = taxonomy.getConceptForName(conceptName)
                    dimValue = taxonomy.getConceptForName(memberName)
                    crh = self._createCellRangeMetadata(dn)
                    if crh is not None and concept is not None and dimValue is not None:
                        b = XbrlConceptCellRangeMetadata.fromCellRangeMetadata(
                            crh, concept=concept
                        )
                        if (
                            dim := taxonomy.getExplicitDimensionForDomainMember(
                                concept, dimValue
                            )
                        ) is not None:
                            concept_map[dn] = b
                            preset_dims[b][dim] = dimValue
                        else:
                            results.addMessage(
                                f"Domain member qualification set in named range {dn.name} but no dimension can be found for member.",
                                Severity.ERROR,
                                MessageType.DevInfo,
                            )
            if dn in concept_map:
                self._unused.discard(dn)

        results.addMessage(
            f"Excel file parsed ({results.numCellsPopulated} cells had data, with {results.numCellQueries} cells accessed).",
            Severity.INFO,
            MessageType.ExcelParsing,
        )

        hypercube_ranges, concepts_in_excel, candidates_by_ws = index_xbrl_candidates(
            concept_map, taxonomy
        )
        if empty := taxonomy.emptyHypercubes.intersection(
            c for c in concepts_in_excel if c.isHypercube
        ):
            results.addMessage(
                f"The following hypercubes exist and have corresponding named ranges but they cannot be used due to missing taxonomy definitions: {conceptsToText(empty)}.",
                Severity.ERROR,
                MessageType.DevInfo,
            )

        ctx = ExcelCellBindingContext(self, results, taxonomy)

        tables: list[TableBinding] = []
        for table_stuff in hypercube_ranges:
            binding = XBRLTableResolver(
                ctx,
                unit_map,
                table_stuff,
                candidates_by_ws.get(table_stuff.worksheet, ()),
                concepts_in_excel,
            ).resolve()
            if binding is not None:
                tables.append(binding)
        consume_table_bindings(concept_map, unit_map, tables)

        return WorkbookBindings(
            concept_map=concept_map,
            tables=tables,
            unit_map=unit_map,
            preset_dims=preset_dims,
            has_external_value=ExternalValuesResolver(ctx).resolve(),
            footnote=FootnoteTableResolver(ctx).resolve(),
        )

    def _createCellRangeMetadata(self, dn: DefinedName) -> Optional[CellRangeMetadata]:
        try:
            all_destinations = list(dn.destinations)
        except AttributeError:
            self._results.addMessage(
                f"Named range {dn.name} has an unreadable destination: {dn.attr_text!r}. \nSomething has modified the digital template's structure. \nPlease try a fresh copy of the template and check that it has not been modified in unsupported ways.",
                Severity.ERROR,
                MessageType.DevInfo,
            )
            L.exception(
                f"OpenPyXL error processing named range definition {dn.name=} {dn.attr_text=!r}."
            )
            return None
        match len(all_destinations):
            case 0:
                self._results.addMessage(
                    f"Named range {dn.name} has no destinations specified. Ignoring.",
                    Severity.ERROR,
                    MessageType.DevInfo,
                )
                return None
            case 1:
                pass
            case _:
                self._results.addMessage(
                    f"Table {dn.name} has multiple destinations. Ignoring table.",
                    Severity.ERROR,
                    MessageType.DevInfo,
                )
                return None
        sheetName, cell_range = all_destinations[0]
        if not sheetName or not cell_range:
            self._results.addMessage(
                f"Named range {dn.name} has damaged cell reference {sheetName=} {cell_range=}",
                Severity.ERROR,
                MessageType.ExcelParsing,
            )
            return None
        try:
            ws = self._workbook[sheetName]
            cr = CellRange(cell_range)
        except Exception as e:
            L.exception(
                f"OpenPyXL error processing cell range. {dn.name=} {sheetName=} {cell_range=}",
                exc_info=e,
            )
            return None
        dims = getEffectiveCellRangeDimensions(ws, cr)
        self._results.addCellQueries(dims.cellsAccessed)
        self._results.addCellsWithData(dims.cellsPopulated)
        return CellRangeMetadata(
            dn,
            ws,
            cr,
            populated_height=dims.populated_height,
            populated_width=dims.populated_width,
            populated_min_col=dims.populated_min_col,
            populated_min_row=dims.populated_min_row,
        )

    def _getCellRangeMetadata(
        self,
        definedName: DefinedName
        | str
        | XbrlConceptCellRangeMetadata
        | CellRangeMetadata,
    ) -> Optional[CellRangeMetadata]:
        if isinstance(definedName, str):
            definedName = self._workbook.defined_names.get(definedName)
            if definedName is None:
                return None
        if isinstance(definedName, DefinedName):
            if (crm := self._createCellRangeMetadata(definedName)) is None:
                return None
            definedName = crm
        if isinstance(definedName, (XbrlConceptCellRangeMetadata, CellRangeMetadata)):
            self._unused.discard(definedName.definedName)
            return definedName
        return None

    def getSingleCell(
        self,
        definedName: DefinedName
        | str
        | XbrlConceptCellRangeMetadata
        | CellRangeMetadata,
        *,
        row: int = -1,
        column: int = -1,
    ) -> Optional[CellType]:
        if (stuff := self._getCellRangeMetadata(definedName)) is None:
            return None

        cr = stuff.cellRange
        ws = stuff.worksheet

        if not all(
            x is not None for x in (cr.min_row, cr.max_row, cr.min_col, cr.max_col)
        ):
            self._results.addMessage(
                f"Named range {stuff.definedName.name} has an invalid cell range {cr.bounds}.",
                Severity.ERROR,
                MessageType.DevInfo,
                excel_reference=excelDefinedNameRef(stuff.definedName),
            )
            return None

        shouldOverrideRow = row == -1 or stuff.maximum_height == 1
        shouldOverrideColumn = column == -1 or stuff.maximum_width == 1

        if shouldOverrideRow:
            row = cr.min_row
            if stuff.populated_height > 1:
                self._results.addMessage(
                    f"Named range {stuff.definedName.name} has {stuff.populated_height} populated rows but no row was specified; using the first.",
                    Severity.WARNING,
                    MessageType.DevInfo,
                    excel_reference=stuff.excelRef(),
                )

        if shouldOverrideColumn:
            column = cr.min_col
            if stuff.populated_width > 1:
                self._results.addMessage(
                    f"Named range {stuff.definedName.name} has {stuff.populated_width} populated columns but no column was specified; using the first.",
                    Severity.WARNING,
                    MessageType.DevInfo,
                    excel_reference=stuff.excelRef(),
                )

        if not (cr.min_row <= row <= cr.max_row):
            self._results.addMessage(
                f"Row {row} has not been specified correctly.",
                Severity.WARNING,
                MessageType.DevInfo,
                excel_reference=stuff.excelRef(),
            )
            row = cr.min_row
        if not (cr.min_col <= column <= cr.max_col):
            self._results.addMessage(
                f"Column {column} has not been specified correctly.",
                Severity.WARNING,
                MessageType.DevInfo,
                excel_reference=stuff.excelRef(),
            )
            column = cr.min_col

        cell = ws.cell(row=row, column=column)

        if cell is None or cell.value is None:
            return None

        if cell.value == EXCEL_PLACEHOLDER_VALUE:
            self._results.addMessage(
                f"Excel cell has an invalid stored value {EXCEL_PLACEHOLDER_VALUE}. Please check the Excel formula for this specific cell.",
                Severity.ERROR,
                MessageType.ExcelParsing,
                excel_reference=stuff.excelRef(cell),
            )
            return None
        return cell

    def getSingleValue(
        self,
        definedName: DefinedName | str,
        *,
        row: int = -1,
        column: int = -1,
    ) -> CellValueType:
        if (
            cell := self.getSingleCell(definedName, row=row, column=column)
        ) is not None:
            value = cell.value
            if not isinstance(value, CellValueType):
                value = str(value)
            return value
        return None

    def getSingleStringValue(
        self,
        definedName: DefinedName | str,
        *,
        row: int = -1,
        column: int = -1,
        fallbackValue: str = "",
    ) -> str:
        value = self.getSingleValue(definedName, row=row, column=column)
        return str(value) if value is not None else str(fallbackValue)

    def getSingleDateValue(self, definedName: DefinedName | str) -> date:
        value = self.getSingleValue(definedName)
        return getDateFromValue(value)


def index_xbrl_candidates(
    concept_map: dict,
    taxonomy: Taxonomy,
) -> tuple[
    list[XbrlConceptCellRangeMetadata],
    frozenset[Concept],
    dict[Worksheet, list[XbrlConceptCellRangeMetadata]],
]:
    """Single pass over concept_map: the hypercube table ranges, every concept
    present, and a worksheet-keyed index of reportable/dimension candidate ranges."""
    hypercubes = taxonomy.hypercubes
    hypercube_ranges: list[XbrlConceptCellRangeMetadata] = []
    concepts_in_excel: list[Concept] = []
    candidates_by_ws: defaultdict[Worksheet, list[XbrlConceptCellRangeMetadata]] = (
        defaultdict(list)
    )
    for stuff in concept_map.values():
        concept = stuff.concept
        concepts_in_excel.append(concept)
        if concept in hypercubes:
            hypercube_ranges.append(stuff)
        if concept.isReportable or concept.isDimension:
            candidates_by_ws[stuff.worksheet].append(stuff)
    return hypercube_ranges, frozenset(concepts_in_excel), candidates_by_ws


def consume_table_bindings(
    concept_map: dict,
    unit_map: dict,
    bindings: list[TableBinding],
) -> None:
    """Remove resolved table entries from concept_map/unit_map (now held in bindings)."""
    for binding in bindings:
        for crm in binding.conceptRanges:
            concept_map.pop(crm.definedName, None)
        for u in binding.units:
            unit_map.pop(u.concept, None)
