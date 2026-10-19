"""XbrlJsonDocument: an xBRL-JSON document read and written as data, before any taxonomy."""

from __future__ import annotations

import copy
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from mireport.xbrljson_reader._periods import parseDateTime, parseDuration, parsePeriod
from mireport.xbrljson_reader.document import (
    INVALID_JSON_STRUCTURE,
    INVALID_PERIOD,
    FactProblem,
    XbrlJsonDocument,
    XbrlJsonException,
    XbrlJsonFact,
)

DOCUMENT: dict[str, Any] = {
    "documentInfo": {
        "documentType": "https://xbrl.org/2021/xbrl-json",
        "features": {"xbrl:canonicalValues": True},
        "namespaces": {"ex": "http://example.com/ex", "lei": "http://lei"},
        "taxonomy": ["http://example.com/ex.xsd"],
    },
    "facts": {
        "f1": {
            "value": "12.5",
            "decimals": 1,
            "dimensions": {
                "concept": "ex:Total",
                "entity": "lei:X",
                "period": "2025-01-01T00:00:00/2026-01-01T00:00:00",
                "unit": "iso4217:EUR",
                "ex:ScopeAxis": "ex:ScopeA",
            },
            "links": {"footnote": {"_": ["n1"]}},
        },
        "n1": {
            "value": "<p>a note</p>",
            "dimensions": {"concept": "xbrl:note", "noteId": "n1", "language": "en"},
        },
    },
}


def test_a_document_reads_and_writes_back_the_same() -> None:
    document = XbrlJsonDocument.fromDict(DOCUMENT)
    assert document.toDict() == DOCUMENT
    (fact, note) = document.facts
    assert (fact.concept, fact.unit, fact.decimals) == ("ex:Total", "iso4217:EUR", 1)
    assert fact.dimensions == {"ex:ScopeAxis": "ex:ScopeA"}
    assert fact.links == {"footnote": {"_": ("n1",)}}
    assert note.isNote and note.noteId == "n1" and note.language == "en"


def test_to_file_and_from_file_round_trip(tmp_path: Path) -> None:
    path = tmp_path / "report.json"
    XbrlJsonDocument.fromDict(DOCUMENT).toFile(path)
    text = path.read_text(encoding="utf-8")
    assert text.endswith("}\n") and "\r\n" not in text
    assert XbrlJsonDocument.fromFile(path).toDict() == DOCUMENT


def test_json_numbers_are_read_exactly(tmp_path: Path) -> None:
    path = tmp_path / "report.json"
    path.write_text(
        '{"documentInfo": {"documentType": "https://xbrl.org/2021/xbrl-json"},'
        ' "facts": {"f": {"value": 0.1000000000000000000001, "dimensions": {"concept": "a:b"}}}}',
        encoding="utf-8",
    )
    assert XbrlJsonDocument.fromFile(path).facts[0].value == "0.1000000000000000000001"


@pytest.mark.parametrize("text", ["NaN", "Infinity", "-Infinity", "{not json"])
def test_what_is_not_json_is_refused(tmp_path: Path, text: str) -> None:
    path = tmp_path / "report.json"
    path.write_text(
        '{"documentInfo": {"documentType": "https://xbrl.org/2021/xbrl-json"},'
        f' "facts": {{"f": {{"value": {text}, "dimensions": {{"concept": "a:b"}}}}}}}}',
        encoding="utf-8",
    )
    with pytest.raises(XbrlJsonException):
        XbrlJsonDocument.fromFile(path)


@pytest.mark.parametrize(
    "data, message",
    [
        ([], "JSON object"),
        ({}, "documentInfo"),
        ({"documentInfo": {"documentType": "x"}}, "documentType"),
        (
            {
                "documentInfo": {
                    "documentType": "https://xbrl.org/2021/xbrl-json",
                    "namespaces": {"a": 1},
                }
            },
            "namespaces",
        ),
        (
            {
                "documentInfo": {"documentType": "https://xbrl.org/2021/xbrl-json"},
                "facts": [],
            },
            "keyed by fact id",
        ),
    ],
)
def test_what_is_not_an_xbrl_json_document_raises(data: Any, message: str) -> None:
    with pytest.raises(XbrlJsonException, match=message):
        XbrlJsonDocument.fromDict(data)


@pytest.mark.parametrize(
    "fact, message",
    [
        ("text", "JSON object"),
        ({"value": "1"}, "dimensions object"),
        ({"value": "1", "dimensions": {}}, "needs a concept"),
        ({"value": "1", "dimensions": {"concept": "a:b", "unit": 3}}, "unit"),
        ({"value": "1", "dimensions": {"concept": "a:b", "a:Axis": 3}}, "a:Axis"),
        ({"value": {}, "dimensions": {"concept": "a:b"}}, "must be a string"),
        ({"value": "1", "decimals": "2", "dimensions": {"concept": "a:b"}}, "integer"),
        ({"value": "1", "decimals": True, "dimensions": {"concept": "a:b"}}, "integer"),
        (
            {
                "value": "1",
                "links": {"footnote": ["n"]},
                "dimensions": {"concept": "a:b"},
            },
            "links",
        ),
    ],
)
def test_a_badly_shaped_fact_is_kept_aside_not_fatal(fact: Any, message: str) -> None:
    data = copy.deepcopy(DOCUMENT)
    data["facts"]["bad"] = fact
    document = XbrlJsonDocument.fromDict(data)
    assert [f.id for f in document.facts] == ["f1", "n1"]
    (invalid,) = document.invalidFacts
    assert invalid.id == "bad" and invalid.problem.code == INVALID_JSON_STRUCTURE
    assert message in invalid.problem.reason


@pytest.mark.parametrize(
    "value, text",
    [(True, "true"), (False, "false"), (3, "3"), (Decimal("1.50"), "1.50")],
)
def test_values_written_as_json_numbers_or_booleans_are_taken_as_their_text(
    value: Any, text: str
) -> None:
    assert (
        XbrlJsonFact.fromDict(
            "f", {"value": value, "dimensions": {"concept": "a:b"}}
        ).value
        == text
    )


def test_inf_decimals_is_the_same_as_none() -> None:
    fact = XbrlJsonFact.fromDict(
        "f", {"value": "1", "decimals": "INF", "dimensions": {"concept": "a:b"}}
    )
    assert fact.decimals is None


def test_extension_properties_are_kept() -> None:
    data = copy.deepcopy(DOCUMENT)
    data["ext:report"] = 1
    data["documentInfo"]["ext:info"] = 2
    data["facts"]["f1"]["ext:fact"] = 3
    document = XbrlJsonDocument.fromDict(data)
    assert document.extensions == {"ext:report": 1}
    assert document.documentInfoExtensions == {"ext:info": 2}
    assert document.facts[0].extensions == {"ext:fact": 3}
    assert document.toDict() == data


def test_a_name_resolves_against_the_documents_namespaces() -> None:
    document = XbrlJsonDocument.fromDict(DOCUMENT)
    assert document.resolve("ex:Total") == ("http://example.com/ex", "Total")
    with pytest.raises(FactProblem, match="declared prefix"):
        document.resolve("nope:Total")


# -- periods -------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text, iso",
    [
        ("2026-10-19T24:00:00", "2026-10-20T00:00:00"),
        ("2026-10-19T24:00:00.000", "2026-10-20T00:00:00"),
        ("2024-02-28T24:00:00", "2024-02-29T00:00:00"),
        ("2026-12-31T24:00:00", "2027-01-01T00:00:00"),
        ("2026-10-19T09:30:00.5", "2026-10-19T09:30:00.500000"),
    ],
)
def test_the_end_of_a_day_is_the_start_of_the_next(text: str, iso: str) -> None:
    """Python before 3.14 cannot parse T24:00:00 itself."""
    assert parseDateTime(text).isoformat() == iso


@pytest.mark.parametrize(
    "text, message",
    [
        ("2026-10-19", "not a dateTime"),
        ("2026-10-19T00:00:00Z", "time zone"),
        ("2026-10-19T00:00:00+01:00", "time zone"),
        ("2026-10-19T24:00:01", "not a dateTime"),
        ("2026-02-30T00:00:00", "not a dateTime"),
    ],
)
def test_what_is_not_an_oim_date_time_is_refused(text: str, message: str) -> None:
    with pytest.raises(FactProblem, match=message) as problem:
        parseDateTime(text)
    assert problem.value.code == INVALID_PERIOD


def test_a_duration_must_end_after_it_starts() -> None:
    with pytest.raises(FactProblem, match="end after it starts"):
        parsePeriod("2026-01-01T00:00:00/2025-01-01T00:00:00")


def test_a_report_period_is_a_duration_of_whole_days() -> None:
    with pytest.raises(FactProblem, match="not a duration"):
        parseDuration("2026-01-01T00:00:00")
    with pytest.raises(FactProblem, match="whole days"):
        parseDuration("2025-01-01T00:00:00/2025-06-30T12:00:00")
