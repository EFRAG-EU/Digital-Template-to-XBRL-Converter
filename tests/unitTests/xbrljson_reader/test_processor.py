"""XbrlJsonProcessor, exercised against the real VSME taxonomy.

The coverage-report generator emits an xBRL-JSON document that exercises every reportable concept
and every hypercube dimension (explicit, typed, enumerations, units, instants), so reading its
output back is a broad test of the reader against real OIM shapes.
"""

from __future__ import annotations

import copy
from datetime import date
from typing import Any

import pytest

from mireport.conversionresults import ConversionResultsBuilder, Severity
from mireport.coverage_report_generator._sampling import SampleEntityPeriod
from mireport.coverage_report_generator.report_writer import buildCoverageReportSet
from mireport.taxonomy import (
    Taxonomy,
    getTaxonomy,
    listTaxonomies,
    loadBuiltInTaxonomyJSON,
)
from mireport.xbrljson_reader import XbrlJsonProcessor
from mireport.xbrljson_reader.processor import XbrlJsonException

DURATION = "2025-01-01T00:00:00/2026-01-01T00:00:00"
SAMPLE = SampleEntityPeriod(
    entity="lei:529900T8BM49AURSDO55",
    entityPrefix="lei",
    entityNamespace="http://standards.iso.org/iso/17442",
    periodInstant="2025-12-31T24:00:00",
    periodDuration=DURATION,
)


@pytest.fixture(scope="module")
def taxonomy() -> Taxonomy:
    if not listTaxonomies():
        loadBuiltInTaxonomyJSON()
    return getTaxonomy(
        next(ep for ep in listTaxonomies() if any("vsme" in uri.lower() for uri in ep))
    )


@pytest.fixture(scope="module")
def coverage(taxonomy: Taxonomy) -> dict[str, Any]:
    reports = buildCoverageReportSet(taxonomy, SAMPLE, sorted(taxonomy.entryPointSet))
    return {"values": reports.allValues, "nil": reports.allNil}


def _read(
    document: dict[str, Any], *, strict: bool = False
) -> tuple[XbrlJsonProcessor, ConversionResultsBuilder]:
    results = ConversionResultsBuilder()
    processor = XbrlJsonProcessor(document, results, strict=strict)
    processor.createReport()
    return processor, results


def test_every_generated_value_fact_is_read_back(coverage: dict[str, Any]) -> None:
    processor, results = _read(coverage["values"], strict=True)
    assert processor.factsAdded == len(coverage["values"]["facts"])
    assert processor.factsSkipped == 0
    assert not [m for m in results.messages if m.severity is Severity.ERROR]


def test_nil_facts_are_reported_not_silently_dropped(coverage: dict[str, Any]) -> None:
    processor, results = _read(coverage["nil"])
    assert processor.factsAdded == 0
    assert processor.factsSkipped == len(coverage["nil"]["facts"])
    assert len(results.messages) == processor.factsSkipped
    with pytest.raises(XbrlJsonException, match="could not be added"):
        _read(coverage["nil"], strict=True)


def test_wrong_document_type_is_refused(coverage: dict[str, Any]) -> None:
    doc = copy.deepcopy(coverage["values"])
    doc["documentInfo"]["documentType"] = "https://xbrl.org/2021/xbrl-csv"
    with pytest.raises(XbrlJsonException, match="documentType"):
        _read(doc)


def test_unbaked_entry_point_is_refused(coverage: dict[str, Any]) -> None:
    doc = copy.deepcopy(coverage["values"])
    doc["documentInfo"]["taxonomy"] = ["https://example.com/not-baked.xsd"]
    with pytest.raises(XbrlJsonException, match="No baked taxonomy"):
        _read(doc)


def test_the_taxonomy_is_found_by_its_entry_point_documents(
    coverage: dict[str, Any], taxonomy: Taxonomy
) -> None:
    doc = copy.deepcopy(coverage["values"])
    # The report's taxonomy is the one whose entry point is exactly the set of documents named.
    doc["documentInfo"]["taxonomy"] = sorted(taxonomy.entryPointSet, reverse=True)
    processor, _ = _read(doc)
    assert processor.taxonomy is taxonomy


@pytest.mark.parametrize(
    "taxonomy_value, message",
    [
        ("https://xbrl.efrag.org/x.xsd", "must be an array"),
        ([], "unusable"),
        (["not/an/absolute/uri.xsd"], "unusable"),
        (["https://example.com/not-baked.xsd"], "No baked taxonomy"),
    ],
)
def test_an_unusable_taxonomy_is_refused(
    coverage: dict[str, Any], taxonomy_value: Any, message: str
) -> None:
    doc = copy.deepcopy(coverage["values"])
    doc["documentInfo"]["taxonomy"] = taxonomy_value
    with pytest.raises(XbrlJsonException, match=message):
        _read(doc)


def test_two_entities_are_refused(coverage: dict[str, Any]) -> None:
    doc = copy.deepcopy(coverage["values"])
    first = next(iter(doc["facts"].values()))
    first["dimensions"]["entity"] = "lei:5493001KJTIIGC8Y1R12"
    with pytest.raises(XbrlJsonException, match="one reporting entity"):
        _read(doc)


def test_a_report_with_only_instants_has_no_period_to_anchor_on(
    coverage: dict[str, Any],
) -> None:
    doc = copy.deepcopy(coverage["values"])
    doc["facts"] = {
        k: f for k, f in doc["facts"].items() if "/" not in f["dimensions"]["period"]
    }
    with pytest.raises(XbrlJsonException, match="no duration period"):
        _read(doc)


def test_instant_that_is_not_the_end_of_a_reported_duration_is_skipped(
    coverage: dict[str, Any],
) -> None:
    doc = copy.deepcopy(coverage["values"])
    instants = [
        f for f in doc["facts"].values() if "/" not in f["dimensions"]["period"]
    ]
    assert instants, "the VSME coverage report should contain instant facts"
    instants[0]["dimensions"]["period"] = "2024-06-30T24:00:00"
    processor, results = _read(doc)
    assert processor.factsSkipped == 1
    assert "nor the start or end of one" in results.messages[-1].messageText
    assert "2024-07-01T00:00:00" in results.messages[-1].messageText


def test_instant_written_as_next_day_midnight_is_the_same_instant(
    coverage: dict[str, Any],
) -> None:
    doc = copy.deepcopy(coverage["values"])
    for fact in doc["facts"].values():
        if fact["dimensions"]["period"] == SAMPLE.periodInstant:
            fact["dimensions"]["period"] = "2026-01-01T00:00:00"
    processor, _ = _read(doc, strict=True)
    assert processor.factsSkipped == 0


def test_a_period_is_the_oim_one_with_the_end_not_included() -> None:
    duration = XbrlJsonProcessor._parseDuration(DURATION)
    assert (duration.start.isoformat(), duration.end.isoformat()) == (
        "2025-01-01T00:00:00",
        "2026-01-01T00:00:00",
    )
    # to the people reading the report it is the days it includes
    assert (duration.firstDay.isoformat(), duration.lastDay.isoformat()) == (
        "2025-01-01",
        "2025-12-31",
    )
    with pytest.raises(XbrlJsonException, match="duration"):
        XbrlJsonProcessor._parseDuration("2025-12-31T24:00:00")


def test_the_end_of_a_day_may_be_written_as_24_hundred() -> None:
    duration = XbrlJsonProcessor._parseDuration(
        "2024-12-31T24:00:00/2025-12-31T24:00:00"
    )
    assert duration == XbrlJsonProcessor._parseDuration(DURATION)


def test_a_report_period_that_is_not_whole_days_is_refused() -> None:
    with pytest.raises(XbrlJsonException, match="whole days"):
        XbrlJsonProcessor._parseDuration("2025-01-01T09:00:00/2026-01-01T00:00:00")


def test_an_instant_at_the_start_of_the_period_is_an_opening_balance(
    coverage: dict[str, Any],
) -> None:
    """The instant a period starts at is a period of its own to XBRL, one day back. With no
    period ending there it can only be this one's opening balance."""
    doc = copy.deepcopy(coverage["values"])
    # The report's variant facts sit in earlier periods, one of which ends where this one
    # starts; this test is about a start with nothing ending there.
    mainPeriod = {SAMPLE.periodDuration, SAMPLE.periodInstant}
    doc["facts"] = {
        key: fact
        for key, fact in doc["facts"].items()
        if fact["dimensions"]["period"] in mainPeriod
    }
    instants = [
        f for f in doc["facts"].values() if "/" not in f["dimensions"]["period"]
    ]
    instants[0]["dimensions"]["period"] = "2024-12-31T24:00:00"
    results = ConversionResultsBuilder()
    report = XbrlJsonProcessor(doc, results, strict=True).createReport()
    (opening,) = [f for f in report.facts if f.period.isStart]
    assert opening.period.reportPeriod == report.defaultReportPeriod
    assert opening.period.aoixPeriodType == "instant-start"


def test_an_instant_at_the_start_of_a_period_is_the_end_of_the_one_before(
    coverage: dict[str, Any],
) -> None:
    """xBRL-JSON cannot say which was meant, so the end of the period before wins."""
    doc = copy.deepcopy(coverage["values"])
    facts = list(doc["facts"].values())
    prior = next(f for f in facts if "/" in f["dimensions"]["period"])
    prior["dimensions"]["period"] = "2024-01-01T00:00:00/2025-01-01T00:00:00"
    instant = next(f for f in facts if "/" not in f["dimensions"]["period"])
    instant["dimensions"]["period"] = "2024-12-31T24:00:00"
    report = XbrlJsonProcessor(
        doc, ConversionResultsBuilder(), strict=False
    ).createReport()
    assert not [f for f in report.facts if f.period.isStart]
    assert any(
        f.period.isEnd and f.period.reportPeriod.duration.lastDay == date(2024, 12, 31)
        for f in report.facts
    )
