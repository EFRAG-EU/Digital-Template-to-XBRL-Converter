"""mergePriorReport: last year's report supplies this one's comparatives; PriorReport reads it.

Both reports are read in memory from xBRL-JSON over a synthetic taxonomy, so each test says only
the facts it is about.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import date
from typing import Any

import pytest
from tests.unitTests.synthetic_taxonomy import (
    DURATION,
    ENTITY,
    ENTITY_SCHEME,
    GROUP_ENTRY_POINT,
    NAMESPACE,
    concept,
    cube,
    document,
    fact,
    group_taxonomy_json,
    qname,
    taxonomy_json,
)

from mireport.comparatives import PriorReport
from mireport.conversionresults import ConversionResultsBuilder, Severity
from mireport.exceptions import EarlyAbortException, InlineReportException
from mireport.filesupport import FilelikeAndFileName
from mireport.report import InlineReport
from mireport.report.comparatives import SkipReason, mergePriorReport
from mireport.report.model import PeriodPart, PeriodRole
from mireport.report.report import Report
from mireport.taxonomy import Taxonomy
from mireport.xbrljson_reader import XbrlJsonProcessor
from mireport.xbrljson_writer import toXbrlJson

ENTRY_POINT = "test://synthetic/comparatives"
ROLE = f"{NAMESPACE}/role/Comparatives"
PRIOR = "2024-01-01T00:00:00/2025-01-01T00:00:00"
OLDER = "2023-01-01T00:00:00/2024-01-01T00:00:00"
BASELINE = "2020-01-01T00:00:00/2021-01-01T00:00:00"
TARGET = "2030-01-01T00:00:00/2031-01-01T00:00:00"
OPENING = "2024-01-01T00:00:00"
SHARED = "2025-01-01T00:00:00"
CLOSING = "2026-01-01T00:00:00"
FILE = "last-year.json"
ADDRESS_ROLE = f"{NAMESPACE}/role/Addresses"


def _monetary(label: str) -> dict[str, Any]:
    return {
        "labels": {"en": {"http://www.xbrl.org/2003/role/label": label}},
        "dataType": "xbrli:monetaryItemType",
        "baseDataType": "xbrli:monetaryItemType",
        "periodType": "duration",
        "numeric": True,
    }


def _taxonomy(entryPoint: str = ENTRY_POINT) -> dict[str, Any]:
    concepts = {
        qname("Group"): concept("Group", abstract=True),
        qname("Revenue"): _monetary("Revenue"),
        qname("Staff"): concept("Staff", numeric=True),
        qname("Waste"): {**concept("Waste", numeric=True), "nillable": True},
        qname("Cash"): concept("Cash", numeric=True, periodType="instant"),
        qname("Note"): concept("Note"),
        qname("Flag"): {
            **concept("Flag"),
            "dataType": "xbrli:booleanItemType",
            "baseDataType": "xbrli:booleanItemType",
        },
    }
    return taxonomy_json(
        entryPoint,
        concepts=concepts,
        presentation={
            ROLE: {
                "definition": "Comparatives",
                "rows": [[0, qname("Group")]] + [[1, c] for c in list(concepts)[1:]],
            }
        },
    )


@pytest.fixture(autouse=True)
def taxonomy(register_taxonomy: Callable[[dict[str, Any]], Taxonomy]) -> Taxonomy:
    return register_taxonomy(_taxonomy())


def _revenue(value: str, period: str = DURATION, currency: str = "EUR") -> dict:
    f = fact(qname("Revenue"), value, period=period)
    f["dimensions"]["unit"] = f"iso4217:{currency}"
    return f


def _report(
    *facts: dict[str, Any], entryPoint: str = ENTRY_POINT, strict: bool = True
) -> InlineReport:
    doc = document(entryPoint, {f"f{n}": f for n, f in enumerate(facts)})
    doc["documentInfo"]["namespaces"]["iso4217"] = "http://www.xbrl.org/2003/iso4217"
    return XbrlJsonProcessor(
        doc, ConversionResultsBuilder(), strict=strict
    ).createReport()


def _merge(
    current: InlineReport, prior: InlineReport
) -> tuple[ConversionResultsBuilder, Any]:
    results = ConversionResultsBuilder()
    return results, mergePriorReport(current, prior, results, fileName=FILE)


def _messages(results: ConversionResultsBuilder, severity: Severity) -> list[str]:
    return [str(m.messageText) for m in results.messages if m.severity is severity]


def _priorFacts(report: InlineReport) -> dict[str, list[tuple[Any, PeriodPart]]]:
    found: dict[str, list[tuple[Any, PeriodPart]]] = {}
    for f in report.facts:
        if report.periodRole(f.period.reportPeriod) is PeriodRole.PRIOR:
            found.setdefault(f.concept.qname.localName, []).append(
                (f.value, f.period.part)
            )
    return found


def test_the_year_before_becomes_the_prior_period_and_renders() -> None:
    current = _report(_revenue("100"), fact(qname("Staff"), "10"))
    prior = _report(_revenue("90", PRIOR), fact(qname("Staff"), "9", period=PRIOR))
    prior.addFootnoteToFacts("Restated", prior.facts)
    results, merged = _merge(current, prior)

    assert merged.taken == 2 and not merged.skipped
    assert current.priorPeriod is not None and str(current.priorPeriod) == PRIOR
    assert _priorFacts(current) == {
        "Revenue": [(90, PeriodPart.Whole)],
        "Staff": [(9, PeriodPart.Whole)],
    }
    assert not any(f.footnotes for f in current.facts)
    assert _messages(results, Severity.INFO) == [
        f"Comparative figures for 2024-01-01 – 2024-12-31: 2 taken from {FILE}."
    ]
    current.requireAllFactsRendered = True
    assert current.getInlineReport().fileContent


def test_a_prior_duration_of_any_length_lines_up_by_its_end() -> None:
    eighteenMonths = "2023-07-01T00:00:00/2025-01-01T00:00:00"
    current = _report(_revenue("100"))
    _merge(current, _report(_revenue("150", eighteenMonths)))
    assert str(current.priorPeriod) == eighteenMonths


@pytest.mark.parametrize(
    "period",
    [
        pytest.param("2023-01-01T00:00:00/2024-07-01T00:00:00", id="gap"),
        pytest.param("2024-07-01T00:00:00/2025-07-01T00:00:00", id="overlap"),
    ],
)
def test_a_prior_report_that_does_not_line_up_raises_listing_periods(
    period: str,
) -> None:
    current = _report(_revenue("100"))
    prior = _report(_revenue("90", period))
    with pytest.raises(InlineReportException) as raised:
        _merge(current, prior)
    message = str(raised.value)
    assert "has no period ending where" in message
    assert "2025-01-01 – 2025-12-31" in message
    assert prior.defaultPeriod.firstDay.isoformat() in message
    assert current.priorPeriod is None and len(current.reportingPeriods) == 1


@pytest.mark.parametrize(
    ("current", "prior", "says"),
    [
        pytest.param(PRIOR, DURATION, "after this report's", id="wrong way round"),
        pytest.param(DURATION, DURATION, "the same period", id="the same twice"),
    ],
)
def test_reports_given_the_wrong_way_round_or_twice_say_so(
    current: str, prior: str, says: str
) -> None:
    with pytest.raises(InlineReportException, match=says):
        _merge(_report(_revenue("100", current)), _report(_revenue("90", prior)))


@pytest.mark.parametrize(
    "period",
    [
        pytest.param(PRIOR, id="the year before"),
        pytest.param("2025-07-01T00:00:00/2026-07-01T00:00:00", id="overlapping"),
    ],
)
def test_a_current_report_that_already_has_comparatives_raises(period: str) -> None:
    current = _report(_revenue("100"), _revenue("100"), _revenue("90", period))
    with pytest.raises(InlineReportException, match="already has comparatives"):
        _merge(current, _report(_revenue("90", PRIOR)))


def test_a_current_report_with_a_prior_period_raises() -> None:
    current = _report(_revenue("100"), _revenue("100"), _revenue("90", PRIOR))
    current.setPriorPeriodName(current.reportingPeriods[1].name)
    with pytest.raises(InlineReportException, match="already has comparatives"):
        _merge(current, _report(_revenue("90", PRIOR)))


def test_a_current_report_with_baseline_and_target_years_merges() -> None:
    current = _report(
        _revenue("100"),
        _revenue("100"),
        fact(qname("Staff"), "20", period=BASELINE),
        fact(qname("Staff"), "5", period=TARGET),
    )
    _merge(current, _report(_revenue("90", PRIOR)))
    roles = {str(p.duration): current.periodRole(p) for p in current.reportingPeriods}
    assert roles == {
        DURATION: PeriodRole.CURRENT,
        PRIOR: PeriodRole.PRIOR,
        BASELINE: PeriodRole.OTHER,
        TARGET: PeriodRole.OTHER,
    }


def test_only_the_lined_up_duration_is_taken_and_the_rest_named() -> None:
    """Last year's own comparatives, baseline and target years are all left out."""
    current = _report(_revenue("100"))
    prior = _report(
        _revenue("90", PRIOR),
        _revenue("90", PRIOR),
        _revenue("80", OLDER),
        fact(qname("Staff"), "20", period=BASELINE),
        fact(qname("Staff"), "21", period=BASELINE),
        fact(qname("Staff"), "5", period=TARGET),
    )
    results, merged = _merge(current, prior)
    assert _priorFacts(current) == {
        "Revenue": [(90, PeriodPart.Whole), (90, PeriodPart.Whole)]
    }
    assert merged.skipped == {SkipReason.OTHER_PERIOD: 4}
    assert (
        f"Facts in {FILE} for periods other than the one before 2025-01-01 – 2025-12-31 "
        "left out: 2023-01-01 – 2023-12-31 (1 facts), 2020-01-01 – 2020-12-31 (2 facts), "
        "2030-01-01 – 2030-12-31 (1 facts)."
    ) in _messages(results, Severity.INFO)


def test_another_entity_raises() -> None:
    other = _revenue("90", PRIOR)
    other["dimensions"]["entity"] = "lei:5299000000000000OTHER"
    with pytest.raises(InlineReportException, match="5299000000000000OTHER"):
        _merge(_report(_revenue("100")), _report(other))


def test_another_taxonomy_raises(
    register_taxonomy: Callable[[dict[str, Any]], Taxonomy],
) -> None:
    register_taxonomy(_taxonomy("test://synthetic/comparatives-other"))
    prior = _report(
        _revenue("90", PRIOR), entryPoint="test://synthetic/comparatives-other"
    )
    with pytest.raises(InlineReportException, match="taxonomy"):
        _merge(_report(_revenue("100")), prior)


def test_only_numbers_and_booleans_are_taken_and_nil_is_not() -> None:
    nil = fact(qname("Waste"), "", period=PRIOR, decimals=None)
    nil["value"] = None
    current = _report(
        _revenue("100"),
        fact(qname("Flag"), "false", decimals=None),
        fact(qname("Note"), "This year", decimals=None),
        fact(qname("Waste"), "3"),
    )
    prior = _report(
        _revenue("90", PRIOR),
        fact(qname("Flag"), "true", period=PRIOR, decimals=None),
        fact(qname("Note"), "Last year", period=PRIOR, decimals=None),
        nil,
    )
    results, merged = _merge(current, prior)
    assert merged.taken == 2
    assert merged.skipped == {SkipReason.NOT_COMPARABLE: 1, SkipReason.NIL: 1}
    assert _priorFacts(current) == {
        "Revenue": [(90, PeriodPart.Whole)],
        "Flag": [(True, PeriodPart.Whole)],
    }
    assert not _messages(results, Severity.WARNING)


def test_other_facts_come_only_into_a_table_row_a_comparative_is_drawn_in(
    register_taxonomy: Callable[[dict[str, Any]], Taxonomy],
) -> None:
    """Last year's note for target T1 is in the row its intensity is drawn in, so it comes too; a
    note for a target with no number, text for T1 in another group's table (a list of sites beside
    their areas), and text with no dimensions, stay out."""
    target = qname("TargetAxis")
    taxonomy = group_taxonomy_json()
    taxonomy["concepts"][qname("Address")] = concept("Address")
    taxonomy["concepts"][qname("AddressCube")] = concept("Address cube", hypercube=True)
    taxonomy["presentation"][ADDRESS_ROLE] = {
        "definition": "Addresses",
        "rows": [[0, qname("Address")]],
    }
    taxonomy["dimensions"][ADDRESS_ROLE] = {
        qname("AddressCube"): cube([qname("Address")], {}, [target])
    }
    register_taxonomy(taxonomy)

    def text(name: str, value: str, **dimensions: str) -> dict[str, Any]:
        return fact(
            qname(name), value, period=PRIOR, decimals=None, dimensions=dimensions
        )

    current = _report(
        fact(qname("Intensity"), "6", dimensions={target: "T1"}),
        fact(qname("Note"), "this year", decimals=None, dimensions={target: "T1"}),
        entryPoint=GROUP_ENTRY_POINT,
    )
    prior = _report(
        text("Note", "beside", **{target: "T1"}),
        fact(qname("Intensity"), "5", period=PRIOR, dimensions={target: "T1"}),
        text("Note", "alone", **{target: "T2"}),
        text("Address", "elsewhere", **{target: "T1"}),
        text("Orphan", "plain"),
        entryPoint=GROUP_ENTRY_POINT,
    )
    _, merged = _merge(current, prior)
    assert _priorFacts(current) == {
        "Intensity": [(5, PeriodPart.Whole)],
        "Note": [("beside", PeriodPart.Whole)],
    }
    assert merged.skipped == {SkipReason.NOT_COMPARABLE: 3}


def test_a_comparative_with_nothing_this_year_to_compare_with_is_left_out() -> None:
    """Staff this report has only for its baseline year, not its own: last year's is not shown."""
    current = _report(_revenue("100"), fact(qname("Staff"), "5", period=BASELINE))
    prior = _report(_revenue("90", PRIOR), fact(qname("Staff"), "9", period=PRIOR))
    results, merged = _merge(current, prior)
    assert _priorFacts(current) == {"Revenue": [(90, PeriodPart.Whole)]}
    assert merged.skipped == {SkipReason.NOTHING_THIS_YEAR: 1}
    assert (
        f"1 facts in {FILE} left out: this report has nothing for this year to compare them "
        "with."
    ) in _messages(results, Severity.INFO)


def test_instants_at_both_ends_of_the_prior_period_are_taken() -> None:
    current = _report(_revenue("100"), fact(qname("Cash"), "9", period=CLOSING))
    prior = _report(
        _revenue("90", PRIOR),
        fact(qname("Cash"), "7", period=OPENING),
        fact(qname("Cash"), "8", period=SHARED),
    )
    _merge(current, prior)
    assert sorted(_priorFacts(current)["Cash"], key=lambda vp: vp[0]) == [
        (7, PeriodPart.Start),
        (8, PeriodPart.End),
    ]


def test_the_shared_instant_keeps_this_reports_value_quietly_when_consistent() -> None:
    """The prior closing balance is this report's opening balance: one OIM fact."""
    current = _report(_revenue("100"), fact(qname("Cash"), "8", period=SHARED))
    prior = _report(_revenue("90", PRIOR), fact(qname("Cash"), "8", period=SHARED))
    results, merged = _merge(current, prior)
    assert merged.skipped == {SkipReason.ALREADY_REPORTED: 1}
    assert "Cash" not in _priorFacts(current)
    assert not _messages(results, Severity.WARNING)


def test_the_shared_instant_keeps_this_reports_value_with_a_warning_if_not() -> None:
    current = _report(_revenue("100"), fact(qname("Cash"), "8", period=SHARED))
    prior = _report(_revenue("90", PRIOR), fact(qname("Cash"), "6", period=SHARED))
    results, merged = _merge(current, prior)
    assert merged.skipped == {SkipReason.CONFLICT: 1}
    assert [f.value for f in current.facts if f.concept.qname.localName == "Cash"] == [
        8
    ]
    assert _messages(results, Severity.WARNING) == [
        f"1 facts in {FILE} differ from the same facts in this report; this report's are kept."
    ]


def test_another_currency_is_kept_with_one_warning() -> None:
    current = _report(_revenue("100"))
    prior = _report(_revenue("90", PRIOR, "USD"))
    results, merged = _merge(current, prior)
    assert merged.taken == 1
    (revenue,) = [f for f in current.facts if f.period.reportPeriod.name == "prior"]
    assert revenue.unit is not None and revenue.unit.measure.localName == "USD"
    assert _messages(results, Severity.WARNING) == [
        f"Comparative figures from {FILE} are in iso4217:USD, not this report's iso4217:EUR."
    ]


def test_the_entity_scheme_is_compared_too() -> None:
    current = _report(_revenue("100"))
    current.setEntity("http://example.com/other-scheme", "529900T8BM49AURSDO55")
    with pytest.raises(InlineReportException, match=ENTITY_SCHEME):
        _merge(current, _report(_revenue("90", PRIOR)))


def _lastYearsFile(taxonomy: Taxonomy, *extraFacts: dict[str, Any]) -> bytes:
    """Last year's report, built with FactBuilder and written as xBRL-JSON."""
    report = Report(taxonomy)
    report.setEntity(ENTITY_SCHEME, ENTITY.partition(":")[2])
    report.addDurationPeriod("fy", date(2024, 1, 1), date(2024, 12, 31))
    report.setDefaultPeriodName("fy")
    report.addFact(
        report.getFactBuilder()
        .setConcept(taxonomy.getConcept(qname("Revenue")))
        .setValue(90)
        .setDecimals(0)
        .setCurrency("EUR")
        .buildFact()
    )
    document = toXbrlJson(report).toDict()
    document["facts"].update({f"x{n}": f for n, f in enumerate(extraFacts)})
    return json.dumps(document).encode("utf-8")


def test_last_years_report_is_read_from_xbrl_json(taxonomy: Taxonomy) -> None:
    unknown = fact(qname("Missing"), "1", period=PRIOR)
    results = ConversionResultsBuilder()
    prior = PriorReport.fromXbrlJson(
        FilelikeAndFileName(_lastYearsFile(taxonomy, unknown), FILE), results
    )
    current = _report(_revenue("100"))
    prior.mergeInto(current, results)
    assert _priorFacts(current) == {"Revenue": [(90, PeriodPart.Whole)]}
    (warning,) = _messages(results, Severity.WARNING)
    assert warning.startswith("Prior-year report: Fact x0 not added")
    current.requireAllFactsRendered = True
    assert current.getInlineReport().fileContent


@pytest.mark.parametrize(
    ("content", "filename", "match"),
    [
        pytest.param(b"{}", "last-year.xlsx", "can't be read", id="extension"),
        pytest.param(b"not json", FILE, "not JSON", id="not JSON"),
    ],
)
def test_a_prior_report_that_cannot_be_read_stops_the_conversion(
    content: bytes, filename: str, match: str
) -> None:
    results = ConversionResultsBuilder()
    with pytest.raises(
        EarlyAbortException, match=f"^Prior-year report: .*{match}"
    ) as raised:
        PriorReport.fromFile(FilelikeAndFileName(content, filename), results)
    assert _messages(results, Severity.ERROR) == [str(raised.value)]
