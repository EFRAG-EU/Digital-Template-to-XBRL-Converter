from __future__ import annotations

import logging
import re
import warnings
from collections import defaultdict
from datetime import date, datetime, time
from itertools import combinations
from pathlib import Path
from typing import (
    TYPE_CHECKING,
    BinaryIO,
    Iterable,
    Iterator,
    Literal,
    NamedTuple,
    Optional,
    TypeAlias,
    overload,
)

if TYPE_CHECKING:
    from mireport.taxonomy import Concept, Taxonomy

from dateutil.parser import parse as parse_datetime
from openpyxl import Workbook, load_workbook
from openpyxl.cell import Cell, MergedCell, ReadOnlyCell
from openpyxl.utils.cell import absolute_coordinate, quote_sheetname
from openpyxl.workbook.defined_name import DefinedName
from openpyxl.worksheet.cell_range import CellRange
from openpyxl.worksheet.worksheet import Worksheet

from mireport.conversionresults import ConversionResultsBuilder, MessageType, Severity
from mireport.exceptions import OpenPyXlRelatedException
from mireport.typealiases import DecimalPlaces
from mireport.xlsx_template_reader._bindings import (
    CellRangeMetadata,
    WorkbookBindings,
    XbrlConceptCellRangeMetadata,
    XbrlTableCellRangeMetadataHolder,
)

L = logging.getLogger(__name__)

CellType: TypeAlias = ReadOnlyCell | MergedCell | Cell
CellValueType: TypeAlias = bool | float | int | str | datetime | date | time | None

EXCEL_PLACEHOLDER_VALUE = "#VALUE!"
EXCEL_VALUES_TO_BE_TREATED_AS_NONE_VALUE = frozenset({"-", EXCEL_PLACEHOLDER_VALUE})
_IGNORED_DEFINED_NAME_PREFIXES = ("enum_", "template_")
_EXTERNAL_VALUES_RANGE = "template_external_values"


def conceptsToText(concepts: Iterable[Concept]) -> str:
    return ", ".join(sorted(str(c.qname) for c in concepts))


class NamedRangeException(OpenPyXlRelatedException):
    """Exception raised when a named range is broken in the workbook."""

    def __init__(self, message: str, defined_name: DefinedName) -> None:
        self.message = message
        self.defined_name = defined_name
        super().__init__(message, str(defined_name))

    def __str__(self) -> str:
        details = (
            f"Details:\n"
            f"  Name: {self.defined_name.name}\n"
            f"  Refers to: {self.defined_name.attr_text}\n"
        )
        return f"{self.message} {details}"


def checkExcelFilePath(path: Path) -> None:
    if not path.is_file():
        raise FileNotFoundError(f'"{path}" is not a file.')
    elif path.suffix != ".xlsx":
        raise Exception(f'"{path}" is not a supported (.xlsx) Excel file')


def loadExcelFromPathOrFileLike(
    pathOrFile: Path | BinaryIO, read_only: bool = False
) -> Workbook:
    # We can safely suppress these warnings as our use-case is **just**
    # extracting data from the Excel file.
    with warnings.catch_warnings():
        warnings.filterwarnings(
            "ignore",
            message=".*? extension is not supported and will be removed",
            category=UserWarning,
            module=r"openpyxl\.worksheet\._reader",
        )
        wb = load_workbook(
            filename=pathOrFile, read_only=read_only, data_only=True, rich_text=True
        )
    return wb


def excelCellRef(worksheet: Worksheet, cell: CellType) -> str:
    """Make an Excel cell reference such as 'Example sheet'!$A$5"""
    ref = f"{quote_sheetname(worksheet.title)}!{absolute_coordinate(cell.coordinate)}"
    return ref


def excelCellRangeRef(worksheet: Worksheet, cellRange: CellRange) -> str:
    """Make an Excel cell reference such as 'Example sheet'!$A$5"""
    ref = f"{quote_sheetname(worksheet.title)}!{absolute_coordinate(cellRange.coord)}"
    return ref


def excelCellOrCellRangeRef(
    worksheet: Worksheet, cellRange: CellRange, cell: CellType | None
) -> str:
    """Make an Excel cell reference such as 'Example sheet'!$A$5"""
    if cell is not None:
        return excelCellRef(worksheet, cell)
    elif cellRange is not None:
        return excelCellRangeRef(worksheet, cellRange)
    else:
        return None


def excelDefinedNameRef(
    definedName: Optional[DefinedName], cell: Optional[CellType] = None
) -> Optional[str]:
    """Make an Excel cell reference such as 'Example sheet'!$A$5"""
    if definedName is None:
        return None

    destinations = list(definedName.destinations)
    match len(destinations):
        case 1:
            sheet_name, cell_range = destinations[0]
            if cell is not None:
                coord = cell.coordinate
            else:
                coord = cell_range
            ref = f"{quote_sheetname(sheet_name)}!{absolute_coordinate(coord)}"
            return ref
        case _:
            return None


def getNamedRanges(
    wb: Workbook,
) -> tuple[dict[str, list[CellValueType]], list[NamedRangeException]]:
    data = {}
    errors = []
    for dn in list(wb.defined_names.values()):
        if not dn.name:
            errors.append(
                NamedRangeException("Named range exists but has no name.", dn)
            )
            continue

        if not dn.destinations:
            errors.append(
                NamedRangeException("Named range has no destination specified.", dn)
            )
            continue

        sheet_name, cell_range = list(dn.destinations)[0]

        if sheet_name not in wb:
            errors.append(
                NamedRangeException(
                    "Named range refers to a worksheet that is not in the workbook.", dn
                )
            )
            continue

        ws = wb[sheet_name]

        if not cell_range:
            errors.append(
                NamedRangeException("Named range has no cell range specified.", dn)
            )
            continue

        cr = CellRange(cell_range)

        if (
            cr.min_col is None
            or cr.min_row is None
            or cr.max_col is None
            or cr.max_row is None
        ):
            errors.append(
                NamedRangeException(
                    f"Named range cell range bounds expected to be int but actually None {cr=}.",
                    dn,
                )
            )
            continue

        width: int = cr.max_col - cr.min_col
        height: int = cr.max_row - cr.min_row
        if width < 0 or height < 0:
            errors.append(
                NamedRangeException(
                    f"Named range has negative cell range {width=} {height=}.", dn
                )
            )
            continue

        if not width and not height:
            # a single (width=0, height=0) cell range ... so the OpenPyXL API returns it directly.
            cell = ws[cell_range]
            values = [cell.value]
        else:
            values = []
            for row in ws[cell_range]:
                values.extend([c.value for c in row])
        data[dn.name] = values

    return data, errors


def get_decimal_places(cell: CellType) -> DecimalPlaces:
    """
    Returns the number of decimal places in the cell's number format. For
    example, a format of '0.00' would return 2.

    If no decimal places are specified, return Literal['INF'], meaning infinite
    precision, include all digits in display.
    """
    number_format = cell.number_format

    # Match typical decimal number formats like '0.00', '#,##0.000', etc.
    match = re.search(r"\.(0+)", number_format)
    if match:
        return len(match.group(1))

    # Handle cases like percentage formats '0.0%' or '0.000%'
    match_percent = re.search(r"\.(0+)%", number_format)
    if match_percent:
        return len(match_percent.group(1))

    # Catch general cases like scientific notation '0.00E+00'
    match_sci = re.search(r"\.(0+)[eE]", number_format)
    if match_sci:
        return len(match_sci.group(1))

    return "INF"


def getDateFromValue(value: object) -> date:
    if isinstance(value, datetime):
        return value.date()
    elif isinstance(value, date):
        return value
    elif isinstance(value, str):
        if "-" in value:
            return date.fromisoformat(value)
        elif "/" in value:
            return parse_datetime(value, yearfirst=False, dayfirst=True).date()
        raise ValueError(f"Unsupported date string: '{value}'")
    else:
        raise TypeError(f"Unsupported type for date conversion: {type(value).__name__}")


@overload
def _getIteratorForCellRange(
    ws: Worksheet,
    cr: CellRange,
    group_by_row: Literal[False] = ...,
    only_cells: Literal[False] = ...,
) -> Iterator[tuple[int, int, CellType]]: ...


@overload
def _getIteratorForCellRange(
    ws: Worksheet,
    cr: CellRange,
    *,
    group_by_row: Literal[True],
    only_cells: Literal[False] = ...,
) -> Iterator[tuple[int, tuple[CellType, ...]]]: ...


@overload
def _getIteratorForCellRange(
    ws: Worksheet,
    cr: CellRange,
    group_by_row: Literal[False] = ...,
    *,
    only_cells: Literal[True],
) -> Iterator[CellType]: ...


def _getIteratorForCellRange(
    ws: Worksheet,
    cr: CellRange,
    group_by_row: bool = False,
    only_cells: bool = False,
) -> Iterator[tuple[int, int, CellType] | tuple[int, tuple[CellType, ...]] | CellType]:
    """Iterates over cells in the given range, supporting standard, row-grouped, and cells-only modes."""
    if group_by_row and only_cells:
        raise ValueError("group_by_row and only_cells are mutually exclusive.")
    if cr.min_row is None or cr.min_col is None:
        raise OpenPyXlRelatedException(
            f"Cell range bounds expected to be int but actually None {cr=}"
        )
    for rnum, row in enumerate(
        ws.iter_rows(
            min_row=cr.min_row,
            min_col=cr.min_col,
            max_row=cr.max_row,
            max_col=cr.max_col,
        ),
        start=cr.min_row,
    ):
        if group_by_row:
            yield rnum, row
        elif only_cells:
            yield from row
        else:
            for cnum, cell in enumerate(row, start=cr.min_col):
                yield rnum, cnum, cell


@overload
def getIteratorForCellRangeMetadata(
    metadata: CellRangeMetadata,
    group_by_row: Literal[False] = ...,
    only_cells: Literal[False] = ...,
) -> Iterator[tuple[int, int, CellType]]: ...


@overload
def getIteratorForCellRangeMetadata(
    metadata: CellRangeMetadata,
    *,
    group_by_row: Literal[True],
    only_cells: Literal[False] = ...,
) -> Iterator[tuple[int, tuple[CellType, ...]]]: ...


@overload
def getIteratorForCellRangeMetadata(
    metadata: CellRangeMetadata,
    group_by_row: Literal[False] = ...,
    *,
    only_cells: Literal[True],
) -> Iterator[CellType]: ...


def getIteratorForCellRangeMetadata(
    metadata: CellRangeMetadata,
    group_by_row: bool = False,
    only_cells: bool = False,
) -> Iterator[tuple[int, int, CellType] | tuple[int, tuple[CellType, ...]] | CellType]:
    """Convenience wrapper around getCellRangeIterator for callers that hold a CellRangeMetadata."""
    if group_by_row:
        return _getIteratorForCellRange(
            metadata.worksheet, metadata.cellRange, group_by_row=True
        )
    if only_cells:
        return _getIteratorForCellRange(
            metadata.worksheet, metadata.cellRange, only_cells=True
        )
    return _getIteratorForCellRange(metadata.worksheet, metadata.cellRange)


class _CellRangeDimensions(NamedTuple):
    cellsAccessed: set[tuple[str, int, int]]
    cellsPopulated: set[tuple[str, int, int]]
    populated_width: int
    populated_height: int
    populated_min_col: int
    populated_min_row: int

    @property
    def countAccessed(self) -> int:
        return len(self.cellsAccessed)

    @property
    def countPopulated(self) -> int:
        return len(self.cellsPopulated)


def _getEffectiveCellRangeDimensions(
    ws: Worksheet, cell_range: CellRange
) -> _CellRangeDimensions:
    cols_not_empty: set[int] = set()
    cols_with_none: set[int] = set()
    populated_rows: set[int] = set()
    populatedCellCount: set[tuple[str, int, int]] = set()
    cellCount: set[tuple[str, int, int]] = set()

    last_rnum = None
    empty_row = True
    sheetName = ws.title
    for rnum, cnum, cell in _getIteratorForCellRange(ws, cell_range):
        cellCount.add((sheetName, rnum, cnum))
        if last_rnum is None:
            last_rnum = rnum

        if rnum != last_rnum:
            if not empty_row:
                populated_rows.add(last_rnum)
            last_rnum = rnum
            empty_row = True

        if cell.value is not None:
            populatedCellCount.add((sheetName, rnum, cnum))
            empty_row = False
            cols_not_empty.add(cnum)
        else:
            cols_with_none.add(cnum)
    else:
        if last_rnum is not None and not empty_row:
            populated_rows.add(last_rnum)

    definitely_empty_cols = cols_with_none - cols_not_empty
    total_cols = len(cols_not_empty.union(cols_with_none))
    populated_width = max(1, total_cols - len(definitely_empty_cols))
    populated_height = max(1, len(populated_rows))
    populated_min_col = min(cols_not_empty, default=None) or cell_range.min_col
    populated_min_row = min(populated_rows, default=None) or cell_range.min_row
    return _CellRangeDimensions(
        cellsAccessed=cellCount,
        cellsPopulated=populatedCellCount,
        populated_width=populated_width,
        populated_height=populated_height,
        populated_min_col=populated_min_col,
        populated_min_row=populated_min_row,
    )


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
            if (name := dn.name) and not name.startswith(_IGNORED_DEFINED_NAME_PREFIXES)
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

        table_map: dict = {}

        tables = [
            (dn, stuff)
            for dn, stuff in concept_map.items()
            if stuff.concept in taxonomy.hypercubes
        ]
        concepts_in_excel = frozenset(stuff.concept for stuff in concept_map.values())
        hc_concepts_in_excel = frozenset(c for c in concepts_in_excel if c.isHypercube)
        used_empty_hypercubes = taxonomy.emptyHypercubes.intersection(
            hc_concepts_in_excel
        )
        if used_empty_hypercubes:
            results.addMessage(
                f"The following hypercubes exist and have corresponding named ranges but they cannot be used due to missing taxonomy definitions: {conceptsToText(used_empty_hypercubes)}.",
                Severity.ERROR,
                MessageType.DevInfo,
            )

        for table, table_stuff in tables:
            tableCr = table_stuff.cellRange
            tableWorksheet = table_stuff.worksheet
            table_concept = table_stuff.concept

            allPermittedConceptsForTable = taxonomy.getDimensionsForHypercube(
                table_concept
            ).union(
                {
                    concept
                    for concept in taxonomy.getPrimaryItemsForHypercube(table_concept)
                    if concept.isReportable or concept.isDimension
                }
            )
            missing_from_excel = allPermittedConceptsForTable.difference(
                concepts_in_excel
            )
            if missing_from_excel:
                results.addMessage(
                    f"Expected Dimensions or Primary Items for hypercube {table.name} have not been found: {conceptsToText(missing_from_excel)}.",
                    Severity.WARNING,
                    MessageType.DevInfo,
                )

            candidates: list[XbrlConceptCellRangeMetadata] = []
            extras_in_excel: set[XbrlConceptCellRangeMetadata] = set()
            for dn, stuff in concept_map.items():
                if tableWorksheet is not stuff.worksheet:
                    continue
                concept = stuff.concept
                if not (concept.isReportable or concept.isDimension):
                    continue
                if tableCr.issuperset(stuff.cellRange):
                    if concept in allPermittedConceptsForTable:
                        candidates.append(stuff)
                    else:
                        extras_in_excel.add(stuff)
                elif not tableCr.isdisjoint(stuff.cellRange):
                    extras_in_excel.add(stuff)

            if extras_in_excel:
                results.addMessage(
                    f"Extra named ranges found within/overlapping bounds of {table.name} named range but not supported by Hypercube {table_stuff.concept.qname}: {extras_in_excel}.",
                    Severity.WARNING,
                    MessageType.DevInfo,
                )

            fishy = False
            for c1, c2 in combinations(candidates, 2):
                disjoint = c1.cellRange.isdisjoint(c2.cellRange)
                same = (
                    c1.concept.isReportable
                    and c2.concept.isReportable
                    and (c1.cellRange.bounds == c2.cellRange.bounds)
                )
                if not (disjoint or same):
                    fishy = True
                    results.addMessage(
                        f"Named range (table) {table.name} has named ranges (primary items or dimensions) {c1.definedName.name} and {c2.definedName.name} that are neither the same nor disjoint. Ignoring table.",
                        Severity.ERROR,
                        MessageType.ExcelParsing,
                    )
                    break

            if not fishy:
                pItems = [c for c in candidates if c.concept.isReportable]
                eDims = [c for c in candidates if c.concept.isExplicitDimension]
                tDims = [c for c in candidates if c.concept.isTypedDimension]
                units = [
                    u for p in pItems if (u := unit_map.get(p.concept)) is not None
                ]
                table_map[table_stuff] = XbrlTableCellRangeMetadataHolder(
                    primaryItems=pItems,
                    explicitDimensions=eDims,
                    typedDimensions=tDims,
                    units=units,
                )

        # Remove table entries from concept_map (they're now in table_map)
        for tableStuff, table_contents in table_map.items():
            concept_map.pop(tableStuff.definedName, None)
            table_dict = table_contents._asdict()
            for name, part_list in table_dict.items():
                for holder in part_list:
                    if "units" == name:
                        unit_map.pop(holder.concept, None)
                    else:
                        concept_map.pop(holder.definedName, None)

        has_external_value: set[Concept] = set()
        if (ext_dn := self._workbook.defined_names.get(_EXTERNAL_VALUES_RANGE)) and (
            crh := self._createCellRangeMetadata(ext_dn)
        ):
            for cell in getIteratorForCellRangeMetadata(crh, only_cells=True):
                if not isinstance(cell.value, str):
                    continue
                name_or_label = cell.value.strip()
                if (
                    not name_or_label
                    or name_or_label in EXCEL_VALUES_TO_BE_TREATED_AS_NONE_VALUE
                ):
                    continue
                concept = taxonomy.getConceptForName(
                    name_or_label
                ) or taxonomy.getConceptForLabel(name_or_label)
                if concept is None or not concept.isTextblock:
                    self._results.addMessage(
                        f"External value specified in {_EXTERNAL_VALUES_RANGE} named range but no matching concept found for name or label '{name_or_label}'.",
                        Severity.WARNING,
                        MessageType.DevInfo,
                        excel_reference=excelCellRef(crh.worksheet, cell),
                    )
                    continue
                has_external_value.add(concept)

        return WorkbookBindings(
            concept_map=concept_map,
            table_map=table_map,
            unit_map=unit_map,
            preset_dims=preset_dims,
            has_external_value=frozenset(has_external_value),
        )

    def _createCellRangeMetadata(self, dn: DefinedName) -> Optional[CellRangeMetadata]:
        all_destinations = list(dn.destinations)
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
            L.exception("OpenPyXL is sad.", exc_info=e)
            return None
        dims = _getEffectiveCellRangeDimensions(ws, cr)
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

        if cr.min_row == cr.max_row:
            row = cr.min_row
        if cr.min_col == cr.max_col:
            column = cr.min_col

        if row == -1:
            row = cr.min_row
        if column == -1:
            column = cr.min_col

        if not (cr.min_row <= row <= cr.max_row):
            self._results.addMessage(
                f"Row {row} has not been specified correctly.",
                Severity.WARNING,
                MessageType.DevInfo,
                excel_reference=excelCellRangeRef(ws, cr),
            )
            row = cr.min_row
        if not (cr.min_col <= column <= cr.max_col):
            self._results.addMessage(
                f"Column {column} has not been specified correctly.",
                Severity.WARNING,
                MessageType.DevInfo,
                excel_reference=excelCellRangeRef(ws, cr),
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
                excel_reference=excelCellRef(ws, cell),
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
