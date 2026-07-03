"""Characterization tests pinning FactCreator behaviour before decomposition.

The snapshot test captures every fact (concept, value, aspects) produced from
the 1.2.0 sample, so any refactor that changes a value, unit, dimension or
period — not just the fact count — fails loudly. Regenerate the snapshot by
running this module directly:

    python tests/unitTests/xlsx_template_reader/test_fact_creator_characterization.py
"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import NamedTuple

import pytest

from mireport.conversionresults import ConversionResultsBuilder
from mireport.data.disclosures import VSME_DEFAULTS
from mireport.report import InlineReport
from mireport.taxonomy import getTaxonomy, loadBuiltInTaxonomyJSON
from mireport.xlsx_template_reader._binder import WorkbookBinder
from mireport.xlsx_template_reader._config import ConverterConfig
from mireport.xlsx_template_reader._enumerations import (
    LabelMatch,
    eeDomainByLabel,
    getClosestEEMemberMatch,
    resolveMemberByLabel,
)
from mireport.xlsx_template_reader._fact_creator import FactCreator
from mireport.xlsx_template_reader._fact_support import processNumeric
from mireport.xlsx_template_reader._messages import Messenger
from mireport.xlsx_template_reader._reader import WorkbookReader
from mireport.xlsx_template_reader._units import UnitResolver, cleanUnitTextFromExcel
from mireport.xlsx_template_reader.processor import XlsxProcessor
from mireport.xlsx_template_reader.util import loadExcelFromPathOrFileLike

_TESTS_DIR = Path(__file__).parent.parent.parent
_REPO_ROOT = _TESTS_DIR.parent
SAMPLE_1_2_0 = _TESTS_DIR / "data" / "VSME-Digital-Template-Sample-1.2.0.xlsx"
SAMPLE_1_3_0 = (
    _REPO_ROOT / "digital-templates" / "VSME-Digital-Template-Sample-1.3.0.xlsx"
)
_SNAPSHOT_DIR = Path(__file__).parent / "snapshots"

# sample workbook -> snapshot file. 1.3.0 is the only sample with footnotes.
SNAPSHOT_CASES = {
    SAMPLE_1_2_0: _SNAPSHOT_DIR / "vsme-1.2.0-facts.json",
    SAMPLE_1_3_0: _SNAPSHOT_DIR / "vsme-1.3.0-facts.json",
}

MAX_VALUE_LENGTH = 80


def _results() -> ConversionResultsBuilder:
    return ConversionResultsBuilder(consoleOutput=False)


def _canonicalFacts(report: InlineReport) -> list[dict]:
    entries = []
    for fact in report.facts:
        value = str(fact.value)
        if len(value) > MAX_VALUE_LENGTH:
            value = f"{value[:MAX_VALUE_LENGTH]}…[{len(str(fact.value))} chars]"
        entry = {
            "concept": str(fact.concept.qname),
            "value": value,
            "aspects": {str(k): str(v) for k, v in fact.aspects.items()},
        }
        if fact.footnotes:
            entry["footnotes"] = sorted(
                str(fn.content)[:MAX_VALUE_LENGTH] for fn in fact.footnotes
            )
        entries.append(entry)
    entries.sort(key=lambda e: json.dumps(e, sort_keys=True, ensure_ascii=False))
    return entries


def _snapshotDocument(sample: Path) -> dict:
    results = _results()
    report = XlsxProcessor.from_file(sample, results, VSME_DEFAULTS).createReport()
    severities = Counter(m.severity.name for m in results.messages)
    return {
        "facts": _canonicalFacts(report),
        "messageSeverities": dict(sorted(severities.items())),
    }


@pytest.mark.slow
class TestFactSnapshot:
    @pytest.mark.parametrize(
        "sample,snapshot",
        SNAPSHOT_CASES.items(),
        ids=[p.stem for p in SNAPSHOT_CASES],
    )
    def test_facts_match_snapshot(self, sample: Path, snapshot: Path):
        assert sample.is_file(), f"Missing sample workbook {sample}"
        assert snapshot.is_file(), (
            f"Missing snapshot {snapshot}. Generate it by running this module "
            "directly, then review and commit it."
        )
        expected = json.loads(snapshot.read_text(encoding="utf-8"))
        actual = _snapshotDocument(sample)

        assert actual["messageSeverities"] == expected["messageSeverities"]

        expected_facts = expected["facts"]
        actual_facts = actual["facts"]
        # Compare pairwise for a readable diff before falling back to counts.
        for exp, act in zip(expected_facts, actual_facts):
            assert act == exp
        assert len(actual_facts) == len(expected_facts)


# ---------------------------------------------------------------------------
# In-process FactCreator fixture (mirrors the production wiring in processor.py).
# Built on the current shipped template (1.3.0); these tests synthesize their
# own cells and discover concepts from the taxonomy, so unlike the snapshots
# they don't depend on specific workbook content.
# ---------------------------------------------------------------------------


class CreatorEnv(NamedTuple):
    creator: FactCreator
    report: InlineReport
    bindings: object
    taxonomy: object
    reader: WorkbookReader
    results: ConversionResultsBuilder


@pytest.fixture(scope="module")
def creator_env():
    wb = loadExcelFromPathOrFileLike(SAMPLE_1_3_0)
    results = _results()
    reader = WorkbookReader(wb, results)

    entry_point = reader.value(VSME_DEFAULTS["entryPoint"]).as_str()
    taxonomy = getTaxonomy(entry_point)
    report = InlineReport(taxonomy, None)
    report.addSchemaRef(entry_point)

    for period in VSME_DEFAULTS.get("periods", []):
        start = reader.value(period["start"]).as_date()
        end = reader.value(period["end"]).as_date()
        if report.addDurationPeriod(period["name"], start, end):
            report.setDefaultPeriodName(period["name"])

    bindings = WorkbookBinder(reader, taxonomy, results).bind()
    creator = FactCreator(bindings, reader, report, results, VSME_DEFAULTS)
    yield CreatorEnv(creator, report, bindings, taxonomy, reader, results)
    wb.close()


@pytest.fixture(scope="module")
def taxonomy(creator_env):
    return creator_env.taxonomy


@pytest.fixture(scope="module")
def unit_resolver(creator_env):
    config = ConverterConfig.fromDefaults(VSME_DEFAULTS, creator_env.taxonomy)
    return UnitResolver(
        creator_env.report,
        config,
        Messenger(creator_env.results),
        creator_env.reader,
        creator_env.bindings.unit_map,
    )


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------


class TestCleanUnitTextFromExcel:
    def test_applies_replacements(self):
        assert cleanUnitTextFromExcel("m3", {"m3": "m^3"}) == "m^3"

    def test_no_replacements_is_identity(self):
        assert cleanUnitTextFromExcel("kg", {}) == "kg"


class TestEEDomainByLabel:
    def test_rejects_non_enumeration_concept(self, taxonomy):
        not_ee = next(
            c
            for c in sorted(taxonomy.concepts, key=str)
            if not (c.isEnumerationSet or c.isEnumerationSingle)
        )
        with pytest.raises(ValueError):
            eeDomainByLabel(not_ee)

    def test_maps_member_labels_to_members(self, taxonomy):
        ee = next(
            c
            for c in sorted(taxonomy.concepts, key=str)
            if c.isEnumerationSingle and c.getEEDomain()
        )
        domain_by_label = eeDomainByLabel(ee)
        assert domain_by_label
        member = ee.getEEDomain()[0]
        label = member.getStandardLabel()
        assert domain_by_label[label][0] == member


class TestGetClosestEEMemberMatch:
    @pytest.fixture(scope="class")
    def ee_concept(self, taxonomy):
        return next(
            c
            for c in sorted(taxonomy.concepts, key=str)
            if c.isEnumerationSingle and c.getEEDomain()
        )

    def test_close_typo_matches(self, ee_concept):
        member = ee_concept.getEEDomain()[0]
        label = member.getStandardLabel()
        result = getClosestEEMemberMatch(ee_concept, label + " x")
        assert result is not None
        assert result[0] == member

    def test_garbage_returns_none(self, ee_concept):
        assert getClosestEEMemberMatch(ee_concept, "zzz qqq 12345 xyzzy") is None


class TestResolveMemberByLabel:
    """The one implementation of the exact -> configured-alias -> closest-match
    label chain that used to be pasted across FactCreator."""

    @pytest.fixture(scope="class")
    def config(self, taxonomy):
        return ConverterConfig.fromDefaults(VSME_DEFAULTS, taxonomy)

    @pytest.fixture(scope="class")
    def ee_concept(self, taxonomy):
        return next(
            c
            for c in sorted(taxonomy.concepts, key=str)
            if c.isEnumerationSingle and c.getEEDomain()
        )

    def test_exact_label(self, taxonomy, config, ee_concept):
        member = ee_concept.getEEDomain()[0]
        label = member.getStandardLabel()
        match = resolveMemberByLabel(taxonomy, config, label)
        assert match == LabelMatch(member, False, None)

    def test_configured_alias(self, taxonomy, config):
        # Find an alias whose cell value doesn't resolve directly but whose
        # target label does (some configured aliases target older taxonomy
        # labels that no longer exist).
        usable = next(
            (
                (cell_value, target)
                for cell_value, label in config.cellValuesToTaxonomyLabels.items()
                if taxonomy.getConceptForLabel(cell_value) is None
                and (target := taxonomy.getConceptForLabel(label)) is not None
            ),
            None,
        )
        if usable is None:
            pytest.skip("no configured alias resolves against this taxonomy")
        cell_value, expected = usable
        match = resolveMemberByLabel(taxonomy, config, cell_value)
        assert match is not None
        assert match.concept == expected
        assert match.viaConfiguredAlias is True

    def test_closest_match_needs_ee_concept(self, taxonomy, config, ee_concept):
        member = ee_concept.getEEDomain()[0]
        label = member.getStandardLabel()
        typo = label + " x"
        # Without the EE concept there is no fuzzy fallback.
        assert resolveMemberByLabel(taxonomy, config, typo) is None
        match = resolveMemberByLabel(taxonomy, config, typo, ee_concept=ee_concept)
        assert match is not None
        assert match.concept == member
        assert match.closestLabel is not None

    def test_no_match_returns_none(self, taxonomy, config):
        assert resolveMemberByLabel(taxonomy, config, "zzz qqq 12345") is None


# ---------------------------------------------------------------------------
# Unit resolution chain
# ---------------------------------------------------------------------------


def _makeCell(value, number_format=None):
    from openpyxl import Workbook

    wb = Workbook()
    cell = wb.active.cell(row=1, column=1)
    cell.value = value
    if number_format:
        cell.number_format = number_format
    return cell


class TestGetSimpleUnit:
    @pytest.fixture(scope="class")
    def any_holder(self, creator_env):
        return next(iter(creator_env.bindings.concept_map.values()))

    def test_direct_unit_id(self, unit_resolver, any_holder):
        unit = unit_resolver.getSimpleUnit(any_holder, _makeCell("MWh"))
        assert unit is not None and str(unit).endswith("MWh")

    def test_parenthesised_unit_id(self, unit_resolver, any_holder):
        unit = unit_resolver.getSimpleUnit(
            any_holder, _makeCell("Megawatt hours (MWh)")
        )
        assert unit is not None and str(unit).endswith("MWh")

    def test_unknown_unit_returns_none(self, unit_resolver, any_holder):
        assert (
            unit_resolver.getSimpleUnit(any_holder, _makeCell("wibbles per parsec"))
            is None
        )

    def test_empty_cell_returns_none(self, unit_resolver, any_holder):
        assert unit_resolver.getSimpleUnit(any_holder, _makeCell(None)) is None


class TestSetFallbackUnitForName:
    def test_non_numeric_concept_returns_false(
        self, creator_env, unit_resolver, taxonomy
    ):
        concept = next(
            c
            for c in sorted(taxonomy.concepts, key=str)
            if c.isReportable and not c.isNumeric
        )
        fb = creator_env.report.getFactBuilder().setConcept(concept)
        assert unit_resolver.setFallbackUnitForName(None, concept, fb) is False

    def test_numeric_concept_gets_a_unit(self, creator_env, unit_resolver, taxonomy):
        concept = next(
            c
            for c in sorted(taxonomy.concepts, key=str)
            if c.isReportable and c.isNumeric and not c.isMonetary
        )
        fb = creator_env.report.getFactBuilder().setConcept(concept)

        class FakeDn:
            name = "test_range"

        assert unit_resolver.setFallbackUnitForName(FakeDn(), concept, fb) is True
        assert "units" in fb._aspects


class TestProcessNumeric:
    def test_decimals_from_number_format(self, creator_env, taxonomy):
        report, bindings = creator_env.report, creator_env.bindings
        holder = next(iter(bindings.concept_map.values()))
        concept = next(
            c
            for c in sorted(taxonomy.concepts, key=str)
            if c.isReportable
            and c.isNumeric
            and c.dataType.localName != "percentItemType"
        )
        fb = report.getFactBuilder().setConcept(concept)
        processNumeric(
            Messenger(creator_env.results),
            holder,
            _makeCell(12.345, "0.00"),
            fb,
            12.345,
        )
        assert fb._aspects.get("decimals") == "2"

    def test_plain_format_means_inf_decimals(self, creator_env, taxonomy):
        report, bindings = creator_env.report, creator_env.bindings
        holder = next(iter(bindings.concept_map.values()))
        concept = next(
            c
            for c in sorted(taxonomy.concepts, key=str)
            if c.isReportable
            and c.isNumeric
            and c.dataType.localName != "percentItemType"
        )
        fb = report.getFactBuilder().setConcept(concept)
        processNumeric(
            Messenger(creator_env.results), holder, _makeCell(12, "General"), fb, 12
        )
        assert fb._aspects.get("decimals") == "INF"


if __name__ == "__main__":
    loadBuiltInTaxonomyJSON()
    _SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
    for sample, snapshot in SNAPSHOT_CASES.items():
        snapshot.write_text(
            json.dumps(_snapshotDocument(sample), indent=1, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        print(f"Snapshot written to {snapshot}")
