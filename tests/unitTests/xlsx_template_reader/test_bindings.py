import dataclasses
from collections import defaultdict

from mireport.xlsx_template_reader._bindings import (
    CellAndXBRLMetadataHolder,
    CellRangeMetadata,
    ComplexUnit,
    TableXBRLContents,
    WorkbookBindings,
)


class TestBindingsModuleExports:
    def test_required_names_importable(self):
        assert all(
            [
                ComplexUnit,
                CellRangeMetadata,
                CellAndXBRLMetadataHolder,
                TableXBRLContents,
                WorkbookBindings,
            ]
        )


class TestWorkbookBindingsShape:
    def test_has_expected_fields(self):
        field_names = {f.name for f in dataclasses.fields(WorkbookBindings)}
        assert {
            "concept_map",
            "table_map",
            "unit_map",
            "preset_dims",
            "unused",
        } <= field_names

    def test_can_instantiate_empty(self):
        wb = WorkbookBindings(
            concept_map={},
            table_map={},
            unit_map={},
            preset_dims=defaultdict(dict),
            unused=set(),
        )
        assert wb.concept_map == {}


class TestCellAndXBRLMetadataHolderCompat:
    def test_from_cell_range_metadata_exists(self):
        assert callable(CellAndXBRLMetadataHolder.fromCellRangeMetadata)
