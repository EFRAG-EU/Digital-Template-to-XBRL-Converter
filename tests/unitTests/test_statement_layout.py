"""A hypercube with no dimension is a statement: a row for each relationship, a column for each of
the report's periods.

A roll-forward (cash at the start and at the end of a period) is two rows of one concept, each
taking the facts of its own part of the period, and each labelled by the taxonomy's own preferred
labels. A concept in two roles is shown in both.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from datetime import date
from typing import Any

import pytest
from tests.unitTests.synthetic_taxonomy import (
    NAMESPACE,
    concept,
    cube,
    qname,
    taxonomy_json,
)
from tests.unitTests.table_integrity import problems

from mireport.report import InlineReport
from mireport.report.factbuilder import FactBuilder
from mireport.report.model import PeriodPart
from mireport.taxonomy import PresentationStyle, Taxonomy
from mireport.xml import getBootstrapQNameMaker

LABEL_ROOT = "http://www.xbrl.org/2003/role"
START = f"{LABEL_ROOT}/periodStartLabel"
END = f"{LABEL_ROOT}/periodEndLabel"
CASH_FLOW = f"{NAMESPACE}/role/CashFlow"
BALANCE = f"{NAMESPACE}/role/Balance"

_Q = getBootstrapQNameMaker()
_Q.addNamespacePrefix("utr", "http://www.xbrl.org/2009/utr")

Register = Callable[[dict[str, Any]], Taxonomy]


def _statements() -> dict[str, Any]:
    """Two statements, each a cube with no dimension. ``Cash`` is in both: at the start and at the
    end of the period in the cash flow, at the end of it on the balance sheet."""
    cash = concept("Cash", numeric=True, periodType="instant")
    cash["labels"]["en"][START] = "Cash at the start of the period"
    cash["labels"]["en"][END] = "Cash at the end of the period"
    concepts = {
        qname("CashFlowCube"): concept("Cash flow [Table]", hypercube=True),
        qname("BalanceCube"): concept("Balance [Table]", hypercube=True),
        qname("CashFlowLines"): concept("Cash flow line items", abstract=True),
        qname("BalanceLines"): concept("Balance line items", abstract=True),
        qname("Cash"): cash,
        qname("Flow"): concept("Net cash flow", numeric=True),
        qname("Assets"): concept("Total assets", numeric=True, periodType="instant"),
    }
    return taxonomy_json(
        "test://synthetic/statements",
        concepts=concepts,
        presentation={
            CASH_FLOW: {
                "definition": "Cash flow",
                "rows": [
                    [0, qname("CashFlowCube")],
                    [1, qname("CashFlowLines")],
                    [2, qname("Cash"), START],
                    [2, qname("Flow")],
                    [2, qname("Cash"), END],
                ],
            },
            BALANCE: {
                "definition": "Balance",
                "rows": [
                    [0, qname("BalanceCube")],
                    [1, qname("BalanceLines")],
                    [2, qname("Cash")],
                    [2, qname("Assets")],
                ],
            },
        },
        dimensions={
            CASH_FLOW: {
                qname("CashFlowCube"): cube([qname("Cash"), qname("Flow")], {})
            },
            BALANCE: {qname("BalanceCube"): cube([qname("Cash"), qname("Assets")], {})},
        },
    )


def _report(
    taxonomy: Taxonomy, periods: tuple[str, ...] = ("cur", "prior")
) -> InlineReport:
    report = InlineReport(taxonomy)
    report.setEntity("http://standards.iso.org/iso/17442", "529900T8BM49AURSDO55")
    report.addDurationPeriod("cur", date(2025, 1, 1), date(2025, 12, 31))
    report.addDurationPeriod("prior", date(2024, 1, 1), date(2024, 12, 31))
    report.setDefaultPeriodName("cur")
    if "prior" in periods:
        report.setPriorPeriodName("prior")
    report.requireAllFactsRendered = True

    def add(name: str, value: int, period: str, at: PeriodPart | None = None) -> None:
        c = taxonomy.getConcept(name)
        fb = FactBuilder(report).setConcept(c).setValue(value).setDecimals(0)
        fb.setSimpleUnit(_Q.fromString("utr:kg"))
        fb.setNamedPeriod(period, at=at) if at else fb.setNamedPeriod(period)
        report.addFact(fb.buildFact())

    values = {"cur": (1000, 1200, 50, 9000), "prior": (900, 1000, 40, 8000)}
    for period in periods:
        start, end, flow, assets = values[period]
        add(qname("Cash"), start, period, PeriodPart.Start)
        add(qname("Cash"), end, period, PeriodPart.End)
        add(qname("Flow"), flow, period)
        add(qname("Assets"), assets, period)
    return report


def _html(report: InlineReport) -> str:
    return report.getInlineReport().fileContent.decode("utf-8")


def _tables(html: str) -> list[list[list[str]]]:
    """Each table as its rows of cell texts."""
    return [
        [
            [
                re.sub(r"<[^>]+>", "", cell).strip()
                for cell in re.findall(r"<t[hd][^>]*>.*?</t[hd]>", row, re.DOTALL)
            ]
            for row in re.findall(r"<tr.*?</tr>", table, re.DOTALL)
        ]
        for table in re.findall(r"<table.*?</table>", html, re.DOTALL)
    ]


def _body(table: list[list[str]]) -> list[list[str]]:
    """The rows that start with a label and carry only numbers, not the header rows."""
    return [
        row
        for row in table
        if len(row) > 1
        and any(ch.isalpha() for ch in row[0])
        and all(re.fullmatch(r"-?[\d,]+", cell) for cell in row[1:])
    ]


# Sections come in the order of their roles' definitions: "Balance", then "Cash flow".


@pytest.fixture
def statements(register_taxonomy: Register) -> Taxonomy:
    return register_taxonomy(_statements())


def test_a_cube_with_no_dimension_is_a_table_not_a_list_of_dimension_sets(
    statements: Taxonomy,
) -> None:
    assert [g.style for g in statements.presentation] == [PresentationStyle.Table] * 2
    html = _html(_report(statements))
    assert html.count('<table class="thematic-table">') == 2
    assert problems(html) == []


def test_the_rows_are_the_relationships_in_presentation_order_with_their_own_labels(
    statements: Taxonomy,
) -> None:
    _, cash_flow = _tables(_html(_report(statements)))
    assert [row[0] for row in _body(cash_flow)] == [
        "Cash at the start of the period",
        "Net cash flow",
        "Cash at the end of the period",
    ]


def test_the_columns_are_the_periods_current_first(statements: Taxonomy) -> None:
    _, cash_flow = _tables(_html(_report(statements)))
    years = [
        cell for row in cash_flow for cell in row if re.fullmatch(r".*20\d\d", cell)
    ]
    assert [y[-4:] for y in years][:2] == ["2025", "2024"]


def test_a_roll_forward_row_takes_the_facts_of_its_part_of_the_period(
    statements: Taxonomy,
) -> None:
    _, cash_flow = _tables(_html(_report(statements)))
    rows = {row[0]: row[1:] for row in _body(cash_flow)}
    assert rows["Cash at the start of the period"] == ["1,000", "900"]
    assert rows["Cash at the end of the period"] == ["1,200", "1,000"]
    assert rows["Net cash flow"] == ["50", "40"]


def test_a_concept_in_two_statements_is_shown_in_both(statements: Taxonomy) -> None:
    balance, cash_flow = _tables(_html(_report(statements)))
    in_cash_flow = {row[0]: row[1:] for row in _body(cash_flow)}
    in_balance = {row[0]: row[1:] for row in _body(balance)}
    # the end of the period is on both; the start of it only where a row asks for it
    assert in_cash_flow["Cash at the end of the period"] == ["1,200", "1,000"]
    assert in_balance["Cash"] == ["1,200", "1,000"]
    assert "Cash at the start of the period" not in in_balance
    assert in_balance["Total assets"] == ["9,000", "8,000"]


def test_one_period_is_said_once_over_the_table(statements: Taxonomy) -> None:
    html = _html(_report(statements, periods=("cur",)))
    _, cash_flow = _tables(html)
    assert [row[0] for row in _body(cash_flow)] == [
        "Cash at the start of the period",
        "Net cash flow",
        "Cash at the end of the period",
    ]
    assert len([c for row in cash_flow for c in row if "2025" in c]) == 1
    assert problems(html) == []


NEGATED = f"{LABEL_ROOT}/negatedLabel"
NEGATED_TOTAL = f"{LABEL_ROOT}/negatedTotalLabel"
COSTS_ROLE = f"{NAMESPACE}/role/Costs"


def _costs() -> dict[str, Any]:
    """A statement of costs: three duration items, two of them shown with the opposite sign."""
    concepts = {
        qname("CostsCube"): concept("Costs [Table]", hypercube=True),
        qname("CostsLines"): concept("Costs line items", abstract=True),
        qname("Staff"): concept("Staff costs", numeric=True),
        qname("Rent"): concept("Rent", numeric=True),
        qname("Total"): concept("Total costs", numeric=True),
    }
    return taxonomy_json(
        "test://synthetic/costs",
        concepts=concepts,
        presentation={
            COSTS_ROLE: {
                "definition": "Costs",
                "rows": [
                    [0, qname("CostsCube")],
                    [1, qname("CostsLines")],
                    [2, qname("Staff"), NEGATED],
                    [2, qname("Rent")],
                    [2, qname("Total"), NEGATED_TOTAL],
                ],
            }
        },
        dimensions={
            COSTS_ROLE: {
                qname("CostsCube"): cube(
                    [qname("Staff"), qname("Rent"), qname("Total")], {}
                )
            }
        },
    )


def _costs_report(taxonomy: Taxonomy, values: dict[str, int]) -> InlineReport:
    report = InlineReport(taxonomy)
    report.setEntity("http://standards.iso.org/iso/17442", "529900T8BM49AURSDO55")
    report.addDurationPeriod("cur", date(2025, 1, 1), date(2025, 12, 31))
    report.setDefaultPeriodName("cur")
    report.requireAllFactsRendered = True
    for name, value in values.items():
        fb = FactBuilder(report).setConcept(taxonomy.getConcept(qname(name)))
        fb.setValue(value).setDecimals(0).setNamedPeriod("cur")
        fb.setSimpleUnit(_Q.fromString("utr:kg"))
        report.addFact(fb.buildFact())
    return report


@pytest.fixture
def costs(register_taxonomy: Register) -> Taxonomy:
    return register_taxonomy(_costs())


def test_a_row_with_a_negated_label_shows_the_opposite_sign_to_the_fact(
    costs: Taxonomy,
) -> None:
    html = _html(_costs_report(costs, {"Staff": 30, "Rent": 20, "Total": 50}))
    (table,) = _tables(html)
    rows = {row[0]: row[1:] for row in _body(table)}
    assert rows == {"Staff costs": ["-30"], "Rent": ["20"], "Total costs": ["-50"]}
    assert problems(html) == []


def test_a_negative_fact_in_a_negated_row_is_shown_unsigned(costs: Taxonomy) -> None:
    html = _html(_costs_report(costs, {"Staff": -30, "Rent": -20, "Total": -50}))
    (table,) = _tables(html)
    rows = {row[0]: row[1:] for row in _body(table)}
    assert rows == {"Staff costs": ["30"], "Rent": ["-20"], "Total costs": ["50"]}


def test_a_zero_in_a_negated_row_is_just_zero(costs: Taxonomy) -> None:
    html = _html(_costs_report(costs, {"Staff": 0, "Rent": 0, "Total": 0}))
    (table,) = _tables(html)
    assert {row[0]: row[1:] for row in _body(table)}["Staff costs"] == ["0"]


def test_the_fact_keeps_its_value_whatever_the_row_shows(costs: Taxonomy) -> None:
    html = _html(_costs_report(costs, {"Staff": 30, "Rent": 20, "Total": 50}))
    staff = re.search(r"<ix:nonFraction[^>]*name=\"[^\"]*Staff\"[^>]*>", html)
    assert staff is not None
    assert 'sign="-"' not in staff.group(0)
    html = _html(_costs_report(costs, {"Staff": -30, "Rent": 20, "Total": 50}))
    staff = re.search(r"<ix:nonFraction[^>]*name=\"[^\"]*Staff\"[^>]*>", html)
    assert staff is not None
    assert 'sign="-"' in staff.group(0)


def _only_ends(taxonomy: Taxonomy, repeat: bool = False) -> InlineReport:
    """Cash only at the end of each period, as a producer that tags the opening balance of a year
    as the closing balance of the year before does; optionally with every fact given twice."""
    report = InlineReport(taxonomy)
    report.setEntity("http://standards.iso.org/iso/17442", "529900T8BM49AURSDO55")
    report.addDurationPeriod("cur", date(2025, 1, 1), date(2025, 12, 31))
    report.addDurationPeriod("prior", date(2024, 1, 1), date(2024, 12, 31))
    report.setDefaultPeriodName("cur")
    report.setPriorPeriodName("prior")
    report.requireAllFactsRendered = True
    for name, value, period, at in [
        ("Cash", 1000, "prior", PeriodPart.End),
        ("Cash", 1200, "cur", PeriodPart.End),
        ("Flow", 50, "cur", None),
    ]:
        for _ in range(2 if repeat else 1):
            fb = FactBuilder(report).setConcept(taxonomy.getConcept(qname(name)))
            fb.setValue(value).setDecimals(0).setSimpleUnit(_Q.fromString("utr:kg"))
            fb.setNamedPeriod(period, at=at) if at else fb.setNamedPeriod(period)
            report.addFact(fb.buildFact())
    return report


def test_an_opening_row_takes_the_closing_balance_of_the_period_before(
    statements: Taxonomy,
) -> None:
    _, cash_flow = _tables(_html(_only_ends(statements)))
    rows = {row[0]: row[1:] for row in cash_flow if len(row) > 1}
    # the end of 2024 is the start of 2025: the current column's opening balance
    assert rows["Cash at the start of the period"] == ["1,000", ""]
    assert rows["Cash at the end of the period"] == ["1,200", "1,000"]


def test_a_repeated_fact_is_shown_once_and_is_not_left_over(
    statements: Taxonomy, caplog: pytest.LogCaptureFixture
) -> None:
    html = _html(_only_ends(statements, repeat=True))
    assert html.count('<table class="thematic-table">') == 2
    assert "belong in the same table cell" not in caplog.text
