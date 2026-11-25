"""C7's checkbox rows map to its own domain, by position: the fifth, "other", is
OtherContentMember, not C6's AccidentPreventionMember (converter #415)."""

from datetime import date

import pytest
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
C7 = "TypeOfHumanRightRelatedToTheConfirmedIncident"
# The template's C7 rows, in order.
TEMPLATE_ROWS = [
    "vs:ChildLabourMember",
    "vs:ForcedLabourMember",
    "vs:HumanTraffickingMember",
    "vs:DiscriminationMember",
    "vs:OtherContentMember",
]


def tickedMembers(ticked: int) -> list[str]:
    """Convert C7's five template rows with only row *ticked* (0-based) ticked."""
    wb = Workbook()
    ws = wb.active
    assert ws is not None
    ws.title = "S"
    for row in range(len(TEMPLATE_ROWS)):
        ws.cell(row=row + 1, column=1, value=row == ticked)
    wb.defined_names[C7] = DefinedName(C7, attr_text=f"S!$A$1:$A${len(TEMPLATE_ROWS)}")

    results = ConversionResultsBuilder(consoleOutput=False)
    reader = WorkbookReader(wb, results)
    taxonomy = getTaxonomy(VS_ENTRY_POINT)
    bindings = WorkbookBinder(reader, taxonomy, results).bind()
    report = InlineReport(taxonomy, None)
    report.addDurationPeriod("cur", date(2025, 1, 1), date(2025, 12, 31))
    report.setDefaultPeriodName("cur")
    FactCreator(bindings, reader, report, results, VSME_DEFAULTS).create_all_facts()
    [fact] = [f for f in report.facts if str(f.concept.qname) == f"vs:{C7}"]
    assert fact.enumeration is not None
    return [str(member.qname) for member in fact.enumeration]


def test_c7_domain_is_the_template_rows() -> None:
    domain = getTaxonomy(VS_ENTRY_POINT).getConcept(f"vs:{C7}").getEEDomain()
    assert [str(member.qname) for member in domain] == TEMPLATE_ROWS


@pytest.mark.parametrize("row", range(len(TEMPLATE_ROWS)))
def test_each_c7_row_reports_its_member(row: int) -> None:
    assert tickedMembers(row) == [TEMPLATE_ROWS[row]]
