"""XlsxProcessor._verifyEntryPoint(): the workbook names one entry-point URL
(a one-document entry-point set), which must be a loaded taxonomy's."""

import pytest
from openpyxl import Workbook
from openpyxl.utils.cell import absolute_coordinate, quote_sheetname
from openpyxl.workbook.defined_name import DefinedName

from mireport.conversionresults import ConversionResultsBuilder
from mireport.data.disclosures import VSME_DEFAULTS
from mireport.exceptions import EarlyAbortException
from mireport.xlsx_template_reader.processor import XlsxProcessor

_SHEET = "S"
SUPPORTED = VSME_DEFAULTS["taxonomyEntryPoints"]["supportedEntryPoint"]


def processorWithEntryPoint(
    value: object,
) -> tuple[XlsxProcessor, ConversionResultsBuilder]:
    wb = Workbook()
    ws = wb.active
    assert ws is not None
    ws.title = _SHEET
    ws["A1"].value = value
    name = VSME_DEFAULTS["entryPoint"]
    attr = f"{quote_sheetname(_SHEET)}!{absolute_coordinate('A1')}"
    wb.defined_names[name] = DefinedName(name, attr_text=attr)
    results = ConversionResultsBuilder(consoleOutput=False)
    return XlsxProcessor(wb, results, VSME_DEFAULTS), results


def errorTexts(results: ConversionResultsBuilder) -> list[str]:
    return [str(m.messageText) for m in results.messages]


@pytest.mark.parametrize("value", [None, "", "   "], ids=["none", "empty", "blank"])
def test_missing_or_blank_entry_point_is_an_excel_error(value: object) -> None:
    processor, results = processorWithEntryPoint(value)

    with pytest.raises(EarlyAbortException):
        processor._verifyEntryPoint()

    assert any(
        "does not specify taxonomy entry point" in t for t in errorTexts(results)
    )


def test_entry_point_that_is_not_a_uri_is_an_excel_error() -> None:
    processor, results = processorWithEntryPoint("vsme-all.xsd")

    with pytest.raises(EarlyAbortException):
        processor._verifyEntryPoint()

    assert any("not an absolute URI" in t for t in errorTexts(results))


def test_unknown_entry_point_is_an_excel_error() -> None:
    processor, results = processorWithEntryPoint("https://example.com/unknown.xsd")

    with pytest.raises(EarlyAbortException):
        processor._verifyEntryPoint()

    assert any("unsupported taxonomy" in t for t in errorTexts(results))


def test_supported_entry_point_names_its_schema_ref() -> None:
    processor, _ = processorWithEntryPoint(SUPPORTED)

    processor._verifyEntryPoint()

    assert (
        processor._report.getSchemaRefForAoix() == f'{{{{ schema-ref "{SUPPORTED}" }}}}'
    )
