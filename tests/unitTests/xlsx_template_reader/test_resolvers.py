"""Tests for the resolver layer: per-table XBRLTableResolver, one-shot resolver
functions, and the VSME taxonomy name aliases."""

from pathlib import Path

import pytest

import mireport.xlsx_template_reader._resolvers as resolvers_module
from mireport.conversionresults import ConversionResultsBuilder
from mireport.data.disclosures import VSME_DEFAULTS
from mireport.taxonomy import getTaxonomy
from mireport.xlsx_template_reader._binder import WorkbookBinder
from mireport.xlsx_template_reader._bindings import TableBinding
from mireport.xlsx_template_reader._constants import TAXONOMY_NAME_ALIASES
from mireport.xlsx_template_reader._messages import Messenger
from mireport.xlsx_template_reader._reader import WorkbookReader
from mireport.xlsx_template_reader._resolvers import (
    ExcelCellBindingContext,
    XBRLTableResolver,
    resolveExternalValues,
    resolveFootnoteBinding,
    resolveNamedRangeTable,
)
from mireport.xlsx_template_reader.util import loadExcelFromPathOrFileLike

SAMPLE = (
    Path(__file__).parent.parent.parent
    / "data"
    / "VSME-Digital-Template-Sample-1.2.0.xlsx"
)

ALIASED_NAME = "IdentifierOfSitesInBiodiversitySensitiveAreasTypedAxis"
ALIAS_TARGET = "IdentifierOfSiteTypedAxis"


def _results() -> ConversionResultsBuilder:
    return ConversionResultsBuilder(consoleOutput=False)


@pytest.fixture(scope="module")
def reader():
    wb = loadExcelFromPathOrFileLike(SAMPLE)
    yield WorkbookReader(wb, _results())
    wb.close()


@pytest.fixture(scope="module")
def taxonomy(reader):
    return getTaxonomy(reader.value(VSME_DEFAULTS["entryPoint"]).asString())


@pytest.fixture(scope="module")
def ctx(reader, taxonomy):
    return ExcelCellBindingContext(reader, Messenger(_results()), taxonomy)


@pytest.fixture(scope="module")
def bound(taxonomy):
    wb = loadExcelFromPathOrFileLike(SAMPLE)
    reader = WorkbookReader(wb, _results())
    try:
        yield WorkbookBinder(reader, taxonomy, _results()).bind()
    finally:
        wb.close()


class TestResolverModuleShape:
    def test_binding_resolver_abc_removed(self):
        assert not hasattr(resolvers_module, "BindingResolver")

    def test_one_shot_resolvers_are_functions(self):
        assert callable(resolveExternalValues)
        assert callable(resolveFootnoteBinding)
        assert callable(resolveNamedRangeTable)


class TestXbrlTableResolverPerTable:
    def test_one_instance_resolves_multiple_tables(self, ctx, bound):
        resolver = XBRLTableResolver(
            ctx, unit_map={}, candidates_by_ws={}, concepts_in_excel=frozenset()
        )
        assert len(bound.tables) >= 2, "sample should have several hypercube tables"
        for table_binding in bound.tables[:3]:
            out = resolver.resolve(table_binding.table)
            assert isinstance(out, TableBinding)
            assert out.table is table_binding.table
            # No candidate ranges supplied, so nothing to classify.
            assert out.primaryItems == []
            assert out.explicitDimensions == []
            assert out.typedDimensions == []
            assert out.units == []


class TestOneShotResolvers:
    def test_external_values_returns_frozenset(self, ctx):
        assert isinstance(resolveExternalValues(ctx), frozenset)

    def test_footnotes_none_when_unconfigured(self, ctx):
        # The 1.2.0 sample has no footnote named ranges: silently nothing.
        assert resolveFootnoteBinding(ctx) is None

    def test_named_range_table_none_when_unconfigured(self, ctx):
        assert (
            resolveNamedRangeTable(
                ctx,
                label="Bogus",
                container_name="bogus_container",
                required_sub_names=("bogus_a", "bogus_b"),
                context="Testing.",
            )
            is None
        )


def _external_values_ctx(cells, taxonomy):
    """Build a tiny in-memory workbook whose template_external_values range
    holds the given cell values (one per row), plus its resolver context."""
    from openpyxl import Workbook
    from openpyxl.workbook.defined_name import DefinedName

    wb = Workbook()
    ws = wb.active
    for i, v in enumerate(cells, start=1):
        ws.cell(row=i, column=1).value = v
    wb.defined_names.add(
        DefinedName(
            "template_external_values",
            attr_text=f"'{ws.title}'!$A$1:$A${max(1, len(cells))}",
        )
    )
    results = _results()
    reader = WorkbookReader(wb, results)
    return ExcelCellBindingContext(reader, Messenger(results), taxonomy), results


def _warnings(results):
    from mireport.conversionresults import Severity

    return [m for m in results.messages if m.severity is Severity.WARNING]


class TestResolveExternalValues:
    @pytest.fixture(scope="class")
    def textblock(self, taxonomy):
        return next(
            c for c in sorted(taxonomy.concepts, key=str) if c.isTextblock
        )

    def test_known_concept_name_resolved(self, taxonomy, textblock):
        ctx, results = _external_values_ctx(
            [textblock.qname.localName], taxonomy
        )
        assert resolveExternalValues(ctx) == frozenset({textblock})
        assert not _warnings(results)

    def test_blank_and_placeholder_cells_skipped_silently(self, taxonomy):
        ctx, results = _external_values_ctx([None, "-", "   ", "#VALUE!"], taxonomy)
        assert resolveExternalValues(ctx) == frozenset()
        assert not _warnings(results)

    def test_non_string_cells_warn_and_are_excluded(self, taxonomy):
        # Numbers/booleans can't name a concept: they now stringify, fail the
        # lookup and warn (previously they were skipped silently).
        ctx, results = _external_values_ctx([42, 3.14, True], taxonomy)
        assert resolveExternalValues(ctx) == frozenset()
        assert len(_warnings(results)) == 3

    def test_unknown_name_warns_and_is_excluded(self, taxonomy):
        ctx, results = _external_values_ctx(["NoSuchConceptXYZ"], taxonomy)
        assert resolveExternalValues(ctx) == frozenset()
        assert len(_warnings(results)) == 1

    def test_ambiguous_name_warns_and_is_excluded(self):
        from mireport.exceptions import AmbiguousComponentException

        class AmbiguousTaxonomy:
            def resolveConcept(self, text, **kwargs):
                raise AmbiguousComponentException(f"'{text}' is ambiguous")

        ctx, results = _external_values_ctx(["SharedLabel"], AmbiguousTaxonomy())
        assert resolveExternalValues(ctx) == frozenset()
        assert len(_warnings(results)) == 1


class TestTaxonomyNameAliases:
    def test_known_vsme_alias_registered(self):
        assert TAXONOMY_NAME_ALIASES[ALIASED_NAME] == ALIAS_TARGET

    def test_bind_applies_alias(self, bound):
        """The aliased defined name must bind to the alias-target concept,
        whether it ended up in the concept_map or inside a table binding."""
        all_ranges = list(bound.concept_map.values())
        for table_binding in bound.tables:
            all_ranges.extend(table_binding.conceptRanges)
        aliased = [crm for crm in all_ranges if crm.definedName.name == ALIASED_NAME]
        assert aliased, f"sample should contain the {ALIASED_NAME} named range"
        for crm in aliased:
            assert crm.concept.qname.localName == ALIAS_TARGET
