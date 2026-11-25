"""VS 2.0.1's B7 reports the overall percentages of waste diverted to recycling and to preparation
for reuse, by mass and by volume, from the template's named ranges of the same names."""

from datetime import date
from decimal import Decimal

from openpyxl import Workbook
from openpyxl.workbook.defined_name import DefinedName

from mireport.conversionresults import ConversionResultsBuilder
from mireport.data.disclosures import VSME_DEFAULTS
from mireport.report import InlineReport
from mireport.taxonomy import getTaxonomy
from mireport.xlsx_template_reader._binder import WorkbookBinder
from mireport.xlsx_template_reader._fact_creator import FactCreator
from mireport.xlsx_template_reader._reader import WorkbookReader

VS_ENTRY_POINT = "https://xbrl.efrag.org/taxonomy/vs/draft-2026-11-17/vs-all.xsd"
OVERALL = {
    "OverallPercentageOfWasteDivertedToRecyclingMass": 0.25,
    "OverallPercentageOfWasteDivertedToPreparationForReuseMass": 0.5,
    "OverallPercentageOfWasteDivertedToRecyclingVolume": 0.1,
    "OverallPercentageOfWasteDivertedToPreparationForReuseVolume": 0.05,
}


def test_overall_percentages_are_reported() -> None:
    wb = Workbook()
    ws = wb.active
    assert ws is not None
    ws.title = "S"
    for row, (name, value) in enumerate(OVERALL.items(), start=1):
        ws.cell(row=row, column=1, value=value)
        wb.defined_names[name] = DefinedName(name, attr_text=f"S!$A${row}")

    results = ConversionResultsBuilder(consoleOutput=False)
    reader = WorkbookReader(wb, results)
    taxonomy = getTaxonomy(VS_ENTRY_POINT)
    bindings = WorkbookBinder(reader, taxonomy, results).bind()
    report = InlineReport(taxonomy, None)
    report.addDurationPeriod("cur", date(2025, 1, 1), date(2025, 12, 31))
    report.setDefaultPeriodName("cur")
    FactCreator(bindings, reader, report, results, VSME_DEFAULTS).create_all_facts()

    reported = {
        f.concept.qname.localName: Decimal(str(f.value))
        for f in report.facts
        if f.concept.qname.localName in OVERALL
    }
    assert reported == {name: Decimal(str(v)) for name, v in OVERALL.items()}
    assert all(
        str(f.concept.dataType) == "dtr-types:percentItemType"
        for f in report.facts
        if f.concept.qname.localName in OVERALL
    )
