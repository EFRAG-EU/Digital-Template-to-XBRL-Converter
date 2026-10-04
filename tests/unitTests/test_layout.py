from __future__ import annotations

import logging
from datetime import date
from unittest.mock import MagicMock

from mireport.report.disclosure_layout import (
    OldVsmeLayoutStrategy,
    _move_sections_after,
    _old_vsme_section_code,
)
from mireport.report.fact import Fact
from mireport.report.layout import (
    ReportLayoutOrganiser,
    ReportSection,
    TableHeadingCell,
    TableStyle,
)
from mireport.report.layout.grid import GridBuilder
from mireport.report.layout.headers import (
    column_periods,
    column_units,
    table_period,
    table_unit,
)
from mireport.report.model import FactPeriod, PeriodPart, ReportPeriod
from mireport.report.periods import DurationPeriodHolder
from mireport.taxonomy import (
    Concept,
    PresentationGroup,
    PresentationStyle,
    Relationship,
    Taxonomy,
)


def _fact(
    *,
    numeric=False,
    unit=None,
    period=None,
    concept=None,
    explicit=None,
    typed=None,
    context=None,
    value="x",
):
    """A fact as layout sees it. `period` is a FactPeriod, `explicit`
    maps an explicit dimension to its member, `typed` a typed dimension to its text, and `context`
    stands in for everything that makes two facts duplicates of each other."""
    f = MagicMock(spec=Fact)
    f.concept = concept or MagicMock(spec=Concept)
    f.concept.isNumeric = numeric
    f.unitSymbol = unit
    f.period = period
    f.explicit_dimensions = dict(explicit or {})
    f.typed_dimensions = dict(typed or {})
    f.context_key = context
    f.value = value
    f.hasTaxonomyDimensions.return_value = bool(explicit or typed)
    return f


def _organiser(facts_by_concept=None, presentation_groups=None):
    taxonomy = MagicMock(spec=Taxonomy)
    taxonomy.presentation = presentation_groups or []
    report = MagicMock()
    report.taxonomy = taxonomy
    report.requireAllFactsRendered = False  # the real default
    facts_map = facts_by_concept or {}
    report.getFacts.side_effect = lambda c: facts_map.get(c, [])
    all_facts = [f for facts in facts_map.values() for f in facts]
    report.facts = all_facts
    return ReportLayoutOrganiser(taxonomy, report)


def _section(definition: str, style=PresentationStyle.List, label=None):
    pres = MagicMock()
    pres.definition = definition
    pres.style = style
    pres.roleUri = definition
    pres.relationships = []
    pres.getLabel.return_value = definition if label is None else label
    return ReportSection(relationshipToFact={}, presentation=pres)


_RP_2024 = ReportPeriod(
    "prior", DurationPeriodHolder.fromDays(date(2024, 1, 1), date(2024, 12, 31))
)
_RP_2025 = ReportPeriod(
    "cur", DurationPeriodHolder.fromDays(date(2025, 1, 1), date(2025, 12, 31))
)
_DUR = FactPeriod(_RP_2024)
_INST = FactPeriod(_RP_2025, PeriodPart.End)


class TestTableHeadingCell:
    def test_duration_period(self):
        cell = TableHeadingCell(_DUR)
        assert cell.isPeriod
        assert not cell.isConcept
        assert not cell.isRelationship

    def test_instant_period(self):
        cell = TableHeadingCell(_INST)
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
        assert not cell.isPeriod
        assert not cell.isConcept
        assert not cell.isRelationship

    def test_none_value(self):
        cell = TableHeadingCell(None)
        assert not cell.isPeriod
        assert not cell.isConcept


class TestGetTableUnit:
    def test_empty_data(self):
        assert table_unit([]) is None

    def test_all_none(self):
        assert table_unit([[None, None]]) is None

    def test_single_numeric_fact(self):
        f = _fact(numeric=True, unit="EUR")
        assert table_unit([[f]]) == "EUR"

    def test_two_facts_same_unit(self):
        f1 = _fact(numeric=True, unit="EUR")
        f2 = _fact(numeric=True, unit="EUR")
        assert table_unit([[f1], [f2]]) == "EUR"

    def test_two_facts_different_units(self):
        f1 = _fact(numeric=True, unit="EUR")
        f2 = _fact(numeric=True, unit="USD")
        assert table_unit([[f1, f2]]) is None

    def test_empty_string_unit_returns_none(self):
        f = _fact(numeric=True, unit="")
        assert table_unit([[f]]) is None

    def test_non_numeric_facts_ignored(self):
        f = _fact(numeric=False, unit=None)
        assert table_unit([[f]]) is None

    def test_mix_numeric_and_non_numeric(self):
        f_num = _fact(numeric=True, unit="EUR")
        f_text = _fact(numeric=False, unit=None)
        assert table_unit([[f_num, f_text]]) == "EUR"


class TestGetTablePeriod:
    def test_empty_data(self):
        assert table_period([]) is None

    def test_all_none(self):
        assert table_period([[None]]) is None

    def test_single_period(self):
        f = _fact(period=_DUR)
        assert table_period([[f]]) == _DUR

    def test_two_facts_same_period(self):
        f1 = _fact(period=_DUR)
        f2 = _fact(period=_DUR)
        assert table_period([[f1], [f2]]) == _DUR

    def test_two_facts_different_periods(self):
        f1 = _fact(period=_DUR)
        f2 = _fact(period=_INST)
        assert table_period([[f1, f2]]) is None


class TestGetColumnUnits:
    def test_empty_data(self):
        assert column_units([]) == []

    def test_single_column_with_unit(self):
        f = _fact(numeric=True, unit="EUR")
        assert column_units([[f]]) == ["EUR"]

    def test_all_none_returns_empty_list(self):
        assert column_units([[None]]) == []

    def test_mixed_units_in_column_returns_empty_list(self):
        # mixed units → column is None → all-None short-circuit → []
        f1 = _fact(numeric=True, unit="EUR")
        f2 = _fact(numeric=True, unit="USD")
        assert column_units([[f1], [f2]]) == []

    def test_partial_none_columns_preserved(self):
        # first column has a unit, second has no numeric facts → [unit, None]
        f_eur = _fact(numeric=True, unit="EUR")
        f_text = _fact(numeric=False)
        assert column_units([[f_eur, f_text]]) == ["EUR", None]

    def test_two_columns_different_units(self):
        f_eur = _fact(numeric=True, unit="EUR")
        f_usd = _fact(numeric=True, unit="USD")
        result = column_units([[f_eur, f_usd]])
        assert result == ["EUR", "USD"]

    def test_empty_string_unit_treated_as_none(self):
        f = _fact(numeric=True, unit="")
        assert column_units([[f]]) == []

    def test_non_numeric_column_gives_none(self):
        f = _fact(numeric=False, unit=None)
        result = column_units([[f]])
        assert result == []


class TestGetColumnPeriods:
    def test_empty_data(self):
        assert column_periods([]) == []

    def test_single_column_with_period(self):
        f = _fact(period=_DUR)
        assert column_periods([[f]]) == [_DUR]

    def test_all_none_returns_empty_list(self):
        assert column_periods([[None]]) == []

    def test_mixed_periods_in_column_returns_empty_list(self):
        # mixed periods → column is None → all-None short-circuit → []
        f1 = _fact(period=_DUR)
        f2 = _fact(period=_INST)
        assert column_periods([[f1], [f2]]) == []

    def test_two_columns_different_periods(self):
        f_dur = _fact(period=_DUR)
        f_inst = _fact(period=_INST)
        result = column_periods([[f_dur, f_inst]])
        assert result == [_DUR, _INST]

    def test_partial_none_columns_preserved(self):
        # second column has no facts → [_DUR, None]
        f_dur = _fact(period=_DUR)
        f_none: Fact | None = None
        result = column_periods([[f_dur, f_none]])
        assert result == [_DUR, None]


class TestOldVsmeSectionCode:
    def test_extracts_and_dezeropads(self):
        s = _section("[B02.Group Name")
        assert _old_vsme_section_code(s) == "B2"

    def test_multiple_dots_only_first_split(self):
        s = _section("[C02.foo.bar.baz")
        assert _old_vsme_section_code(s) == "C2"

    def test_full_old_vsme_definition(self):
        s = _section("[B07.000] - General information - Basis for Preparation")
        assert _old_vsme_section_code(s) == "B7"

    def test_multi_digit_not_zero_stripped(self):
        s = _section("[B10.x")
        assert _old_vsme_section_code(s) == "B10"

    def test_empty_definition(self, caplog):
        s = _section("")
        with caplog.at_level(
            logging.WARNING, logger="mireport.report.disclosure_layout"
        ):
            assert _old_vsme_section_code(s) == ""
        assert any("does not match" in r.message for r in caplog.records)

    def test_non_conforming_token_falls_back_and_warns(self, caplog):
        s = _section("[General.x")
        with caplog.at_level(
            logging.WARNING, logger="mireport.report.disclosure_layout"
        ):
            assert _old_vsme_section_code(s) == "General"
        assert any("does not match" in r.message for r in caplog.records)

    def test_conforming_code_does_not_warn(self, caplog):
        s = _section("[B07.000] - General information")
        with caplog.at_level(
            logging.WARNING, logger="mireport.report.disclosure_layout"
        ):
            assert _old_vsme_section_code(s) == "B7"
        assert not caplog.records


class TestOldVsmeSectionLabel:
    def test_replaces_prefix_with_code(self):
        s = _section("[C06.000] - General information - Basis for Preparation")
        assert (
            OldVsmeLayoutStrategy().section_label(s, "en")
            == "C6 - General information - Basis for Preparation"
        )

    def test_two_part_label(self):
        s = _section("[B01.000] - General information")
        assert (
            OldVsmeLayoutStrategy().section_label(s, "en") == "B1 - General information"
        )

    def test_label_differs_from_definition(self):
        # code comes from the definition; the descriptive text from the label
        s = _section(
            "[C06.000] - ignored",
            label="[C06.000] - Workforce - General characteristics",
        )
        assert (
            OldVsmeLayoutStrategy().section_label(s, "en")
            == "C6 - Workforce - General characteristics"
        )


class TestMoveSectionsAfter:
    def test_source_not_present_leaves_sections_unchanged(self):
        o = _organiser()
        sections = [_section("[A01.x"), _section("[B02.x")]
        o.reportSections = sections[:]
        o.reportSections = _move_sections_after(o.reportSections, "C2", "B2")
        assert o.reportSections == sections

    def test_target_not_present_leaves_sections_unchanged(self):
        o = _organiser()
        sections = [_section("[A01.x"), _section("[C02.x")]
        o.reportSections = sections[:]
        o.reportSections = _move_sections_after(o.reportSections, "C2", "B2")
        assert o.reportSections == sections

    def test_moves_source_after_target(self):
        o = _organiser()
        a = _section("[A01.x")
        b = _section("[B02.x")
        c = _section("[C02.x")
        d = _section("[D03.x")
        o.reportSections = [a, c, b, d]
        o.reportSections = _move_sections_after(o.reportSections, "C2", "B2")
        assert o.reportSections == [a, b, c, d]

    def test_multiple_source_sections_all_move_together(self):
        o = _organiser()
        a = _section("[A01.x")
        b = _section("[B02.x")
        c1 = _section("[C02.x1")
        c2 = _section("[C02.x2")
        d = _section("[D03.x")
        o.reportSections = [a, c1, c2, b, d]
        o.reportSections = _move_sections_after(o.reportSections, "C2", "B2")
        assert o.reportSections == [a, b, c1, c2, d]

    def test_inserts_after_last_section_of_target_group(self):
        # Target group (B2) has two rows; source must land after BOTH, not
        # wedged into the middle of the group.
        o = _organiser()
        a = _section("[A01.x")
        c = _section("[C02.x")
        b1 = _section("[B02.x1")
        b2 = _section("[B02.x2")
        d = _section("[D03.x")
        o.reportSections = [a, c, b1, b2, d]
        o.reportSections = _move_sections_after(o.reportSections, "C2", "B2")
        assert o.reportSections == [a, b1, b2, c, d]

    def test_source_already_after_target_stays_in_place(self):
        o = _organiser()
        a = _section("[A01.x")
        b = _section("[B02.x")
        c = _section("[C02.x")
        o.reportSections = [a, b, c]
        o.reportSections = _move_sections_after(o.reportSections, "C2", "B2")
        assert o.reportSections == [a, b, c]


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

    def test_unused_fact_with_inconsistent_duplicate_logs_debug(self, caplog):
        # User-facing surfacing now happens via
        # InlineReport.reportInconsistentDuplicateFacts; this is kept only as a
        # debug-level developer safety net.
        concept = MagicMock(spec=Concept)
        context = ("2024",)
        unused = _fact(value="v1", concept=concept, context=context)
        duplicate = _fact(value="v2", concept=concept, context=context)
        o = _organiser(facts_by_concept={concept: [unused, duplicate]})
        o.reportSections = []
        o.report.getFacts.return_value = [unused, duplicate]
        with caplog.at_level(logging.DEBUG, logger="mireport.report.layout"):
            o.checkAllFactsUsed()
        assert any("inconsistent" in r.message.lower() for r in caplog.records)


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


def _builder(facts_by_concept):
    for concept, facts in facts_by_concept.items():
        for fact in facts:
            fact.concept = concept  # a concept's facts are, by definition, its own
    return GridBuilder(lambda c: facts_by_concept.get(c, []), lambda c: c.name)


def _concept(name):
    concept = MagicMock(spec=Concept)
    concept.name = name
    concept.qname = name
    return concept


class TestExplicitDimensionAsColumns:
    """No more members than concepts: the members are the columns."""

    def _setup(self):
        dimension = _concept("dim")
        member_a, member_b = _concept("a"), _concept("b")
        domain = [member_a, member_b]
        x, y = _concept("x"), _concept("y")
        facts = {
            x: [
                _fact(explicit={dimension: member_a}),
                _fact(explicit={dimension: member_b}),
            ],
            y: [
                _fact(explicit={dimension: member_a}),
                _fact(explicit={dimension: member_b}),
            ],
        }
        return dimension, domain, [x, y], facts

    def test_returns_correct_table_style(self):
        dimension, domain, reportable, facts = self._setup()
        grid = _builder(facts).explicit_dimension(reportable, dimension, domain, None)
        assert grid.style == TableStyle.SingleExplicitDimensionColumn

    def test_col_labels(self):
        dimension, domain, reportable, facts = self._setup()
        grid = _builder(facts).explicit_dimension(reportable, dimension, domain, None)
        assert grid.col_labels == domain

    def test_row_heading_label_is_none(self):
        dimension, domain, reportable, facts = self._setup()
        grid = _builder(facts).explicit_dimension(reportable, dimension, domain, None)
        assert grid.row_heading_label is None

    def test_row_labels_are_reportable_concepts(self):
        dimension, domain, reportable, facts = self._setup()
        grid = _builder(facts).explicit_dimension(reportable, dimension, domain, None)
        assert grid.row_labels == reportable

    def test_data_matrix_shape(self):
        dimension, domain, reportable, facts = self._setup()
        grid = _builder(facts).explicit_dimension(reportable, dimension, domain, None)
        assert len(grid.data) == 2
        assert len(grid.data[0]) == len(domain)

    def test_each_fact_lands_under_its_member(self):
        dimension, domain, reportable, facts = self._setup()
        grid = _builder(facts).explicit_dimension(reportable, dimension, domain, None)
        x = reportable[0]
        assert grid.data[0] == facts[x]

    def test_empty_rows_excluded(self):
        dimension, domain, reportable, facts = self._setup()
        x, y = reportable
        grid = _builder({x: facts[x], y: []}).explicit_dimension(
            reportable, dimension, domain, None
        )
        assert grid.row_labels == [x]

    def test_a_fact_with_no_member_is_at_the_default(self):
        dimension, domain, reportable, _ = self._setup()
        x = reportable[0]
        plain = _fact()
        grid = _builder({x: [plain]}).explicit_dimension(
            reportable, dimension, domain, domain[0]
        )
        assert grid.data == [[plain, None]]

    def test_a_fact_with_no_member_and_no_default_is_left_out(self):
        dimension, domain, reportable, _ = self._setup()
        grid = _builder({reportable[0]: [_fact()]}).explicit_dimension(
            reportable, dimension, domain, None
        )
        assert grid.data == []

    def test_a_member_outside_the_domain_is_left_out(self):
        dimension, domain, reportable, _ = self._setup()
        stranger = _concept("elsewhere")
        grid = _builder(
            {reportable[0]: [_fact(explicit={dimension: stranger})]}
        ).explicit_dimension(reportable, dimension, domain, None)
        assert grid.data == []

    def test_two_facts_for_one_cell_show_the_first_and_say_so(self, caplog):
        dimension, domain, reportable, _ = self._setup()
        first = _fact(explicit={dimension: domain[0]}, value="first")
        second = _fact(explicit={dimension: domain[0]}, value="second")
        with caplog.at_level(logging.WARNING, logger="mireport.report.layout"):
            grid = _builder({reportable[0]: [first, second]}).explicit_dimension(
                reportable, dimension, domain, None
            )
        assert grid.data == [[first, None]]
        assert any("same table cell" in r.message for r in caplog.records)


class TestExplicitDimensionAsRows:
    """More members than concepts: the members are the rows."""

    def _setup(self):
        dimension = _concept("dim")
        a, b, c = _concept("a"), _concept("b"), _concept("c")
        x = _concept("x")
        facts = {x: [_fact(explicit={dimension: a}), _fact(explicit={dimension: b})]}
        return dimension, [a, b, c], [x], facts

    def test_returns_correct_table_style(self):
        dimension, domain, reportable, facts = self._setup()
        grid = _builder(facts).explicit_dimension(reportable, dimension, domain, None)
        assert grid.style == TableStyle.SingleExplicitDimensionRow

    def test_col_labels_are_the_reportable_concepts(self):
        dimension, domain, reportable, facts = self._setup()
        grid = _builder(facts).explicit_dimension(reportable, dimension, domain, None)
        assert grid.col_labels == reportable

    def test_row_heading_label_is_the_dimension(self):
        dimension, domain, reportable, facts = self._setup()
        grid = _builder(facts).explicit_dimension(reportable, dimension, domain, None)
        assert grid.row_heading_label is dimension

    def test_row_labels_are_the_members_that_have_facts(self):
        dimension, domain, reportable, facts = self._setup()
        a, b, _c = domain
        grid = _builder(facts).explicit_dimension(reportable, dimension, domain, None)
        assert grid.row_labels == [a, b]  # the third has no facts, so no row


class TestTypedDimension:
    def _setup(self):
        dimension = _concept("typed")
        x, y = _concept("x"), _concept("y")
        facts = {
            x: [_fact(typed={dimension: "2"}), _fact(typed={dimension: "10"})],
            y: [_fact(typed={dimension: "2"}), _fact(typed={dimension: "10"})],
        }
        return dimension, [x, y], facts

    def test_returns_correct_table_style(self):
        dimension, reportable, facts = self._setup()
        grid = _builder(facts).typed_dimension(reportable, dimension)
        assert grid.style == TableStyle.SingleTypedDimensionColumn

    def test_col_labels_are_reportable(self):
        dimension, reportable, facts = self._setup()
        assert (
            _builder(facts).typed_dimension(reportable, dimension).col_labels
            == reportable
        )

    def test_row_heading_label_is_typed_dim(self):
        dimension, reportable, facts = self._setup()
        grid = _builder(facts).typed_dimension(reportable, dimension)
        assert grid.row_heading_label is dimension

    def test_rows_sorted_numerically(self):
        dimension, reportable, facts = self._setup()
        grid = _builder(facts).typed_dimension(reportable, dimension)
        assert len(grid.data) == 2
        assert grid.row_labels == ["2", "10"]  # 2 before 10, not "10" before "2"

    def test_empty_rows_excluded(self):
        dimension, reportable, facts = self._setup()
        x, y = reportable
        only_two = {x: [facts[x][0]], y: []}
        grid = _builder(only_two).typed_dimension(reportable, dimension)
        assert len(grid.data) == 1
        assert grid.row_labels == ["2"]

    def test_a_fact_without_the_dimension_is_left_out_not_an_error(self):
        dimension, reportable, facts = self._setup()
        x, y = reportable
        grid = _builder({x: [_fact(), facts[x][0]], y: []}).typed_dimension(
            reportable, dimension
        )
        assert grid.row_labels == ["2"]


class TestDimensionSet:
    """Facts with a set of dimensions in common, with no hypercube to say how to lay them out."""

    @staticmethod
    def _rel(concept):
        return Relationship("role", 1, concept)

    def test_fewer_combinations_than_concepts_makes_them_the_columns(self):
        dimension = _concept("d")
        a = _concept("a")
        x, y = _concept("x"), _concept("y")
        items = [(self._rel(c), _fact(explicit={dimension: a})) for c in (x, y)]
        grid = _builder({}).dimension_set((dimension,), items)
        assert grid.row_labels == [x, y]
        assert grid.col_labels == ["a"]
        assert grid.row_heading_label is None

    def test_more_combinations_than_concepts_makes_them_rows(self):
        dimension = _concept("d")
        x = _concept("x")
        items = [(self._rel(x), _fact(typed={dimension: v})) for v in ("1", "2")]
        grid = _builder({}).dimension_set((dimension,), items)
        assert grid.row_labels == ["1", "2"]
        assert grid.col_labels == [x]
        assert grid.row_heading_label == "d"

    def test_combinations_of_several_dimensions_are_labelled_with_all_of_them(self):
        d1, d2 = _concept("d1"), _concept("d2")
        m = _concept("m")
        x, y = _concept("x"), _concept("y")
        items = [
            (self._rel(x), _fact(explicit={d1: m}, typed={d2: "7"})),
            (self._rel(y), _fact(explicit={d1: m}, typed={d2: "7"})),
        ]
        grid = _builder({}).dimension_set((d1, d2), items)
        assert grid.col_labels == ["m / 7"]

    def test_typed_values_are_ordered_numerically(self):
        dimension = _concept("d")
        x = _concept("x")
        items = [(self._rel(x), _fact(typed={dimension: v})) for v in ("10", "9", "2")]
        grid = _builder({}).dimension_set((dimension,), items)
        assert grid.row_labels == ["2", "9", "10"]


_CUR = FactPeriod(_RP_2025)
_PRI = FactPeriod(_RP_2024)


def _rank(period: ReportPeriod) -> int:
    """Current before prior, as a report would say."""
    return {_RP_2025: 0, _RP_2024: 1}.get(period, 2)


def _period_builder(facts_by_concept):
    for concept, facts in facts_by_concept.items():
        for fact in facts:
            fact.concept = concept
    return GridBuilder(lambda c: facts_by_concept.get(c, []), lambda c: c.name, _rank)


class TestPeriodAxis:
    """A column holding facts from more than one period is each column once per period."""

    @staticmethod
    def _members():
        dimension = _concept("dim")
        return dimension, _concept("a"), _concept("b")

    def test_one_period_is_laid_out_as_ever(self):
        dimension, a, b = self._members()
        x = _concept("x")
        facts = {
            x: [
                _fact(explicit={dimension: a}, period=_CUR),
                _fact(explicit={dimension: b}, period=_CUR),
            ]
        }
        grid = _period_builder(facts).explicit_dimension(
            [x, _concept("y")], dimension, [a, b], None
        )
        assert grid.period_axis is False
        assert list(grid.col_labels) == [a, b]

    def test_each_column_in_its_own_period_is_not_a_period_axis(self):
        """A baseline and a target column, each in its own year: the period follows the column."""
        dimension, a, b = self._members()
        x = _concept("x")
        facts = {
            x: [
                _fact(explicit={dimension: a}, period=_PRI),
                _fact(explicit={dimension: b}, period=_CUR),
            ]
        }
        grid = _period_builder(facts).explicit_dimension(
            [x, _concept("y")], dimension, [a, b], None
        )
        assert grid.period_axis is False
        assert list(grid.col_labels) == [a, b]

    def test_current_and_prior_in_one_column_split_every_column_by_period(self):
        dimension, a, b = self._members()
        x = _concept("x")
        cur_a = _fact(explicit={dimension: a}, period=_CUR, value="cur-a")
        pri_a = _fact(explicit={dimension: a}, period=_PRI, value="pri-a")
        cur_b = _fact(explicit={dimension: b}, period=_CUR, value="cur-b")
        grid = _period_builder({x: [pri_a, cur_a, cur_b]}).explicit_dimension(
            [x, _concept("y")], dimension, [a, b], None
        )
        assert grid.period_axis is True
        # every column once per period, current's before prior's, whatever order the facts came in
        assert list(grid.col_labels) == [a, b, a, b]
        assert grid.data == [[cur_a, cur_b, pri_a, None]]

    def test_the_same_cell_in_two_periods_is_not_a_duplicate(self, caplog):
        dimension, a, _b = self._members()
        x = _concept("x")
        facts = {
            x: [
                _fact(explicit={dimension: a}, period=_CUR),
                _fact(explicit={dimension: a}, period=_PRI),
            ]
        }
        with caplog.at_level(logging.WARNING, logger="mireport.report.layout"):
            _period_builder(facts).explicit_dimension([x], dimension, [a], None)
        assert not caplog.records

    def test_typed_rows_across_periods(self):
        dimension = _concept("typed")
        x, y = _concept("x"), _concept("y")
        cur = _fact(typed={dimension: "1"}, period=_CUR)
        pri = _fact(typed={dimension: "1"}, period=_PRI)
        grid = _period_builder({x: [cur, pri], y: []}).typed_dimension(
            [x, y], dimension
        )
        assert grid.period_axis is True
        assert grid.row_labels == ["1"]
        assert grid.data == [[cur, None, pri, None]]
        assert list(grid.col_labels) == [x, y, x, y]

    def test_dimension_set_across_periods(self):
        dimension = _concept("d")
        x = _concept("x")
        rel = Relationship("role", 1, x)
        member = _concept("m")
        cur = _fact(explicit={dimension: member}, period=_CUR)
        pri = _fact(explicit={dimension: member}, period=_PRI)
        # one member, one concept: the member is the (one) column, and it is split by period
        grid = _period_builder({}).dimension_set((dimension,), [(rel, cur), (rel, pri)])
        assert grid.period_axis is True
        assert grid.data == [[cur, pri]]

    def test_a_period_the_report_does_not_rank_comes_after_those_it_does(self):
        dimension, a, _b = self._members()
        x = _concept("x")
        other = FactPeriod(
            ReportPeriod(
                "old",
                DurationPeriodHolder.fromDays(date(2020, 1, 1), date(2020, 12, 31)),
            )
        )
        cur = _fact(explicit={dimension: a}, period=_CUR)
        old = _fact(explicit={dimension: a}, period=other)
        pri = _fact(explicit={dimension: a}, period=_PRI)
        grid = _period_builder({x: [old, pri, cur]}).explicit_dimension(
            [x], dimension, [a], None
        )
        assert grid.data == [[cur, pri, old]]

    def test_the_opening_and_closing_balance_of_a_period_are_not_one_cell(self):
        dimension, a, _b = self._members()
        x = _concept("x")
        opening = _fact(
            explicit={dimension: a}, period=FactPeriod(_RP_2025, PeriodPart.Start)
        )
        closing = _fact(
            explicit={dimension: a}, period=FactPeriod(_RP_2025, PeriodPart.End)
        )
        grid = _period_builder({x: [closing, opening]}).explicit_dimension(
            [x], dimension, [a], None
        )
        # the same OIM period but not the same statement: both are kept, opening first
        assert grid.period_axis is True
        assert grid.data == [[opening, closing]]
