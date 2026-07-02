"""Tests for ConverterConfig: the typed, parsed-once view of the defaults dict."""

from pathlib import Path

import pytest

from mireport.data.disclosures import VSME_DEFAULTS
from mireport.taxonomy import getTaxonomy
from mireport.xlsx_template_reader._config import ComplexUnit, ConverterConfig
from mireport.xlsx_template_reader._reader import WorkbookReader
from mireport.xlsx_template_reader.util import loadExcelFromPathOrFileLike

SAMPLE = (
    Path(__file__).parent.parent.parent
    / "data"
    / "VSME-Digital-Template-Sample-1.2.0.xlsx"
)


@pytest.fixture(scope="module")
def taxonomy():
    from mireport.conversionresults import ConversionResultsBuilder

    wb = loadExcelFromPathOrFileLike(SAMPLE)
    try:
        reader = WorkbookReader(wb, ConversionResultsBuilder(consoleOutput=False))
        return getTaxonomy(reader.value(VSME_DEFAULTS["entryPoint"]).asString())
    finally:
        wb.close()


@pytest.fixture(scope="module")
def config(taxonomy):
    return ConverterConfig.fromDefaults(VSME_DEFAULTS, taxonomy)


class TestConverterConfig:
    def test_data_types_to_units_are_qnames(self, config, taxonomy):
        area = taxonomy.QNameMaker.fromString("dtr-types:areaItemType")
        assert config.dataTypesToUnits[area] == taxonomy.QNameMaker.fromString(
            "utr:ha"
        )

    def test_concepts_to_units_keyed_by_concept(self, config, taxonomy):
        concept = taxonomy.getConcept("vsme:TotalWasteGeneratedMass")
        assert config.conceptsToUnits[concept] == taxonomy.QNameMaker.fromString(
            "utr:t"
        )

    def test_unit_ids_to_measures_builds_complex_units(self, config):
        cu = config.unitIdsToMeasures["Emissions_per_Monetary"]
        assert isinstance(cu, ComplexUnit)
        assert [str(q) for q in cu.numerator] == ["utr:tCO2e"]
        assert [str(q) for q in cu.denominator] == ["iso4217:EUR"]

    def test_cell_values_to_taxonomy_labels_passthrough(self, config):
        assert (
            config.cellValuesToTaxonomyLabels["Option A (Basic Module only)"]
            == "Basic Module (only) [member]"
        )

    def test_cell_unit_replacements_passthrough(self, config):
        assert config.cellUnitReplacements["m2"] == "sqm"

    def test_empty_defaults_gives_empty_config(self, taxonomy):
        cfg = ConverterConfig.fromDefaults({}, taxonomy)
        assert cfg.dataTypesToUnits == {}
        assert cfg.unitIdsToMeasures == {}
        assert cfg.conceptsToUnits == {}
        assert cfg.cellValuesToTaxonomyLabels == {}
        assert cfg.cellUnitReplacements == {}

    def test_frozen(self, config):
        with pytest.raises(AttributeError):
            config.cellUnitReplacements = {}
