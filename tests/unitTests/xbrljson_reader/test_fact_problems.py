"""What the reader does with a fact it cannot take: skip that fact, say why (with the OIM error
code where there is one), and carry on with the rest of the document.

Each test bends one fact of the VSME coverage report, which the reader otherwise takes whole.
"""

from __future__ import annotations

import copy
from collections.abc import Callable
from typing import Any

import pytest

from mireport.conversionresults import ConversionResultsBuilder, MessageType, Severity
from mireport.coverage_report_generator._sampling import SampleEntityPeriod
from mireport.coverage_report_generator.report_writer import buildCoverageReportSet
from mireport.taxonomy import (
    Concept,
    Taxonomy,
    getTaxonomy,
    listTaxonomies,
    loadBuiltInTaxonomyJSON,
)
from mireport.xbrljson_reader import XbrlJsonProcessor
from mireport.xbrljson_reader.document import XbrlJsonException

SAMPLE = SampleEntityPeriod(
    entity="lei:529900T8BM49AURSDO55",
    entityPrefix="lei",
    entityNamespace="http://standards.iso.org/iso/17442",
    periodInstant="2026-01-01T00:00:00",
    periodDuration="2025-01-01T00:00:00/2026-01-01T00:00:00",
)


@pytest.fixture(scope="module")
def taxonomy() -> Taxonomy:
    if not listTaxonomies():
        loadBuiltInTaxonomyJSON()
    return getTaxonomy(
        next(ep for ep in listTaxonomies() if any("vsme" in uri.lower() for uri in ep))
    )


@pytest.fixture(scope="module")
def values(taxonomy: Taxonomy) -> dict[str, Any]:
    reports = buildCoverageReportSet(taxonomy, SAMPLE, sorted(taxonomy.entryPointSet))
    return reports.documents["all-values.json"].toDict()


@pytest.fixture
def doc(values: dict[str, Any]) -> dict[str, Any]:
    return copy.deepcopy(values)


def _concept(taxonomy: Taxonomy, fact: dict[str, Any]) -> Concept:
    return taxonomy.getConcept(fact["dimensions"]["concept"])


def _first(
    doc: dict[str, Any], taxonomy: Taxonomy, test: Callable[[Concept, dict], bool]
) -> tuple[str, dict[str, Any]]:
    return next(
        (factId, fact)
        for factId, fact in doc["facts"].items()
        if test(_concept(taxonomy, fact), fact)
    )


def _read(
    doc: dict[str, Any], *, strict: bool = False
) -> tuple[XbrlJsonProcessor, ConversionResultsBuilder]:
    results = ConversionResultsBuilder(consoleOutput=False)
    processor = XbrlJsonProcessor(doc, results, strict=strict)
    processor.createReport()
    return processor, results


def _skipped(doc: dict[str, Any], factId: str) -> tuple[XbrlJsonProcessor, str]:
    """Read the document; exactly ``factId`` is skipped. Returns the message about it."""
    processor, results = _read(doc)
    assert processor.factsSkipped == 1
    assert processor.factsAdded == len(doc["facts"]) - 1
    (message,) = [
        str(m.messageText)
        for m in results.messages
        if m.severity is Severity.WARNING and f"Fact {factId} " in str(m.messageText)
    ]
    return processor, message


def test_a_badly_shaped_fact_costs_only_that_fact(doc: dict[str, Any]) -> None:
    doc["facts"]["broken"] = {"value": "1"}
    _, message = _skipped(doc, "broken")
    assert "xbrlje:invalidJSONStructure" in message


def test_a_bad_period_costs_only_that_fact(
    doc: dict[str, Any], taxonomy: Taxonomy
) -> None:
    factId, fact = _first(doc, taxonomy, lambda c, f: "/" in f["dimensions"]["period"])
    fact["dimensions"]["period"] = "2025-01-01T00:00:00/2025-06-30T12:00:00"
    _, message = _skipped(doc, factId)
    assert "whole days" in message and "oime:invalidPeriodRepresentation" in message


def test_a_period_with_a_time_zone_is_skipped_not_a_crash(
    doc: dict[str, Any], taxonomy: Taxonomy
) -> None:
    factId, fact = _first(
        doc, taxonomy, lambda c, f: "/" not in f["dimensions"]["period"]
    )
    fact["dimensions"]["period"] = "2026-01-01T00:00:00Z"
    _, message = _skipped(doc, factId)
    assert "time zone" in message


def test_a_fact_with_no_period_is_skipped(
    doc: dict[str, Any], taxonomy: Taxonomy
) -> None:
    factId, fact = _first(doc, taxonomy, lambda c, f: True)
    del fact["dimensions"]["period"]
    _, message = _skipped(doc, factId)
    assert "no period" in message


@pytest.mark.parametrize("value", ["Infinity", "NaN", "twelve"])
def test_a_number_must_be_a_finite_number(
    doc: dict[str, Any], taxonomy: Taxonomy, value: str
) -> None:
    factId, fact = _first(
        doc,
        taxonomy,
        lambda c, f: c.isNumeric and not c.isMonetary and "unit" in f["dimensions"],
    )
    fact["value"] = value
    _skipped(doc, factId)


def test_a_number_is_kept_exact(doc: dict[str, Any], taxonomy: Taxonomy) -> None:
    _factId, fact = _first(
        doc,
        taxonomy,
        lambda c, f: (
            c.isNumeric
            and not c.isMonetary
            and c.dataType.localName != "percentItemType"
        ),
    )
    fact["value"] = "123456789012345678"
    processor, _ = _read(doc, strict=True)
    (read,) = [
        f
        for f in processor.report.facts
        if str(f.concept.qname) == fact["dimensions"]["concept"]
        and f.value == 123456789012345678
    ]
    assert isinstance(read.value, int)


@pytest.mark.parametrize(
    "text, flag", [("true", True), ("1", True), ("false", False), ("0", False)]
)
def test_a_boolean_is_any_xs_boolean(
    doc: dict[str, Any], taxonomy: Taxonomy, text: str, flag: bool
) -> None:
    _factId, fact = _first(doc, taxonomy, lambda c, f: c.isBoolean)
    fact["value"] = text
    processor, _ = _read(doc, strict=True)
    assert any(f.concept.isBoolean and f.value is flag for f in processor.report.facts)


def test_anything_else_is_not_a_boolean(
    doc: dict[str, Any], taxonomy: Taxonomy
) -> None:
    factId, fact = _first(doc, taxonomy, lambda c, f: c.isBoolean)
    fact["value"] = "yes"
    _, message = _skipped(doc, factId)
    assert "not a boolean" in message


def test_a_monetary_fact_needs_a_currency(
    doc: dict[str, Any], taxonomy: Taxonomy
) -> None:
    factId, fact = _first(doc, taxonomy, lambda c, f: c.isMonetary)
    del fact["dimensions"]["unit"]
    _, message = _skipped(doc, factId)
    assert "currency" in message and "oime:invalidUnitStringRepresentation" in message


def test_a_unit_the_utr_does_not_allow_is_skipped(
    doc: dict[str, Any], taxonomy: Taxonomy
) -> None:
    kg = taxonomy.QNameMaker.fromString("utr:kg")
    factId, fact = _first(
        doc,
        taxonomy,
        lambda c, f: (
            c.isNumeric
            and "/" not in f["dimensions"].get("unit", "/")
            and not c.isMonetary
            and taxonomy.UTR.getUnitsForDataType(c.dataType)
            and not taxonomy.UTR.valid(c.dataType, kg)
        ),
    )
    fact["dimensions"]["unit"] = "utr:kg"
    _, message = _skipped(doc, factId)
    assert "UTR does not allow" in message


def test_an_enumeration_member_must_be_in_its_domain(
    doc: dict[str, Any], taxonomy: Taxonomy
) -> None:
    factId, fact = _first(doc, taxonomy, lambda c, f: c.isEnumerationSingle)
    domain = set(_concept(taxonomy, fact).getEEDomain())
    outsider = next(
        c
        for c in sorted(taxonomy.concepts, key=str)
        if c.qname.prefix == "vsme" and c not in domain
    )
    fact["value"] = str(outsider.qname)
    _, message = _skipped(doc, factId)
    assert "not in the domain" in message


def test_an_enumeration_set_names_each_member_once(
    doc: dict[str, Any], taxonomy: Taxonomy
) -> None:
    factId, fact = _first(doc, taxonomy, lambda c, f: c.isEnumerationSet and f["value"])
    member = fact["value"].split()[0]
    fact["value"] = f"{member} {member}"
    _, message = _skipped(doc, factId)
    assert "twice" in message


def test_an_explicit_dimension_at_its_default_is_refused(
    doc: dict[str, Any], taxonomy: Taxonomy
) -> None:
    def hasDefaultedAxis(c: Concept, f: dict) -> bool:
        return any(
            taxonomy.getDimensionDefault(taxonomy.getConcept(name)) is not None
            for name in f["dimensions"]
            if ":" in name and taxonomy.getConcept(name).isExplicitDimension
        )

    factId, fact = _first(doc, taxonomy, hasDefaultedAxis)
    name = next(
        n
        for n in fact["dimensions"]
        if ":" in n
        and taxonomy.getConcept(n).isExplicitDimension
        and taxonomy.getDimensionDefault(taxonomy.getConcept(n)) is not None
    )
    default = taxonomy.getDimensionDefault(taxonomy.getConcept(name))
    assert default is not None
    fact["dimensions"][name] = str(default.qname)
    _, message = _skipped(doc, factId)
    assert "default" in message and "oime:invalidDimensionValue" in message


def test_a_nil_fact_with_decimals_is_skipped(
    doc: dict[str, Any], taxonomy: Taxonomy
) -> None:
    factId, fact = _first(doc, taxonomy, lambda c, f: c.isNumeric and "decimals" in f)
    fact["value"] = None
    _, message = _skipped(doc, factId)
    assert "nil fact has no decimals" in message


def test_a_nil_monetary_fact_still_needs_its_currency(
    doc: dict[str, Any], taxonomy: Taxonomy
) -> None:
    factId, fact = _first(doc, taxonomy, lambda c, f: c.isMonetary)
    fact["value"] = None
    del fact["decimals"]
    del fact["dimensions"]["unit"]
    _, message = _skipped(doc, factId)
    assert "currency" in message


def test_a_nil_typed_dimension_value_is_skipped_not_read_as_none(
    doc: dict[str, Any], taxonomy: Taxonomy
) -> None:
    def typedAxis(f: dict) -> str | None:
        return next(
            (
                n
                for n in f["dimensions"]
                if ":" in n and taxonomy.getConcept(n).isTypedDimension
            ),
            None,
        )

    factId, fact = _first(doc, taxonomy, lambda c, f: typedAxis(f) is not None)
    axis = typedAxis(fact)
    assert axis is not None
    fact["dimensions"][axis] = None
    _, message = _skipped(doc, factId)
    assert "nil value" in message


def test_a_fact_in_another_language_is_skipped(
    doc: dict[str, Any], taxonomy: Taxonomy
) -> None:
    factId, fact = _first(
        doc,
        taxonomy,
        lambda c, f: (
            not c.isNumeric
            and not c.isEnumerationSingle
            and not c.isEnumerationSet
            and not c.isBoolean
            and not c.isTextblock
        ),
    )
    fact["dimensions"]["language"] = "fr"
    _, message = _skipped(doc, factId)
    assert "in fr" in message


def test_a_fact_in_the_reports_language_is_read(
    doc: dict[str, Any], taxonomy: Taxonomy
) -> None:
    _, fact = _first(
        doc,
        taxonomy,
        lambda c, f: (
            not c.isNumeric
            and not c.isEnumerationSingle
            and not c.isEnumerationSet
            and not c.isBoolean
            and not c.isTextblock
        ),
    )
    fact["dimensions"]["language"] = "en-GB"
    processor, _ = _read(doc, strict=True)
    assert processor.factsSkipped == 0


def test_decimals_on_a_non_numeric_fact_are_refused(
    doc: dict[str, Any], taxonomy: Taxonomy
) -> None:
    factId, fact = _first(doc, taxonomy, lambda c, f: not c.isNumeric)
    fact["decimals"] = 2
    _, message = _skipped(doc, factId)
    assert "only for numeric" in message


def test_strict_mode_says_how_many_facts_failed_for_which_reason(
    doc: dict[str, Any], taxonomy: Taxonomy
) -> None:
    doc["facts"]["broken"] = {"value": "1"}
    _factId, fact = _first(doc, taxonomy, lambda c, f: c.isMonetary)
    del fact["dimensions"]["unit"]
    with pytest.raises(
        XbrlJsonException,
        match=r"2 of \d+ facts could not be added \(1 oime:invalidUnitStringRepresentation, "
        r"1 xbrlje:invalidJSONStructure\)",
    ):
        _read(doc, strict=True)


def test_a_document_with_no_facts_is_an_empty_report(doc: dict[str, Any]) -> None:
    doc["facts"] = {}
    processor, _ = _read(doc, strict=True)
    assert processor.report.facts == []


def test_reading_twice_counts_once(doc: dict[str, Any]) -> None:
    doc["facts"]["broken"] = {"value": "1"}
    processor, _ = _read(doc)
    processor.createReport()
    assert processor.factsSkipped == 1
    assert processor.factsAdded == len(doc["facts"]) - 1


def test_what_is_not_acted_on_is_said(doc: dict[str, Any]) -> None:
    doc["documentInfo"]["features"]["ex:shiny"] = True
    doc["documentInfo"]["namespaces"]["ex"] = "http://example.com/ex"
    next(iter(doc["facts"].values()))["ex:colour"] = "blue"
    _processor, results = _read(doc, strict=True)
    devInfo = [
        str(m.messageText)
        for m in results.messages
        if m.messageType is MessageType.DevInfo
    ]
    assert any("ex:shiny" in m for m in devInfo)
    assert any("ex:colour" in m for m in devInfo)


def test_footnotes_are_read_from_note_facts(
    doc: dict[str, Any], taxonomy: Taxonomy
) -> None:
    doc["documentInfo"]["namespaces"]["xbrl"] = "https://xbrl.org/2021"
    ids = list(doc["facts"])[:2]
    for factId in ids:
        doc["facts"][factId]["links"] = {"footnote": {"_": ["n1"]}}
    doc["facts"]["n1"] = {
        "value": "<p>Restated.</p>",
        "dimensions": {"concept": "xbrl:note", "noteId": "n1", "language": "en"},
    }
    processor, _ = _read(doc, strict=True)
    footnoted = [f for f in processor.report.facts if f.footnotes]
    assert len(footnoted) == 2
    assert {str(fn.content) for f in footnoted for fn in f.footnotes} == {
        "<p>Restated.</p>"
    }


def test_a_snapshot_scale_is_honoured(doc: dict[str, Any], taxonomy: Taxonomy) -> None:
    doc["documentInfo"]["namespaces"]["mireport"] = "urn:mireport:xbrl-json-extension"
    _factId, fact = _first(
        doc,
        taxonomy,
        lambda c, f: (
            c.isNumeric
            and not c.isMonetary
            and c.dataType.localName != "percentItemType"
        ),
    )
    fact["value"] = "1500"
    fact["mireport:scale"] = 3
    processor, results = _read(doc, strict=True)
    (read,) = [
        f
        for f in processor.report.facts
        if str(f.concept.qname) == fact["dimensions"]["concept"] and f.scale == 3
    ]
    assert read.value == 1.5
    assert not [m for m in results.messages if "mireport:scale" in str(m.messageText)]
