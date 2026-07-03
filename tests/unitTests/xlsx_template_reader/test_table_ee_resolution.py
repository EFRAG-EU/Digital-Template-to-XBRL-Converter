"""Table EE cells must use the same label-resolution chain as simple facts:
exact standard label -> configured cell-value alias -> closest EE-domain match.

Historically the table path used a bare label lookup (the since-removed
getConceptForLabel), so table cells with slightly-off labels errored where
simple-fact cells resolved.
"""

from collections import defaultdict

import pytest
from openpyxl import Workbook
from openpyxl.utils.cell import absolute_coordinate, quote_sheetname
from openpyxl.workbook.defined_name import DefinedName

from mireport.conversionresults import ConversionResultsBuilder, Severity
from mireport.data.disclosures import VSME_DEFAULTS
from mireport.report import InlineReport
from mireport.taxonomy import getTaxonomy, listTaxonomies
from mireport.xlsx_template_reader._bindings import TableBinding, WorkbookBindings
from mireport.xlsx_template_reader._config import ConverterConfig
from mireport.xlsx_template_reader._messages import Messenger
from mireport.xlsx_template_reader._ranges import XbrlConceptCellRangeMetadata
from mireport.xlsx_template_reader._reader import WorkbookReader
from mireport.xlsx_template_reader._tables import TableFactCreator
from mireport.xlsx_template_reader._units import UnitResolver

_SHEET = "S"


@pytest.fixture(scope="module")
def taxonomy():
    entry_point = next(ep for ep in listTaxonomies() if "vsme" in ep.lower())
    return getTaxonomy(entry_point)


@pytest.fixture(scope="module")
def ee_concept(taxonomy):
    return next(
        c
        for c in sorted(taxonomy.concepts, key=str)
        if c.isEnumerationSingle and c.isReportable and c.getEEDomain()
    )


def _table_env(taxonomy, ee_concept, cell_value):
    """A one-cell 'table' whose only primary item is the EE concept."""
    wb = Workbook()
    ws = wb.active
    assert ws is not None
    ws.title = _SHEET
    ws["A1"] = cell_value
    attr = f"{quote_sheetname(_SHEET)}!{absolute_coordinate('A1')}"
    wb.defined_names["pri"] = DefinedName("pri", attr_text=attr)

    results = ConversionResultsBuilder(consoleOutput=False)
    reader = WorkbookReader(wb, results)
    crm = reader.peekRange(wb.defined_names["pri"])
    assert crm is not None
    pri = XbrlConceptCellRangeMetadata.fromCellRangeMetadata(crm, ee_concept)
    table_binding = TableBinding(
        table=pri,
        primaryItems=[pri],
        explicitDimensions=[],
        typedDimensions=[],
        units=[],
    )
    bindings = WorkbookBindings(
        concept_map={},
        tables=[table_binding],
        unit_map={},
        preset_dims=defaultdict(dict),
        has_external_value=frozenset(),
        footnote=None,
    )
    report = InlineReport(taxonomy, None)
    config = ConverterConfig.fromDefaults(VSME_DEFAULTS, taxonomy)
    msg = Messenger(results)
    units = UnitResolver(report, config, msg, reader, {})
    creator = TableFactCreator(report, reader, msg, config, units, bindings)
    return creator, results


def _messages(results, severity):
    return [str(m.messageText) for m in results.messages if m.severity is severity]


def test_exact_label_resolves_without_ee_errors(taxonomy, ee_concept):
    member = ee_concept.getEEDomain()[0]
    creator, results = _table_env(taxonomy, ee_concept, member.getStandardLabel())
    creator.createTableFacts()
    errors = _messages(results, Severity.ERROR)
    assert not any("Unable to find EE concept" in e for e in errors), errors


def test_fuzzy_label_resolves_with_closest_match_warning(taxonomy, ee_concept):
    """A typo'd member label must fuzzy-resolve (with the standard 'closest
    match' warning) instead of erroring, as it already does for simple facts."""
    member = ee_concept.getEEDomain()[0]
    typo = member.getStandardLabel() + " x"
    creator, results = _table_env(taxonomy, ee_concept, typo)
    creator.createTableFacts()

    errors = _messages(results, Severity.ERROR)
    assert not any("Unable to find EE concept" in e for e in errors), errors
    warnings = _messages(results, Severity.WARNING)
    assert any("closest match" in w for w in warnings), warnings
