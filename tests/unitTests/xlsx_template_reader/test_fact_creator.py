from pathlib import Path

import pytest

from mireport.conversionresults import ConversionResultsBuilder
from mireport.report import InlineReport
from mireport.taxonomy import getTaxonomy
from mireport.xlsx_template_reader._fact_creator import FactCreator
from mireport.xlsx_template_reader._range_mapper import build_bindings
from mireport.xlsx_template_reader._util import (
    WorkbookReader,
    loadExcelFromPathOrFileLike,
)
from mireport.xlsx_template_reader.processor import VSME_DEFAULTS, ExcelProcessor

SAMPLE = (
    Path(__file__).parent.parent.parent
    / "data"
    / "VSME-Digital-Template-Sample-1.2.0.xlsx"
)


def _results() -> ConversionResultsBuilder:
    return ConversionResultsBuilder(consoleOutput=False)


@pytest.fixture(scope="module")
def full_pipeline_fact_count():
    ep = ExcelProcessor.from_file(SAMPLE, _results(), VSME_DEFAULTS)
    return ep.populateReport().factCount


@pytest.fixture(scope="module")
def fact_creator_fact_count():
    wb = loadExcelFromPathOrFileLike(SAMPLE)
    results = _results()
    reader = WorkbookReader(wb, set(), results)

    entry_point = reader.getSingleStringValue(VSME_DEFAULTS.get("entryPoint", ""))
    taxonomy = getTaxonomy(entry_point)

    report = InlineReport(taxonomy, None)
    report.addSchemaRef(entry_point)

    entity_id_schemes: dict = VSME_DEFAULTS.get("entityIdentifierLabelsToSchemes", {})
    for aoix_name, named_range in VSME_DEFAULTS.get("aoix", {}).items():
        if aoix_name == "entity-scheme":
            raw = (
                reader.getSingleStringValue(named_range)
                .strip()
                .replace(" ", "")
                .lower()
            )
            value = entity_id_schemes.get(raw, "")
        else:
            value = reader.getSingleStringValue(named_range).strip()
        if value:
            report.setDefaultAspect(aoix_name, value)

    for period in VSME_DEFAULTS.get("periods", []):
        start = reader.getSingleDateValue(period["start"])
        end = reader.getSingleDateValue(period["end"])
        if report.addDurationPeriod(period["name"], start, end):
            report.setDefaultPeriodName(period["name"])

    bindings = build_bindings(reader, taxonomy, VSME_DEFAULTS)
    try:
        FactCreator(bindings, reader, report, results, VSME_DEFAULTS).create_all_facts()
    finally:
        wb.close()

    return report.factCount


class TestFactCreatorImport:
    def test_fact_creator_importable(self):
        assert FactCreator is not None

    def test_has_create_all_facts(self):
        assert callable(FactCreator.create_all_facts)

    def test_has_create_simple_facts(self):
        assert hasattr(FactCreator, "createSimpleFacts")

    def test_has_create_table_facts(self):
        assert hasattr(FactCreator, "createTableFacts")


@pytest.mark.slow
class TestFactCreatorIntegration:
    def test_fact_count_is_positive(self, fact_creator_fact_count):
        assert fact_creator_fact_count > 0

    def test_same_fact_count_as_full_pipeline(
        self, fact_creator_fact_count, full_pipeline_fact_count
    ):
        assert fact_creator_fact_count == full_pipeline_fact_count
