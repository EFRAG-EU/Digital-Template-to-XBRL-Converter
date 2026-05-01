from pathlib import Path

import pytest

from mireport.conversionresults import ConversionResultsBuilder
from mireport.taxonomy import getTaxonomy
from mireport.xlsx_template_reader._bindings import (
    CellAndXBRLMetadataHolder,
    WorkbookBindings,
)
from mireport.xlsx_template_reader._util import (
    WorkbookReader,
    loadExcelFromPathOrFileLike,
)
from mireport.xlsx_template_reader.processor import VSME_DEFAULTS

SAMPLE = (
    Path(__file__).parent.parent.parent
    / "data"
    / "VSME-Digital-Template-Sample-1.2.0.xlsx"
)


def _results() -> ConversionResultsBuilder:
    return ConversionResultsBuilder(consoleOutput=False)


@pytest.fixture(scope="module")
def bindings():
    wb = loadExcelFromPathOrFileLike(SAMPLE)
    reader = WorkbookReader(wb, _results())
    entry_point = reader.getSingleStringValue(VSME_DEFAULTS.get("entryPoint", ""))
    taxonomy = getTaxonomy(entry_point)
    b = reader.build_bindings(taxonomy, VSME_DEFAULTS)
    yield b
    wb.close()


class TestRangeMapperImport:
    def test_has_build_bindings(self):
        assert callable(WorkbookReader.build_bindings)


@pytest.mark.slow
class TestBuildBindingsIntegration:
    def test_returns_workbook_bindings(self, bindings):
        assert isinstance(bindings, WorkbookBindings)

    def test_concept_map_is_non_empty(self, bindings):
        assert len(bindings.concept_map) > 0

    def test_table_map_is_non_empty(self, bindings):
        assert len(bindings.table_map) > 0

    def test_concept_map_values_are_cell_and_xbrl_metadata_holders(self, bindings):
        first = next(iter(bindings.concept_map.values()))
        assert isinstance(first, CellAndXBRLMetadataHolder)
