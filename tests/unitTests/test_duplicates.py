"""Duplicate facts as the OIM and the "Handling Duplicate Facts" WGN (2025-01-14) define them.

The numeric examples are the WGN's (3.3): 2,500 (-2) is [2450, 2550], 2,000 (-3) is [1500, 2500],
2,470 (-1) is [2465, 2475]; closed intervals, every pair must overlap, and values stated to the same
accuracy must be equal.
"""

from __future__ import annotations

import logging
from datetime import date
from unittest.mock import MagicMock

import pytest

from mireport.exceptions import InlineReportException
from mireport.report import InlineReport
from mireport.report.duplicates import DuplicateClass, duplicateClass
from mireport.report.fact import Fact
from mireport.report.layout import (
    ReportLayoutOrganiser,
    ReportSection,
)
from mireport.report.model import (
    FactPeriod,
    PeriodPart,
    ReportPeriod,
    TypedDimensionValue,
    Unit,
)
from mireport.report.periods import DurationPeriodHolder
from mireport.taxonomy import Concept, PeriodType, PresentationStyle
from mireport.xml import getBootstrapQNameMaker

_Q = getBootstrapQNameMaker()
EUR = Unit.simple(_Q.fromString("iso4217:EUR"))
USD = Unit.simple(_Q.fromString("iso4217:USD"))
YEAR = DurationPeriodHolder.fromDays(date(2025, 1, 1), date(2025, 12, 31))
CUR = FactPeriod(ReportPeriod("cur", YEAR))

NUMBER = MagicMock(spec=Concept, isNumeric=True, isBoolean=False)
NUMBER.qname = _Q.fromString("utr:Number")
TEXT = MagicMock(spec=Concept, isNumeric=False, isBoolean=False)
TEXT.qname = _Q.fromString("utr:Text")
FLAG = MagicMock(spec=Concept, isNumeric=False, isBoolean=True)
FLAG.qname = _Q.fromString("utr:Flag")
STOCK = MagicMock(spec=Concept, isNumeric=True, isBoolean=False)
STOCK.qname = _Q.fromString("utr:Stock")
STOCK.periodType = PeriodType.Instant


def _fact(
    value,
    decimals=None,
    *,
    concept=NUMBER,
    unit=EUR,
    period=CUR,
    **extra,
) -> Fact:
    return Fact(
        concept,
        value,
        MagicMock(),
        period=period,
        unit=unit if concept is NUMBER else None,
        decimals=decimals,
        **extra,
    )


class TestClassification:
    def test_complete(self) -> None:
        assert (
            duplicateClass(_fact(2500, -2), _fact(2500, -2)) is DuplicateClass.COMPLETE
        )

    def test_numbers_are_compared_as_numbers_not_text(self) -> None:
        a, b = _fact("12.50", 2), _fact(12.5, 2)
        assert duplicateClass(a, b) is DuplicateClass.COMPLETE

    def test_no_decimals_means_infinite_so_inf_matches_none(self) -> None:
        assert (
            duplicateClass(_fact(5, None), _fact(5, "INF")) is DuplicateClass.COMPLETE
        )

    def test_the_wgn_examples_are_consistent(self) -> None:
        a, b, c = _fact(2500, -2), _fact(2000, -3), _fact(2470, -1)
        for x, y in [(a, b), (a, c), (b, c)]:
            assert duplicateClass(x, y) is DuplicateClass.CONSISTENT, (x, y)

    def test_same_accuracy_needs_the_same_value(self) -> None:
        """Their closed intervals touch at 2,500, but 2,000 and 3,000 (both -3) are inconsistent."""
        assert (
            duplicateClass(_fact(2000, -3), _fact(3000, -3))
            is DuplicateClass.INCONSISTENT
        )

    def test_disjoint_intervals_are_inconsistent(self) -> None:
        assert (
            duplicateClass(_fact(2000, -2), _fact(3000, -3))
            is DuplicateClass.INCONSISTENT
        )

    def test_an_exact_value_inside_a_rounded_one_is_consistent(self) -> None:
        assert (
            duplicateClass(_fact(2480, None), _fact(2500, -2))
            is DuplicateClass.CONSISTENT
        )
        assert (
            duplicateClass(_fact(2600, None), _fact(2500, -2))
            is DuplicateClass.INCONSISTENT
        )

    def test_the_interval_edge_is_inclusive(self) -> None:
        assert (
            duplicateClass(_fact(2550, None), _fact(2500, -2))
            is DuplicateClass.CONSISTENT
        )

    def test_text_is_complete_or_inconsistent(self) -> None:
        assert (
            duplicateClass(_fact("a", concept=TEXT), _fact("a", concept=TEXT))
            is DuplicateClass.COMPLETE
        )
        assert (
            duplicateClass(_fact("a", concept=TEXT), _fact("b", concept=TEXT))
            is DuplicateClass.INCONSISTENT
        )

    def test_booleans_are_compared_by_what_they_say(self) -> None:
        assert (
            duplicateClass(_fact("true", concept=FLAG), _fact(True, concept=FLAG))
            is DuplicateClass.COMPLETE
        )
        assert (
            duplicateClass(_fact("true", concept=FLAG), _fact("false", concept=FLAG))
            is DuplicateClass.INCONSISTENT
        )

    def test_a_unit_only_difference_is_alternative_facts_not_duplicates(self) -> None:
        assert duplicateClass(_fact(5, 0), _fact(5, 0, unit=USD)) is None

    def test_a_different_dimension_is_not_a_duplicate(self) -> None:
        typed = MagicMock(spec=Concept)
        typed.qname = _Q.fromString("utr:Axis")
        typed.typedElement.qname = _Q.fromString("utr:W")
        a = _fact(5, 0, typed_dimensions=[TypedDimensionValue.of(typed, "x")])
        assert duplicateClass(a, _fact(5, 0)) is None

    def test_period_names_do_not_matter_dates_do(self) -> None:
        alias = FactPeriod(
            ReportPeriod("another-name", DurationPeriodHolder(YEAR.start, YEAR.end))
        )
        assert duplicateClass(_fact(5, 0), _fact(5, 0, period=alias)) is (
            DuplicateClass.COMPLETE
        )
        other = FactPeriod(
            ReportPeriod(
                "p", DurationPeriodHolder.fromDays(date(2024, 1, 1), date(2024, 12, 31))
            )
        )
        assert duplicateClass(_fact(5, 0), _fact(5, 0, period=other)) is None

    def test_the_start_of_a_period_and_the_end_of_the_one_before_are_one_instant(
        self,
    ) -> None:
        # an opening balance of 2025 and a closing balance of 2024: two statements, one XBRL fact
        prior = ReportPeriod(
            "prior", DurationPeriodHolder.fromDays(date(2024, 1, 1), date(2024, 12, 31))
        )
        opening = FactPeriod(CUR.reportPeriod, PeriodPart.Start)
        closing = FactPeriod(prior, PeriodPart.End)
        assert opening != closing
        assert duplicateClass(
            _fact(5, 0, concept=STOCK, period=opening),
            _fact(5, 0, concept=STOCK, period=closing),
        ) is (DuplicateClass.COMPLETE)
        assert duplicateClass(
            _fact(5, 0, concept=STOCK, period=opening),
            _fact(9, 0, concept=STOCK, period=closing),
        ) is (DuplicateClass.INCONSISTENT)

    def test_the_start_and_the_end_of_one_period_are_different_instants(self) -> None:
        opening = FactPeriod(CUR.reportPeriod, PeriodPart.Start)
        closing = FactPeriod(CUR.reportPeriod, PeriodPart.End)
        assert (
            duplicateClass(
                _fact(5, 0, concept=STOCK, period=opening),
                _fact(5, 0, concept=STOCK, period=closing),
            )
            is None
        )


class TestReportDuplicates:
    def test_groups_by_duplicate_key(self) -> None:
        report = InlineReport.__new__(InlineReport)
        a, b, c = _fact(1, 0), _fact(2, 0), _fact(3, 0, unit=USD)
        report._facts = [a, b, c]
        assert report.duplicates() == [[a, b]]


class TestLayoutPolicy:
    """What a table cell shows when two facts belong in it."""

    @staticmethod
    def _cell(*facts: Fact) -> Fact:
        from mireport.report.layout.grid import _place

        cells = _place(facts, lambda f: "row", lambda f: "col")
        return cells["row", "col", CUR]

    def test_complete_shows_the_first_silently(self, caplog) -> None:
        first, second = _fact(5, 0), _fact(5, 0)
        with caplog.at_level(logging.INFO, logger="mireport.report.layout"):
            assert self._cell(first, second) is first
        assert not caplog.records

    def test_consistent_shows_the_more_precise_with_an_info(self, caplog) -> None:
        rounded, exact = _fact(2500, -2), _fact(2480, None)
        with caplog.at_level(logging.INFO, logger="mireport.report.layout"):
            assert self._cell(rounded, exact) is exact
            assert self._cell(exact, rounded) is exact
        assert {r.levelno for r in caplog.records} == {logging.INFO}

    def test_inconsistent_shows_the_first_with_a_warning(self, caplog) -> None:
        first, second = _fact(1, 0), _fact(2, 0)
        with caplog.at_level(logging.INFO, logger="mireport.report.layout"):
            assert self._cell(first, second) is first
        assert [r.levelno for r in caplog.records] == [logging.WARNING]
        assert "Inconsistent" in caplog.records[0].message

    def test_alternative_facts_show_the_first_with_a_warning(self, caplog) -> None:
        eur, usd = _fact(5, 0), _fact(5, 0, unit=USD)
        with caplog.at_level(logging.INFO, logger="mireport.report.layout"):
            assert self._cell(eur, usd) is eur
        assert [r.levelno for r in caplog.records] == [logging.WARNING]
        assert "not duplicates" in caplog.records[0].message


class TestFactsLeftOut:
    """checkAllFactsUsed: only a complete duplicate of a shown fact is no loss."""

    @staticmethod
    def _organiser(shown: Fact, *others: Fact, strict: bool) -> ReportLayoutOrganiser:
        report = MagicMock()
        report.facts = [shown, *others]
        report.requireAllFactsRendered = strict
        organiser = ReportLayoutOrganiser(MagicMock(), report)
        presentation = MagicMock()
        presentation.style = PresentationStyle.List
        organiser.reportSections = [
            ReportSection(
                relationshipToFact={MagicMock(): [shown]}, presentation=presentation
            )
        ]
        return organiser

    def test_a_complete_duplicate_left_out_is_no_loss(self) -> None:
        shown = _fact(5, 0)
        self._organiser(shown, _fact(5, 0, scale=-2), strict=True).checkAllFactsUsed()

    @pytest.mark.parametrize(
        "left_out", [_fact(2480, None), _fact(6, 0), _fact(5, 0, unit=USD)]
    )
    def test_anything_else_left_out_is_lost(self, left_out: Fact) -> None:
        shown = _fact(2500, -2) if left_out.value == 2480 else _fact(5, 0)
        organiser = self._organiser(shown, left_out, strict=True)
        with pytest.raises(InlineReportException, match="appear nowhere"):
            organiser.checkAllFactsUsed()

    def test_when_not_strict_an_inconsistent_duplicate_is_logged_for_developers(
        self, caplog
    ) -> None:
        # The report preparer hears of it through InlineReport.reportInconsistentDuplicateFacts.
        organiser = self._organiser(_fact(5, 0), _fact(6, 0), strict=False)
        with caplog.at_level(logging.DEBUG, logger="mireport.report.layout"):
            organiser.checkAllFactsUsed()
        records = [r for r in caplog.records if "inconsistent duplicate" in r.message]
        assert records
        assert all(r.levelno == logging.DEBUG for r in records)

    def test_when_not_strict_a_fact_shown_nowhere_is_a_warning(self, caplog) -> None:
        organiser = self._organiser(_fact(5, 0), _fact(5, 0, unit=USD), strict=False)
        with caplog.at_level(logging.DEBUG, logger="mireport.report.layout"):
            organiser.checkAllFactsUsed()
        assert [r.levelno for r in caplog.records if "not shown" in r.message] == [
            logging.WARNING
        ]
