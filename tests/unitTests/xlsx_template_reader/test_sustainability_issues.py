"""B2's tick boxes map to the sustainability issues domain by position, so the domain
must follow the template's nine columns: Own workforce and Workers in the value chain
are one issue, and Water is no longer Water and marine resources (converter #417, #418)."""

from datetime import date

import pytest
from openpyxl import Workbook
from openpyxl.utils import get_column_letter
from openpyxl.workbook.defined_name import DefinedName

from mireport.conversionresults import ConversionResultsBuilder
from mireport.data.disclosures import VSME_DEFAULTS
from mireport.report import InlineReport
from mireport.taxonomy import getTaxonomy
from mireport.xlsx_template_reader._binder import WorkbookBinder
from mireport.xlsx_template_reader._fact_creator import FactCreator
from mireport.xlsx_template_reader._reader import WorkbookReader

VS_ENTRY_POINT = "https://xbrl.efrag.org/taxonomy/vs/draft-2026-11-17/vs-all.xsd"
B2 = "SustainabilityIssueAddressedByPracticePolicyAndOrFutureInitiative"
# The template's B2 columns, in order.
TEMPLATE_COLUMNS = [
    "vs:ClimateChangeMember",
    "vs:PollutionMember",
    "vs:WaterMember",
    "vs:BiodiversityAndEcosystemsMember",
    "vs:CircularEconomyAndResourceUseMember",
    "vs:OwnWorkforceAndWorkersInTheValueChainMember",
    "vs:AffectedCommunitiesMember",
    "vs:ConsumersAndEndUsersMember",
    "vs:BusinessConductMember",
]


def tickedMembers(ticked: int) -> list[str]:
    """Convert B2's nine template columns with only column *ticked* (0-based) ticked."""
    wb = Workbook()
    ws = wb.active
    assert ws is not None
    ws.title = "S"
    for column in range(len(TEMPLATE_COLUMNS)):
        ws.cell(row=1, column=column + 1, value=column == ticked)
    last = get_column_letter(len(TEMPLATE_COLUMNS))
    wb.defined_names[B2] = DefinedName(B2, attr_text=f"S!$A$1:${last}$1")

    results = ConversionResultsBuilder(consoleOutput=False)
    reader = WorkbookReader(wb, results)
    taxonomy = getTaxonomy(VS_ENTRY_POINT)
    bindings = WorkbookBinder(reader, taxonomy, results).bind()
    report = InlineReport(taxonomy, None)
    report.addDurationPeriod("cur", date(2025, 1, 1), date(2025, 12, 31))
    report.setDefaultPeriodName("cur")
    FactCreator(bindings, reader, report, results, VSME_DEFAULTS).create_all_facts()
    [fact] = [f for f in report.facts if str(f.concept.qname) == f"vs:{B2}"]
    assert fact.enumeration is not None
    return [str(member.qname) for member in fact.enumeration]


def test_b2_domain_is_the_template_columns() -> None:
    domain = getTaxonomy(VS_ENTRY_POINT).getConcept(f"vs:{B2}").getEEDomain()
    assert [str(member.qname) for member in domain] == TEMPLATE_COLUMNS


@pytest.mark.parametrize("column", range(len(TEMPLATE_COLUMNS)))
def test_each_b2_column_reports_its_member(column: int) -> None:
    assert tickedMembers(column) == [TEMPLATE_COLUMNS[column]]
