"""Unit tests for FootnoteFactCreator._iterFootnoteRows row/boundary handling.

The footnote table layout: the text column delimits footnotes (a non-merged
cell starts a new footnote; merged continuation cells belong to the one
above), and the ref column holds concept references for the current footnote.
"""

from openpyxl import Workbook
from openpyxl.utils.cell import absolute_coordinate, quote_sheetname
from openpyxl.workbook.defined_name import DefinedName

from mireport.conversionresults import ConversionResultsBuilder
from mireport.xlsx_template_reader._bindings import FootnoteBinding
from mireport.xlsx_template_reader._footnotes import (
    FootnoteFactCreator,
    _columnIndices,
)
from mireport.xlsx_template_reader._messages import Messenger
from mireport.xlsx_template_reader._reader import WorkbookReader

_SHEET = "Sheet"


def _wb() -> Workbook:
    wb = Workbook()
    wb.active.title = _SHEET  # type: ignore[union-attr]
    return wb


def _crm(wb: Workbook, reader: WorkbookReader, name: str, ref: str):
    attr = f"{quote_sheetname(_SHEET)}!{absolute_coordinate(ref)}"
    wb.defined_names[name] = DefinedName(name, attr_text=attr)
    crm = reader.peekRange(wb.defined_names[name])
    assert crm is not None
    return crm


def _footnotes(wb: Workbook, table_ref: str, text_ref: str, ref_ref: str):
    """Build the binding and return the (text, labels) pairs _iterFootnoteRows yields."""
    reader = WorkbookReader(wb, ConversionResultsBuilder(consoleOutput=False))
    table = _crm(wb, reader, "footnote_table", table_ref)
    text = _crm(wb, reader, "footnote_text", text_ref)
    ref = _crm(wb, reader, "footnote_ref_concept", ref_ref)
    binding = FootnoteBinding(table=table, text=text, ref=ref, ref_dimension=None)
    creator = FootnoteFactCreator(
        report=None,  # type: ignore[arg-type] # _iterFootnoteRows doesn't touch the report
        msg=Messenger(ConversionResultsBuilder(consoleOutput=False)),
        binding=binding,
    )
    origin = table.cellRange.min_col
    return [
        (text_value, [label for label, _, _ in label_cells])
        for text_value, label_cells in creator._iterFootnoteRows(
            table, _columnIndices(text, origin), _columnIndices(ref, origin)
        )
    ]


def test_refs_under_blank_text_do_not_leak_into_next_footnote():
    """Refs on rows whose text cell is blank belong to no footnote and must be
    discarded, not attributed to the next footnote."""
    wb = _wb()
    ws = wb.active
    assert ws is not None
    # Row 1: blank text cell with a stray ref; row 2: a real footnote.
    ws["B1"] = "LeakedRef"
    ws["A2"] = "Real footnote"
    ws["B2"] = "RealRef"

    footnotes = _footnotes(wb, "A1:B2", "A1:A2", "B1:B2")

    assert footnotes == [("Real footnote", ["RealRef"])]


def test_merged_text_block_collects_refs_from_all_its_rows():
    """Refs on every row of a merged text block belong to that footnote."""
    wb = _wb()
    ws = wb.active
    assert ws is not None
    ws.merge_cells("A1:A2")
    ws["A1"] = "Footnote one"
    ws["B1"] = "Ref1"
    ws["B2"] = "Ref2"
    ws["A3"] = "Footnote two"
    ws["B3"] = "Ref3"

    footnotes = _footnotes(wb, "A1:B3", "A1:A3", "B1:B3")

    assert footnotes == [
        ("Footnote one", ["Ref1", "Ref2"]),
        ("Footnote two", ["Ref3"]),
    ]
