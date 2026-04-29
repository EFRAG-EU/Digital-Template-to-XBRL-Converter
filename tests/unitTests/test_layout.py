from __future__ import annotations

import logging
from datetime import date
from unittest.mock import MagicMock

from mireport.report.fact import Fact
from mireport.report.layout import (
    ReportLayoutOrganiser,
    ReportSection,
    TableHeadingCell,
    TableStyle,
    _column_periods,
    _column_units,
    _table_period,
    _table_unit,
)
from mireport.report.periods import DurationPeriodHolder, InstantPeriodHolder
from mireport.taxonomy import (
    Concept,
    PresentationGroup,
    PresentationStyle,
    Relationship,
    Taxonomy,
)

# ── Test doubles ─────────────────────────────────────────────────────────────


def _fact(
    *, numeric=False, unit=None, period=None, concept=None, aspects=None, value="x"
):
    f = MagicMock(spec=Fact)
    f.concept = concept or MagicMock(spec=Concept)
    f.concept.isNumeric = numeric
    f.unitSymbol = unit
    f.period = period
    f.aspects = aspects or {}
    f.value = value
    f.hasTaxonomyDimensions.return_value = bool(aspects)
    return f


def _organiser(facts_by_concept=None, presentation_groups=None):
    taxonomy = MagicMock(spec=Taxonomy)
    taxonomy.presentation = presentation_groups or []
    report = MagicMock()
    report.taxonomy = taxonomy
    facts_map = facts_by_concept or {}
    report.getFacts.side_effect = lambda c: facts_map.get(c, [])
    all_facts = [f for facts in facts_map.values() for f in facts]
    report.facts = all_facts
    return ReportLayoutOrganiser(taxonomy, report)


def _section(definition: str, style=PresentationStyle.List):
    pres = MagicMock()
    pres.definition = definition
    pres.style = style
    pres.roleUri = definition
    pres.relationships = []
    return ReportSection(relationshipToFact={}, presentation=pres)


_DUR = DurationPeriodHolder(start=date(2024, 1, 1), end=date(2024, 12, 31))
_INST = InstantPeriodHolder(instant=date(2024, 12, 31))

# ── TableHeadingCell ──────────────────────────────────────────────────────────


class TestTableHeadingCell:
    def test_duration_period(self):
        cell = TableHeadingCell(_DUR)
        assert cell.isDuration
        assert not cell.isInstant
        assert cell.isPeriod
        assert not cell.isConcept
        assert not cell.isRelationship

    def test_instant_period(self):
        cell = TableHeadingCell(_INST)
        assert not cell.isDuration
        assert cell.isInstant
        assert cell.isPeriod
        assert not cell.isConcept
        assert not cell.isRelationship

    def test_concept_value(self):
        concept = MagicMock(spec=Concept)
        cell = TableHeadingCell(concept)
        assert cell.isConcept
        assert not cell.isPeriod
        assert not cell.isRelationship

    def test_relationship_value(self):
        rel = MagicMock(spec=Relationship)
        cell = TableHeadingCell(rel)
        assert cell.isRelationship
        assert not cell.isConcept
        assert not cell.isPeriod

    def test_string_value(self):
        cell = TableHeadingCell("some label")
        assert not cell.isDuration
        assert not cell.isInstant
        assert not cell.isConcept
        assert not cell.isRelationship

    def test_none_value(self):
        cell = TableHeadingCell(None)
        assert not cell.isPeriod
        assert not cell.isConcept


# ── _table_unit ───────────────────────────────────────────────────────────────


class TestGetTableUnit:
    def test_empty_data(self):
        assert _table_unit([]) is None

    def test_all_none(self):
        assert _table_unit([[None, None]]) is None

    def test_single_numeric_fact(self):
        f = _fact(numeric=True, unit="EUR")
        assert _table_unit([[f]]) == "EUR"

    def test_two_facts_same_unit(self):
        f1 = _fact(numeric=True, unit="EUR")
        f2 = _fact(numeric=True, unit="EUR")
        assert _table_unit([[f1], [f2]]) == "EUR"

    def test_two_facts_different_units(self):
        f1 = _fact(numeric=True, unit="EUR")
        f2 = _fact(numeric=True, unit="USD")
        assert _table_unit([[f1, f2]]) is None

    def test_empty_string_unit_returns_none(self):
        f = _fact(numeric=True, unit="")
        assert _table_unit([[f]]) is None

    def test_non_numeric_facts_ignored(self):
        f = _fact(numeric=False, unit=None)
        assert _table_unit([[f]]) is None

    def test_mix_numeric_and_non_numeric(self):
        f_num = _fact(numeric=True, unit="EUR")
        f_text = _fact(numeric=False, unit=None)
        assert _table_unit([[f_num, f_text]]) == "EUR"


# ── _table_period ─────────────────────────────────────────────────────────────


class TestGetTablePeriod:
    def test_empty_data(self):
        assert _table_period([]) is None

    def test_all_none(self):
        assert _table_period([[None]]) is None

    def test_single_period(self):
        f = _fact(period=_DUR)
        assert _table_period([[f]]) == _DUR

    def test_two_facts_same_period(self):
        f1 = _fact(period=_DUR)
        f2 = _fact(period=_DUR)
        assert _table_period([[f1], [f2]]) == _DUR

    def test_two_facts_different_periods(self):
        f1 = _fact(period=_DUR)
        f2 = _fact(period=_INST)
        assert _table_period([[f1, f2]]) is None


# ── _column_units ─────────────────────────────────────────────────────────────


class TestGetColumnUnits:
    def test_empty_data(self):
        assert _column_units([]) == []

    def test_single_column_with_unit(self):
        f = _fact(numeric=True, unit="EUR")
        assert _column_units([[f]]) == ["EUR"]

    def test_all_none_returns_empty_list(self):
        assert _column_units([[None]]) == []

    def test_mixed_units_in_column_returns_empty_list(self):
        # mixed units → column is None → all-None short-circuit → []
        f1 = _fact(numeric=True, unit="EUR")
        f2 = _fact(numeric=True, unit="USD")
        assert _column_units([[f1], [f2]]) == []

    def test_partial_none_columns_preserved(self):
        # first column has a unit, second has no numeric facts → [unit, None]
        f_eur = _fact(numeric=True, unit="EUR")
        f_text = _fact(numeric=False)
        assert _column_units([[f_eur, f_text]]) == ["EUR", None]

    def test_two_columns_different_units(self):
        f_eur = _fact(numeric=True, unit="EUR")
        f_usd = _fact(numeric=True, unit="USD")
        result = _column_units([[f_eur, f_usd]])
        assert result == ["EUR", "USD"]

    def test_empty_string_unit_treated_as_none(self):
        f = _fact(numeric=True, unit="")
        assert _column_units([[f]]) == []

    def test_non_numeric_column_gives_none(self):
        f = _fact(numeric=False, unit=None)
        result = _column_units([[f]])
        assert result == []


# ── _column_periods ───────────────────────────────────────────────────────────


class TestGetColumnPeriods:
    def test_empty_data(self):
        assert _column_periods([]) == []

    def test_single_column_with_period(self):
        f = _fact(period=_DUR)
        assert _column_periods([[f]]) == [_DUR]

    def test_all_none_returns_empty_list(self):
        assert _column_periods([[None]]) == []

    def test_mixed_periods_in_column_returns_empty_list(self):
        # mixed periods → column is None → all-None short-circuit → []
        f1 = _fact(period=_DUR)
        f2 = _fact(period=_INST)
        assert _column_periods([[f1], [f2]]) == []

    def test_two_columns_different_periods(self):
        f_dur = _fact(period=_DUR)
        f_inst = _fact(period=_INST)
        result = _column_periods([[f_dur, f_inst]])
        assert result == [_DUR, _INST]

    def test_partial_none_columns_preserved(self):
        # second column has no facts → [_DUR, None]
        f_dur = _fact(period=_DUR)
        f_none: Fact | None = None
        result = _column_periods([[f_dur, f_none]])
        assert result == [_DUR, None]


# ── _sectionPrefix ────────────────────────────────────────────────────────────


class TestSectionPrefix:
    def test_extracts_prefix_before_dot(self):
        s = _section("[B02.Group Name")
        assert ReportLayoutOrganiser._sectionPrefix(s) == "[B02"

    def test_multiple_dots_only_first_split(self):
        s = _section("[C02.foo.bar.baz")
        assert ReportLayoutOrganiser._sectionPrefix(s) == "[C02"

    def test_no_dot_returns_whole_string(self):
        s = _section("[B02]")
        assert ReportLayoutOrganiser._sectionPrefix(s) == "[B02]"

    def test_empty_definition(self):
        s = _section("")
        assert ReportLayoutOrganiser._sectionPrefix(s) == ""


# ── _move_sections_after ──────────────────────────────────────────────────────


class TestMoveSectionsAfter:
    def test_source_not_present_leaves_sections_unchanged(self):
        o = _organiser()
        sections = [_section("[A01.x"), _section("[B02.x")]
        o.reportSections = sections[:]
        o._move_sections_after("[C02", "[B02")
        assert o.reportSections == sections

    def test_target_not_present_leaves_sections_unchanged(self):
        o = _organiser()
        sections = [_section("[A01.x"), _section("[C02.x")]
        o.reportSections = sections[:]
        o._move_sections_after("[C02", "[B02")
        assert o.reportSections == sections

    def test_moves_source_after_last_target(self):
        o = _organiser()
        a = _section("[A01.x")
        b = _section("[B02.x")
        c = _section("[C02.x")
        d = _section("[D03.x")
        o.reportSections = [a, c, b, d]
        o._move_sections_after("[C02", "[B02")
        assert o.reportSections == [a, b, c, d]

    def test_multiple_source_sections_all_move_together(self):
        o = _organiser()
        a = _section("[A01.x")
        b = _section("[B02.x")
        c1 = _section("[C02.x1")
        c2 = _section("[C02.x2")
        d = _section("[D03.x")
        o.reportSections = [a, c1, c2, b, d]
        o._move_sections_after("[C02", "[B02")
        assert o.reportSections == [a, b, c1, c2, d]

    def test_source_already_after_target_stays_in_place(self):
        o = _organiser()
        a = _section("[A01.x")
        b = _section("[B02.x")
        c = _section("[C02.x")
        o.reportSections = [a, b, c]
        o._move_sections_after("[C02", "[B02")
        assert o.reportSections == [a, b, c]


# ── checkAllFactsUsed ─────────────────────────────────────────────────────────


class TestCheckAllFactsUsed:
    def test_all_facts_in_sections_no_warning(self, caplog):
        fact = _fact()
        rel = MagicMock()
        pres = MagicMock()
        pres.style = PresentationStyle.List
        section = ReportSection(relationshipToFact={rel: [fact]}, presentation=pres)
        o = _organiser(facts_by_concept={fact.concept: [fact]})
        o.reportSections = [section]
        with caplog.at_level(logging.WARNING, logger="mireport.report.layout"):
            o.checkAllFactsUsed()
        assert not caplog.records

    def test_unused_fact_without_inconsistent_duplicate_does_not_raise(self):
        unused = _fact(value="v1")
        o = _organiser(facts_by_concept={unused.concept: [unused]})
        o.reportSections = []
        o.report.getFacts.return_value = [unused]
        o.checkAllFactsUsed()  # must not raise

    def test_unused_fact_with_inconsistent_duplicate_logs_warning(self, caplog):
        concept = MagicMock(spec=Concept)
        aspects_dict = {"period": "2024"}
        unused = _fact(value="v1", concept=concept, aspects=aspects_dict)
        duplicate = _fact(value="v2", concept=concept, aspects=aspects_dict)
        o = _organiser(facts_by_concept={concept: [unused, duplicate]})
        o.reportSections = []
        o.report.getFacts.return_value = [unused, duplicate]
        with caplog.at_level(logging.WARNING, logger="mireport.report.layout"):
            o.checkAllFactsUsed()
        assert any("inconsistent" in r.message.lower() for r in caplog.records)


# ── createReportSections ──────────────────────────────────────────────────────


class TestCreateReportSections:
    def _make_group(self, style, concept, facts):
        rel = MagicMock(spec=Relationship)
        rel.concept = concept
        group = MagicMock(spec=PresentationGroup)
        group.style = style
        group.roleUri = f"role-{style.name}"
        group.definition = f"[X01.{style.name}"
        group.relationships = [rel]
        return group, rel, facts

    def test_empty_style_produces_empty_section(self):
        group = MagicMock()
        group.style = PresentationStyle.Empty
        o = _organiser(presentation_groups=[group])
        o.createReportSections()
        assert len(o.reportSections) == 1
        assert o.reportSections[0].relationshipToFact == {}

    def test_list_style_excludes_dimensional_facts(self):
        concept = MagicMock(spec=Concept)
        plain = _fact(concept=concept)
        plain.hasTaxonomyDimensions.return_value = False
        dimensional = _fact(concept=concept)
        dimensional.hasTaxonomyDimensions.return_value = True

        rel = MagicMock(spec=Relationship)
        rel.concept = concept
        group = MagicMock()
        group.style = PresentationStyle.List
        group.relationships = [rel]

        o = _organiser(
            facts_by_concept={concept: [plain, dimensional]},
            presentation_groups=[group],
        )
        o.createReportSections()
        assert len(o.reportSections) == 1
        included = o.reportSections[0].relationshipToFact[rel]
        assert plain in included
        assert dimensional not in included

    def test_table_style_includes_all_facts(self):
        concept = MagicMock(spec=Concept)
        plain = _fact(concept=concept)
        plain.hasTaxonomyDimensions.return_value = False
        dimensional = _fact(concept=concept)
        dimensional.hasTaxonomyDimensions.return_value = True

        rel = MagicMock(spec=Relationship)
        rel.concept = concept
        group = MagicMock()
        group.style = PresentationStyle.Table
        group.relationships = [rel]

        o = _organiser(
            facts_by_concept={concept: [plain, dimensional]},
            presentation_groups=[group],
        )
        o.createReportSections()
        included = o.reportSections[0].relationshipToFact[rel]
        assert plain in included
        assert dimensional in included


# ── _assemble_explicit_dim_as_columns ─────────────────────────────────────────


class TestAssembleDimsAsColumnTable:
    def _setup(self):
        dim_qname = object()  # use a sentinel as the dimension qname key

        explicit_dim = MagicMock(spec=Concept)
        explicit_dim.qname = dim_qname

        member_a = MagicMock(spec=Concept)
        member_a.qname = "qname:member_a"
        member_b = MagicMock(spec=Concept)
        member_b.qname = "qname:member_b"
        domain = [member_a, member_b]

        concept_x = MagicMock(spec=Concept)
        concept_y = MagicMock(spec=Concept)
        reportable = [concept_x, concept_y]

        fact_xa = _fact(aspects={dim_qname: "qname:member_a"})
        fact_xb = _fact(aspects={dim_qname: "qname:member_b"})
        fact_ya = _fact(aspects={dim_qname: "qname:member_a"})
        fact_yb = _fact(aspects={dim_qname: "qname:member_b"})

        facts_map = {
            concept_x: [fact_xa, fact_xb],
            concept_y: [fact_ya, fact_yb],
        }

        return (
            explicit_dim,
            domain,
            reportable,
            [fact_xa, fact_xb, fact_ya, fact_yb],
            facts_map,
        )

    def test_returns_correct_table_style(self):
        explicit_dim, domain, reportable, _, facts_map = self._setup()
        o = _organiser(facts_by_concept=facts_map)
        matrix = o._assemble_explicit_dim_as_columns(
            "[B01.test", reportable, explicit_dim, domain, None
        )
        assert matrix.style == TableStyle.SingleExplicitDimensionColumn

    def test_col_labels(self):
        explicit_dim, domain, reportable, _, facts_map = self._setup()
        o = _organiser(facts_by_concept=facts_map)
        matrix = o._assemble_explicit_dim_as_columns(
            "[B01.test", reportable, explicit_dim, domain, None
        )
        assert matrix.col_labels == domain

    def test_row_heading_label_is_none(self):
        explicit_dim, domain, reportable, _, facts_map = self._setup()
        o = _organiser(facts_by_concept=facts_map)
        matrix = o._assemble_explicit_dim_as_columns(
            "[B01.test", reportable, explicit_dim, domain, None
        )
        assert matrix.row_heading_label is None

    def test_row_labels_are_reportable_concepts(self):
        explicit_dim, domain, reportable, _, facts_map = self._setup()
        o = _organiser(facts_by_concept=facts_map)
        matrix = o._assemble_explicit_dim_as_columns(
            "[B01.test", reportable, explicit_dim, domain, None
        )
        assert matrix.row_labels == reportable

    def test_data_matrix_shape(self):
        explicit_dim, domain, reportable, _, facts_map = self._setup()
        o = _organiser(facts_by_concept=facts_map)
        matrix = o._assemble_explicit_dim_as_columns(
            "[B01.test", reportable, explicit_dim, domain, None
        )
        assert len(matrix.data) == 2
        assert len(matrix.data[0]) == len(domain)

    def test_empty_rows_excluded(self):
        explicit_dim, domain, reportable, _, facts_map = self._setup()
        # concept_x has no facts → its row should be dropped
        concept_x = reportable[0]
        facts_map = {concept_x: [], reportable[1]: facts_map[reportable[1]]}
        o = _organiser(facts_by_concept=facts_map)
        matrix = o._assemble_explicit_dim_as_columns(
            "[B01.test", reportable, explicit_dim, domain, None
        )
        assert len(matrix.data) == 1
        assert matrix.row_labels == [reportable[1]]


# ── _assemble_explicit_dim_as_rows ────────────────────────────────────────────


class TestAssembleDimsAsRowsTable:
    def _setup(self):
        dim_qname = object()

        explicit_dim = MagicMock(spec=Concept)
        explicit_dim.qname = dim_qname

        member_a = MagicMock(spec=Concept)
        member_a.qname = "qname:member_a"
        member_b = MagicMock(spec=Concept)
        member_b.qname = "qname:member_b"
        domain = [member_a, member_b]

        concept_x = MagicMock(spec=Concept)
        concept_y = MagicMock(spec=Concept)
        reportable = [concept_x, concept_y]

        fact_xa = _fact(aspects={dim_qname: "qname:member_a"})
        fact_ya = _fact(aspects={dim_qname: "qname:member_a"})

        facts_map = {
            concept_x: [fact_xa],
            concept_y: [fact_ya],
        }

        return explicit_dim, domain, reportable, facts_map, member_a, member_b

    def test_returns_correct_table_style(self):
        explicit_dim, domain, reportable, facts_map, _, __ = self._setup()
        o = _organiser(facts_by_concept=facts_map)
        matrix = o._assemble_explicit_dim_as_rows(
            "[B01.test", reportable, explicit_dim, domain, None
        )
        assert matrix.style == TableStyle.SingleExplicitDimensionRow

    def test_col_labels_are_reportable(self):
        explicit_dim, domain, reportable, facts_map, _, __ = self._setup()
        o = _organiser(facts_by_concept=facts_map)
        matrix = o._assemble_explicit_dim_as_rows(
            "[B01.test", reportable, explicit_dim, domain, None
        )
        assert matrix.col_labels == reportable

    def test_row_heading_label_is_explicit_dim(self):
        explicit_dim, domain, reportable, facts_map, _, __ = self._setup()
        o = _organiser(facts_by_concept=facts_map)
        matrix = o._assemble_explicit_dim_as_rows(
            "[B01.test", reportable, explicit_dim, domain, None
        )
        assert matrix.row_heading_label is explicit_dim

    def test_row_labels_are_domain_members(self):
        explicit_dim, domain, reportable, facts_map, member_a, _ = self._setup()
        o = _organiser(facts_by_concept=facts_map)
        matrix = o._assemble_explicit_dim_as_rows(
            "[B01.test", reportable, explicit_dim, domain, None
        )
        assert member_a in matrix.row_labels

    def test_empty_rows_excluded(self):
        explicit_dim, domain, reportable, _, member_a, member_b = self._setup()
        # member_b has no facts
        concept_x = reportable[0]
        concept_y = reportable[1]
        fact_xa = _fact(aspects={explicit_dim.qname: "qname:member_a"})
        facts_map = {concept_x: [fact_xa], concept_y: []}
        o = _organiser(facts_by_concept=facts_map)
        matrix = o._assemble_explicit_dim_as_rows(
            "[B01.test", reportable, explicit_dim, domain, None
        )
        assert member_b not in matrix.row_labels


# ── _assemble_typed_dim ───────────────────────────────────────────────────────


class TestAssembleTypedDimTable:
    def _setup(self):
        typed_dim = MagicMock(spec=Concept)
        typed_dim.qname = "esrs:typedDim"
        typed_qname = f"typed {typed_dim.qname}"

        concept_x = MagicMock(spec=Concept)
        concept_y = MagicMock(spec=Concept)
        reportable = [concept_x, concept_y]

        val_2 = "<value>2</value>"
        val_10 = "<value>10</value>"

        fact_x2 = _fact(aspects={typed_qname: val_2}, concept=concept_x)
        fact_x10 = _fact(aspects={typed_qname: val_10}, concept=concept_x)
        fact_y2 = _fact(aspects={typed_qname: val_2}, concept=concept_y)
        fact_y10 = _fact(aspects={typed_qname: val_10}, concept=concept_y)

        facts_map = {
            concept_x: [fact_x2, fact_x10],
            concept_y: [fact_y2, fact_y10],
        }
        return typed_dim, reportable, facts_map, val_2, val_10

    def test_returns_correct_table_style(self):
        typed_dim, reportable, facts_map, _, __ = self._setup()
        o = _organiser(facts_by_concept=facts_map)
        matrix = o._assemble_typed_dim("[B01.test", [typed_dim], reportable)
        assert matrix.style == TableStyle.SingleTypedDimensionColumn

    def test_col_labels_are_reportable(self):
        typed_dim, reportable, facts_map, _, __ = self._setup()
        o = _organiser(facts_by_concept=facts_map)
        matrix = o._assemble_typed_dim("[B01.test", [typed_dim], reportable)
        assert matrix.col_labels == reportable

    def test_row_heading_label_is_typed_dim(self):
        typed_dim, reportable, facts_map, _, __ = self._setup()
        o = _organiser(facts_by_concept=facts_map)
        matrix = o._assemble_typed_dim("[B01.test", [typed_dim], reportable)
        assert matrix.row_heading_label is typed_dim

    def test_rows_sorted_numerically(self):
        typed_dim, reportable, facts_map, val_2, val_10 = self._setup()
        o = _organiser(facts_by_concept=facts_map)
        matrix = o._assemble_typed_dim("[B01.test", [typed_dim], reportable)
        assert len(matrix.data) == 2
        assert matrix.row_labels[0] == "2"  # tidyTdValue extracts the inner text
        assert matrix.row_labels[1] == "10"

    def test_empty_rows_excluded(self):
        typed_dim, reportable, facts_map, val_2, val_10 = self._setup()
        concept_x, concept_y = reportable
        typed_qname = f"typed {typed_dim.qname}"
        fact_x2 = _fact(aspects={typed_qname: val_2}, concept=concept_x)
        # No val_10 facts at all
        facts_map = {concept_x: [fact_x2], concept_y: []}
        o = _organiser(facts_by_concept=facts_map)
        matrix = o._assemble_typed_dim("[B01.test", [typed_dim], reportable)
        assert len(matrix.data) == 1
        assert matrix.row_labels == ["2"]
