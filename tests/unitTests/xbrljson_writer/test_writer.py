"""toXbrlJson(): any Report as an xBRL-JSON document, in canonical form, that the reader reads back.

The report here is built by hand over the synthetic taxonomy, so each test says exactly which facts
the document should hold.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import date
from decimal import Decimal
from typing import Any

import pytest
from tests.unitTests.synthetic_taxonomy import (
    GROUP_ENTRY_POINT,
    NAMESPACE,
    concept,
    group_taxonomy_json,
    qname,
)

from mireport.conversionresults import ConversionResultsBuilder
from mireport.report import Report
from mireport.report.factbuilder import FactBuilder
from mireport.report.model import Unit
from mireport.taxonomy import Concept, Taxonomy
from mireport.xbrljson_reader import XbrlJsonProcessor
from mireport.xbrljson_reader.document import XbrlJsonException, XbrlJsonFact
from mireport.xbrljson_writer import XBRL_NS, XbrlJsonExtensions, toXbrlJson
from mireport.xbrljson_writer._values import canonicalNumber
from mireport.xml import LEI_NS

LEI = "529900T8BM49AURSDO55"


def _taxonomy_document() -> dict[str, Any]:
    document = group_taxonomy_json()
    extra = {
        qname("Count"): concept("Count", numeric=True),
        qname("Share"): concept("Share", numeric=True),
        qname("Money"): concept("Money", numeric=True),
        qname("Flag"): concept("Flag"),
        qname("Opening"): concept("Opening", numeric=True, periodType="instant"),
    }
    for name, kind in (
        (qname("Count"), "xbrli:integerItemType"),
        (qname("Money"), "xbrli:monetaryItemType"),
        (qname("Flag"), "xbrli:booleanItemType"),
    ):
        extra[name]["dataType"] = extra[name]["baseDataType"] = kind
    extra[qname("Share")]["dataType"] = "dtr:percentItemType"
    document["namespaces"]["dtr"] = "http://www.xbrl.org/dtr/type/2022-03-31"
    document["concepts"].update(extra)
    for entry in document["concepts"].values():
        entry["nillable"] = True
    return document


@pytest.fixture
def taxonomy(register_taxonomy: Callable[[dict[str, Any]], Taxonomy]) -> Taxonomy:
    return register_taxonomy(_taxonomy_document())


@pytest.fixture
def report(taxonomy: Taxonomy) -> Report:
    report = Report(taxonomy)
    report.setEntity(LEI_NS, LEI)
    report.addDurationPeriod("cur", date(2025, 1, 1), date(2025, 12, 31))
    report.setDefaultPeriodName("cur")
    return report


def _c(report: Report, name: str) -> Concept:
    return report.taxonomy.getConcept(qname(name))


def _fact(report: Report, name: str) -> FactBuilder:
    return report.getFactBuilder().setConcept(_c(report, name))


def _pure(report: Report) -> Unit:
    return Unit.simple(report.taxonomy.QNameMaker.fromString("xbrli:pure"))


def _add(report: Report, fb: FactBuilder) -> None:
    report.addFact(fb.buildFact())


def _only(report: Report) -> XbrlJsonFact:
    (fact,) = toXbrlJson(report).facts
    return fact


class TestValues:
    @pytest.mark.parametrize(
        "name,value,expected",
        [
            ("Total", 10, "10.0"),
            ("Total", 12.5, "12.5"),
            ("Total", -0.0, "0.0"),
            ("Count", 3, "3"),
        ],
    )
    def test_a_number_is_written_canonically_for_its_type(
        self, report: Report, name: str, value: float, expected: str
    ) -> None:
        _add(report, _fact(report, name).setValue(value).setUnit(_pure(report)))
        assert _only(report).value == expected

    def test_a_percentage_is_its_xbrl_fraction(self, report: Report) -> None:
        fb = _fact(report, "Share").setUnit(_pure(report))
        _add(report, fb.setPercentageFact(Decimal("0.125"), 3))
        fact = _only(report)
        assert (fact.value, fact.decimals) == ("0.125", 3)

    def test_a_boolean_is_true_or_false(self, report: Report) -> None:
        _add(report, _fact(report, "Flag").setValue("TRUE"))
        assert _only(report).value == "true"

    def test_text_is_as_given(self, report: Report) -> None:
        _add(report, _fact(report, "Orphan").setValue("Some text"))
        assert _only(report).value == "Some text"

    def test_a_nil_fact_is_null_and_keeps_its_unit(self, report: Report) -> None:
        eur = report.taxonomy.QNameMaker.fromString("iso4217:EUR")
        _add(report, _fact(report, "Money").setSimpleUnit(eur).setNil())
        fact = _only(report)
        assert (fact.value, fact.decimals, fact.unit) == (None, None, "iso4217:EUR")

    def test_inf_decimals_are_left_out(self, report: Report) -> None:
        fb = _fact(report, "Total").setValue(1).setUnit(_pure(report))
        _add(report, fb.setDecimals("INF"))
        assert _only(report).decimals is None

    @pytest.mark.parametrize(
        "number,expected",
        [("125", "1.25E2"), ("-0.5", "-5.0E-1"), ("0", "0.0E0"), ("1", "1.0E0")],
    )
    def test_a_double_has_an_exponent(self, number: str, expected: str) -> None:
        class Double:
            qname = "x:Ratio"

            class baseDataType:
                localName = "doubleItemType"

        assert canonicalNumber(Decimal(number), Double) == expected  # type: ignore[arg-type]


class TestAspects:
    def test_core_dimensions(self, report: Report) -> None:
        _add(report, _fact(report, "Total").setValue(1).setUnit(_pure(report)))
        fact = _only(report)
        assert fact.concept == qname("Total")
        assert fact.entity == f"lei:{LEI}"
        assert fact.period == "2025-01-01T00:00:00/2026-01-01T00:00:00"

    def test_an_instant_is_written_as_one(self, report: Report) -> None:
        _add(report, _fact(report, "Opening").setValue(1).setUnit(_pure(report)))
        assert _only(report).period == "2026-01-01T00:00:00"

    def test_a_dimensionless_number_has_no_unit_and_no_xbrli_prefix(
        self, report: Report
    ) -> None:
        _add(report, _fact(report, "Total").setValue(1).setUnit(_pure(report)))
        assert _only(report).unit is None
        assert "xbrli" not in toXbrlJson(report).namespaces

    def test_a_divide_unit_is_a_unit_string(self, report: Report) -> None:
        maker = report.taxonomy.QNameMaker
        tonnes = report.taxonomy.UTR.getQNameForUnitId("t")
        assert tonnes is not None
        unit = Unit.divide(tonnes, maker.fromString("iso4217:EUR"))
        _add(report, _fact(report, "Total").setValue(1).setUnit(unit))
        assert _only(report).unit == "utr:t/iso4217:EUR"
        assert {"utr", "iso4217"} <= toXbrlJson(report).namespaces.keys()

    def test_taxonomy_dimensions_in_the_order_the_fact_has_them(
        self, report: Report
    ) -> None:
        fb = (
            _fact(report, "Emissions")
            .setValue(1)
            .setUnit(_pure(report))
            .setExplicitDimension(_c(report, "ScopeAxis"), _c(report, "ScopeB"))
        )
        _add(report, fb)
        _add(
            report,
            _fact(report, "Note")
            .setValue("x")
            .setTypedDimension(_c(report, "TargetAxis"), "T1"),
        )
        emissions, note = toXbrlJson(report).facts
        assert emissions.dimensions == {qname("ScopeAxis"): qname("ScopeB")}
        assert note.dimensions == {qname("TargetAxis"): "T1"}


class TestDocument:
    def test_an_empty_report_declares_only_the_xbrl_prefix(
        self, report: Report
    ) -> None:
        document = toXbrlJson(report)
        assert document.facts == ()
        assert document.namespaces == {"xbrl": XBRL_NS}
        assert document.taxonomy == (GROUP_ENTRY_POINT,)
        assert document.features == {"xbrl:canonicalValues": True}

    def test_namespaces_are_those_used_sorted(self, report: Report) -> None:
        _add(report, _fact(report, "Total").setValue(1).setUnit(_pure(report)))
        assert toXbrlJson(report).namespaces == {
            "lei": LEI_NS,
            "syn": NAMESPACE,
            "xbrl": XBRL_NS,
        }

    def test_a_given_entity_prefix_is_used(self, report: Report) -> None:
        report.setEntity("http://example.com/ids", "ID1", prefix="ex")
        _add(report, _fact(report, "Orphan").setValue("x"))
        document = toXbrlJson(report)
        assert document.facts[0].entity == "ex:ID1"
        assert document.namespaces["ex"] == "http://example.com/ids"

    def test_one_prefix_for_two_namespaces_is_refused(self, report: Report) -> None:
        report.setEntity("http://example.com/ids", "ID1", prefix="syn")
        _add(report, _fact(report, "Orphan").setValue("x"))
        with pytest.raises(XbrlJsonException, match="'syn'"):
            toXbrlJson(report)

    def test_facts_are_in_report_order_unless_sorted(self, report: Report) -> None:
        for name in ("Orphan", "Note", "Flag"):
            fb = _fact(report, name).setValue("true")
            if name == "Note":
                fb.setTypedDimension(_c(report, "TargetAxis"), "T1")
            _add(report, fb)
        assert [f.concept for f in toXbrlJson(report).facts] == [
            qname(n) for n in ("Orphan", "Note", "Flag")
        ]
        written = toXbrlJson(report, sortFacts=True).facts
        assert [f.concept for f in written] == [
            qname(n) for n in ("Flag", "Note", "Orphan")
        ]
        assert [f.id for f in written] == ["f1", "f2", "f3"]

    def test_a_footnote_is_a_note_its_facts_link_to(self, report: Report) -> None:
        for name in ("Orphan", "Flag"):
            _add(report, _fact(report, name).setValue("true"))
        report.addFootnoteToFacts("A <b>note</b>", report.facts)
        orphan, flag, note = toXbrlJson(report).facts
        assert orphan.links == flag.links == {"footnote": {"_": ("n1",)}}
        assert note.isNote and note.id == note.noteId == "n1"
        assert note.value == "A &lt;b&gt;note&lt;/b&gt;"
        assert note.language == report.language

    def test_extensions_are_written_with_their_namespace(self, report: Report) -> None:
        _add(report, _fact(report, "Orphan").setValue("x"))
        document = toXbrlJson(
            report,
            extensions=XbrlJsonExtensions(
                namespaces={"ex": "urn:example"},
                document={"ex:made": "today"},
                fact=lambda f: {"ex:concept": f.concept.qname.localName},
            ),
        )
        assert document.namespaces["ex"] == "urn:example"
        assert document.extensions == {"ex:made": "today"}
        assert document.facts[0].extensions == {"ex:concept": "Orphan"}


def test_what_is_written_the_reader_reads_back(report: Report) -> None:
    unit = _pure(report)
    _add(report, _fact(report, "Total").setValue(12.5).setDecimals(1).setUnit(unit))
    _add(report, _fact(report, "Count").setValue(3).setDecimals(0).setUnit(unit))
    _add(report, _fact(report, "Flag").setValue(True))
    _add(report, _fact(report, "Orphan").setNil())
    _add(
        report,
        _fact(report, "Emissions")
        .setValue(7)
        .setUnit(unit)
        .setExplicitDimension(_c(report, "ScopeAxis"), _c(report, "ScopeA")),
    )
    document = toXbrlJson(report)
    processor = XbrlJsonProcessor(
        document, ConversionResultsBuilder(consoleOutput=False), strict=True
    )
    read = processor.createReport()
    assert processor.factsAdded == len(document.facts)
    assert toXbrlJson(read).facts == document.facts
