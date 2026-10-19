"""Tests for the middle of UnitResolver.setUnitForName's resolution chain:

    unit named range (direct/parenthesised/config-corrected text)
    -> per-concept config override -> taxonomy required units
    -> data-type/UTR fallbacks, where a type with a complex UTR unit (emissions per monetary
    unit) takes a divide unit

The endpoints (getSimpleUnit, setFallbackUnitForName) are covered in
test_fact_creator_characterization.py; these tests pin the branching between
them, which nothing else exercises directly.
"""

import pytest
from openpyxl import Workbook
from openpyxl.utils.cell import absolute_coordinate, quote_sheetname
from openpyxl.workbook.defined_name import DefinedName

from mireport.conversionresults import ConversionResultsBuilder, Severity
from mireport.data.disclosures import VSME_DEFAULTS
from mireport.report import InlineReport
from mireport.report.model import Unit
from mireport.taxonomy import getTaxonomy, listTaxonomies
from mireport.xlsx_template_reader._config import ConverterConfig
from mireport.xlsx_template_reader._messages import Messenger
from mireport.xlsx_template_reader._ranges import XbrlConceptCellRangeMetadata
from mireport.xlsx_template_reader._reader import WorkbookReader
from mireport.xlsx_template_reader._units import UnitResolver

_SHEET = "S"


@pytest.fixture(scope="module")
def taxonomy():
    entry_point = next(
        ep for ep in listTaxonomies() if any("vsme" in d.lower() for d in ep)
    )
    return getTaxonomy(entry_point)


@pytest.fixture(scope="module")
def config(taxonomy):
    return ConverterConfig.fromDefaults(VSME_DEFAULTS, taxonomy)


@pytest.fixture(scope="module")
def energy_concept(taxonomy):
    """A reportable numeric concept whose data type accepts MWh but not kg."""
    mwh = taxonomy.QNameMaker.fromString("utr:MWh")
    kg = taxonomy.QNameMaker.fromString("utr:kg")
    concept = next(
        (
            c
            for c in sorted(taxonomy.concepts, key=str)
            if c.isReportable
            and c.isNumeric
            and taxonomy.UTR.valid(c.dataType, mwh)
            and not taxonomy.UTR.valid(c.dataType, kg)
        ),
        None,
    )
    if concept is None:
        pytest.skip("taxonomy has no MWh-but-not-kg concept")
    return concept


class _Env:
    """A concept cell (A1) and unit cell (B1) wired through a real reader."""

    def __init__(self, taxonomy, config, concept, unit_text, *, with_unit_map=True):
        wb = Workbook()
        ws = wb.active
        assert ws is not None
        ws.title = _SHEET
        ws["A1"] = 123
        ws["B1"] = unit_text
        for name, ref in (("c", "A1"), ("c_unit", "B1")):
            attr = f"{quote_sheetname(_SHEET)}!{absolute_coordinate(ref)}"
            wb.defined_names[name] = DefinedName(name, attr_text=attr)

        self.results = ConversionResultsBuilder(consoleOutput=False)
        reader = WorkbookReader(wb, self.results)

        def crm(name):
            resolved = reader.peekRange(wb.defined_names[name])
            assert resolved is not None
            return XbrlConceptCellRangeMetadata.fromCellRangeMetadata(resolved, concept)

        self.holder = crm("c")
        self.unit_holder = crm("c_unit")
        self.report = InlineReport(taxonomy, None)
        unit_map = {concept: self.unit_holder} if with_unit_map else {}
        self.resolver = UnitResolver(
            self.report, config, Messenger(self.results), reader, unit_map
        )
        self.fb = self.report.getFactBuilder().setConcept(concept)

    def warnings(self):
        return [
            str(m.messageText)
            for m in self.results.messages
            if m.severity is Severity.WARNING
        ]


class TestUnitFromNamedRange:
    def test_valid_unit_for_data_type_is_used(self, taxonomy, config, energy_concept):
        env = _Env(taxonomy, config, energy_concept, "MWh")
        assert env.resolver.setUnitForName(env.holder, env.fb) is True
        assert env.fb._unit is not None and not env.fb._unit.is_divide
        assert env.fb._unit.measure.localName == "MWh"

    def test_wrong_unit_via_specified_holder_warns_and_fails(
        self, taxonomy, config, energy_concept
    ):
        """Table path: a shared-with-nobody unit range with a wrong-type unit
        means the fact cannot be created."""
        env = _Env(taxonomy, config, energy_concept, "kg")
        ok = env.resolver.setUnitForName(
            env.holder,
            env.fb,
            specifiedUnitHolder=env.unit_holder,
            sharedRange=False,
        )
        assert ok is False
        assert any("not matching data type" in w for w in env.warnings())

    def test_wrong_unit_via_shared_range_fails_silently(
        self, taxonomy, config, energy_concept
    ):
        """A unit range shared between primary items legitimately holds units
        that fit only some of them: no warning for the misfits."""
        env = _Env(taxonomy, config, energy_concept, "kg")
        ok = env.resolver.setUnitForName(
            env.holder,
            env.fb,
            specifiedUnitHolder=env.unit_holder,
            sharedRange=True,
        )
        assert ok is False
        assert not any("not matching data type" in w for w in env.warnings())

    def test_wrong_unit_via_unit_map_falls_back(self, taxonomy, config, energy_concept):
        """Simple-fact path: a wrong-type unit in the unit range falls through
        to the data-type/UTR fallback instead of failing."""
        env = _Env(taxonomy, config, energy_concept, "kg")
        assert env.resolver.setUnitForName(env.holder, env.fb) is True
        assert env.fb._unit is not None
        assert env.fb._unit.measure.localName != "kg"

    def test_empty_unit_cell_fails(self, taxonomy, config, energy_concept):
        env = _Env(taxonomy, config, energy_concept, None)
        assert env.resolver.setUnitForName(env.holder, env.fb) is False

    def test_empty_unit_cell_warns_the_fact_is_dropped(
        self, taxonomy, config, energy_concept
    ):
        env = _Env(taxonomy, config, energy_concept, None)
        env.resolver.setUnitForName(env.holder, env.fb)
        assert any("no unit" in w.lower() for w in env.warnings())


class TestConfiguredConceptUnit:
    def test_unresolvable_text_uses_concepts_to_units_override(self, taxonomy, config):
        concept, unit = next(iter(config.conceptsToUnits.items()))
        assert taxonomy.UTR.valid(concept.dataType, unit), (
            "config override must be valid for this test to be meaningful"
        )
        env = _Env(taxonomy, config, concept, "wibbles per parsec")
        assert env.resolver.setUnitForName(env.holder, env.fb) is True
        assert env.fb._unit == Unit.simple(unit)


class TestNoUnitRange:
    def test_single_required_unit_is_used(self, taxonomy, config):
        concept = next(
            (
                c
                for c in sorted(taxonomy.concepts, key=str)
                if c.isReportable
                and c.isNumeric
                and (units := c.getRequiredUnitQNames()) is not None
                and len(units) == 1
            ),
            None,
        )
        if concept is None:
            pytest.skip("taxonomy has no concept with exactly one required unit")
        env = _Env(taxonomy, config, concept, None, with_unit_map=False)
        assert env.resolver.setUnitForName(env.holder, env.fb) is True
        assert env.fb._unit == Unit.simple(next(iter(concept.getRequiredUnitQNames())))

    @pytest.fixture(scope="class")
    def monetary_concept(self, taxonomy):
        return next(
            c
            for c in sorted(taxonomy.concepts, key=str)
            if c.isReportable and c.isMonetary and c.getRequiredUnitQNames() is None
        )

    def test_monetary_concept_takes_the_report_currency(
        self, taxonomy, config, monetary_concept
    ):
        """A table row with no currency cell: the report's currency, not one of the
        UTR's ~180."""
        env = _Env(taxonomy, config, monetary_concept, None, with_unit_map=False)
        env.report.setDefaultCurrency("GBP")
        assert env.resolver.setUnitForName(env.holder, env.fb) is True
        assert env.fb._unit == Unit.simple(env.report.defaultCurrency)

    def test_monetary_concept_without_a_default_currency_creates_no_fact(
        self, taxonomy, config, monetary_concept
    ):
        env = _Env(taxonomy, config, monetary_concept, None, with_unit_map=False)
        assert env.resolver.setUnitForName(env.holder, env.fb) is False
        assert env.fb._unit is None
        assert any("no default currency" in w for w in env.warnings())

    @pytest.fixture(scope="class")
    def per_monetary_concept(self, taxonomy):
        concept = next(
            (
                c
                for c in sorted(taxonomy.concepts, key=str)
                if c.isReportable
                and c.isNumeric
                and c.getRequiredUnitQNames() is None
                and taxonomy.UTR.requiresComplexUnit(c.dataType)
                and taxonomy.UTR.complexUnitFor(c.dataType).denominator.localName
                == "monetaryItemType"
            ),
            None,
        )
        if concept is None:
            pytest.skip("taxonomy has no per-monetary complex-unit concept")
        return concept

    def test_complex_unit_uses_default_currency_denominator(
        self, taxonomy, config, per_monetary_concept
    ):
        env = _Env(taxonomy, config, per_monetary_concept, None, with_unit_map=False)
        env.report.setDefaultCurrency("EUR")
        assert env.resolver.setUnitForName(env.holder, env.fb) is True
        assert env.fb._unit is not None and env.fb._unit.is_divide
        assert [m.localName for m in env.fb._unit.denominator] == ["EUR"]
        assert taxonomy.UTR.validComplexUnit(
            per_monetary_concept.dataType,
            env.fb._unit.numerator[0],
            env.fb._unit.denominator[0],
        )

    def test_complex_unit_without_a_default_currency_creates_no_fact(
        self, taxonomy, config, per_monetary_concept
    ):
        env = _Env(taxonomy, config, per_monetary_concept, None, with_unit_map=False)
        assert env.resolver.setUnitForName(env.holder, env.fb) is False
        assert env.fb._unit is None
        assert any("no default currency" in w for w in env.warnings())

    def test_a_single_unit_in_the_cell_is_not_valid_for_a_complex_type(
        self, taxonomy, config, per_monetary_concept
    ):
        """A named unit typed into the unit cell cannot be valid for a per-unit type; the
        fact still gets the divide unit rather than that unit or xbrli:pure."""
        env = _Env(taxonomy, config, per_monetary_concept, "tCO2e")
        env.report.setDefaultCurrency("EUR")
        assert env.resolver.setUnitForName(env.holder, env.fb) is True
        assert env.fb._unit is not None and env.fb._unit.is_divide

    @pytest.mark.parametrize("table", [False, True])
    def test_a_numerator_unit_in_the_cell_is_used_as_the_numerator(
        self, taxonomy, config, per_monetary_concept, table
    ):
        utr = taxonomy.UTR
        sides = utr.complexUnitFor(per_monetary_concept.dataType)
        default = config.unitForDataType(sides.numerator)
        other = min(u for u in utr.getUnitsForDataType(sides.numerator) if u != default)
        env = _Env(taxonomy, config, per_monetary_concept, str(other))
        env.report.setDefaultCurrency("EUR")
        kwargs = {"specifiedUnitHolder": env.unit_holder} if table else {}
        assert env.resolver.setUnitForName(env.holder, env.fb, **kwargs) is True
        assert env.fb._unit == Unit.divide([other], [env.report.defaultCurrency])

    def test_a_cell_unit_for_neither_side_is_ignored(
        self, taxonomy, config, per_monetary_concept
    ):
        env = _Env(taxonomy, config, per_monetary_concept, "MWh")
        env.report.setDefaultCurrency("EUR")
        assert env.resolver.setUnitForName(env.holder, env.fb) is True
        sides = taxonomy.UTR.complexUnitFor(per_monetary_concept.dataType)
        assert env.fb._unit == Unit.divide(
            [config.unitForDataType(sides.numerator)], [env.report.defaultCurrency]
        )

    def test_a_side_with_no_unit_says_which(self, taxonomy, per_monetary_concept):
        """With no configured unit, the emissions side has many UTR units to choose from."""
        env = _Env(
            taxonomy,
            ConverterConfig(),
            per_monetary_concept,
            None,
            with_unit_map=False,
        )
        env.report.setDefaultCurrency("EUR")
        assert env.resolver.setUnitForName(env.holder, env.fb) is False
        sides = taxonomy.UTR.complexUnitFor(per_monetary_concept.dataType)
        assert any(
            f"no unit is configured for {sides.numerator.localName}" in w
            and "currency" not in w
            for w in env.warnings()
        )
