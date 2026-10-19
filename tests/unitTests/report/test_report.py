"""Report: a report as data, with nothing about how it looks.

The things a Report holds that InlineReport did not need: nil facts, the prefix its entity's scheme
is written with, and the taxonomy documents it refers to whether or not any were added.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import date
from typing import Any

import pytest
from tests.unitTests.synthetic_taxonomy import concept, qname, taxonomy_json

from mireport.exceptions import InlineReportException
from mireport.report import InlineReport, Report
from mireport.report.duplicates import DuplicateClass, duplicateClass
from mireport.report.factbuilder import (
    EMPTY_ENUMERATION_SET_TEXT,
    FactBuilder,
    enumerationText,
)
from mireport.taxonomy import Taxonomy
from mireport.xml import LEI_NS

ENTRY_POINT = "test://synthetic/report"


def _taxonomy_document() -> dict[str, Any]:
    concepts = {
        qname("Amount"): concept("Amount", numeric=True),
        qname("Text"): concept("Text"),
        qname("Fixed"): concept("Fixed"),
    }
    for name in (qname("Amount"), qname("Text")):
        concepts[name]["nillable"] = True
    return taxonomy_json(ENTRY_POINT, concepts=concepts, presentation={})


@pytest.fixture
def taxonomy(register_taxonomy: Callable[[dict[str, Any]], Taxonomy]) -> Taxonomy:
    return register_taxonomy(_taxonomy_document())


def _report(cls: type[Report], taxonomy: Taxonomy) -> Report:
    report = cls(taxonomy)
    report.addDurationPeriod("cur", date(2025, 1, 1), date(2025, 12, 31))
    report.setDefaultPeriodName("cur")
    return report


@pytest.fixture
def report(taxonomy: Taxonomy) -> Report:
    return _report(Report, taxonomy)


def _builder(report: Report, name: str) -> FactBuilder:
    return FactBuilder(report).setConcept(report.taxonomy.getConcept(qname(name)))


def _pure(report: Report) -> Any:
    return report.taxonomy.QNameMaker.fromString("xbrli:pure")


class TestNil:
    def test_a_nil_fact_has_no_value(self, report: Report) -> None:
        fact = _builder(report, "Text").setNil().buildFact()
        assert fact.isNil
        assert fact.value is None

    def test_a_nil_number_keeps_its_unit_but_not_its_decimals(
        self, report: Report
    ) -> None:
        fact = (
            _builder(report, "Amount")
            .setSimpleUnit(_pure(report))
            .setDecimals(2)
            .setNil()
            .buildFact()
        )
        assert fact.isNil
        assert fact.unit is not None and str(fact.unit) == "xbrli:pure"
        assert fact.decimals is None

    def test_a_nil_number_still_needs_a_unit(self, report: Report) -> None:
        with pytest.raises(InlineReportException, match="require a unit"):
            _builder(report, "Amount").setNil().buildFact()

    def test_a_concept_that_is_not_nillable_cannot_be_nil(self, report: Report) -> None:
        with pytest.raises(InlineReportException, match="cannot be nil"):
            _builder(report, "Fixed").setNil().buildFact()

    def test_a_value_after_nil_makes_the_fact_not_nil(self, report: Report) -> None:
        fact = _builder(report, "Text").setNil().setValue("x").buildFact()
        assert not fact.isNil and fact.value == "x"

    def test_a_copied_nil_fact_stays_nil(self, report: Report) -> None:
        original = _builder(report, "Text").setNil().buildFact()
        assert FactBuilder.fromFact(report, original).buildFact().isNil

    def test_two_nils_are_complete_duplicates_and_a_nil_and_a_value_are_not(
        self, report: Report
    ) -> None:
        nil, otherNil = (_builder(report, "Text").setNil().buildFact() for _ in "ab")
        valued = _builder(report, "Text").setValue("x").buildFact()
        assert duplicateClass(nil, otherNil) is DuplicateClass.COMPLETE
        assert duplicateClass(nil, valued) is DuplicateClass.INCONSISTENT

    def test_an_inline_report_will_not_render_a_nil_fact(
        self, taxonomy: Taxonomy
    ) -> None:
        report = _report(InlineReport, taxonomy)
        report.addFact(_builder(report, "Text").setNil().buildFact())
        with pytest.raises(InlineReportException, match="nil facts"):
            report.getInlineReport()  # type: ignore[attr-defined]


class TestEntity:
    def test_an_lei_is_written_with_the_lei_prefix(self, report: Report) -> None:
        report.setEntity(LEI_NS, "529900T8BM49AURSDO55")
        assert report.entityPrefix == "lei"

    def test_any_other_scheme_is_written_with_the_entity_prefix(
        self, report: Report
    ) -> None:
        report.setEntity("http://example.com/ids", "ID1")
        assert report.entityPrefix == "entity"

    def test_a_prefix_given_is_the_one_used(self, report: Report) -> None:
        report.setEntity("http://example.com/ids", "ID1", prefix="ex")
        assert report.entityPrefix == "ex"


class TestSchemaRefs:
    def test_with_none_added_it_is_the_taxonomys_entry_point(
        self, report: Report
    ) -> None:
        assert report.schemaRefs == (ENTRY_POINT,)

    def test_added_ones_are_kept_in_the_order_they_were_added(
        self, report: Report
    ) -> None:
        for ref in ("test://b", "test://a", "test://b"):
            report.addSchemaRef(ref)
        assert report.schemaRefs == ("test://b", "test://a")


def test_a_report_is_data_so_it_has_no_output_locale(report: Report) -> None:
    with pytest.raises(InlineReportException, match="only an InlineReport"):
        _ = report.outputLocale


def test_an_enumeration_reads_as_its_members_labels(taxonomy: Taxonomy) -> None:
    members = [taxonomy.getConcept(qname(n)) for n in ("Text", "Amount")]
    assert enumerationText(members) == "Text\nAmount"
    assert enumerationText([]) == EMPTY_ENUMERATION_SET_TEXT
