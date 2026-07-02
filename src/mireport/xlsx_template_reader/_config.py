"""ConverterConfig: the typed, parsed-once view of the converter defaults dict."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, NamedTuple

if TYPE_CHECKING:
    from mireport.taxonomy import Concept, QName, Taxonomy


class ComplexUnit(NamedTuple):
    numerator: list[QName]
    denominator: list[QName]


@dataclass(frozen=True, slots=True)
class ConverterConfig:
    """Unit and label overrides supplied by the disclosure defaults."""

    dataTypesToUnits: dict[QName, QName] = field(default_factory=dict)
    unitIdsToMeasures: dict[str, ComplexUnit] = field(default_factory=dict)
    conceptsToUnits: dict[Concept, QName] = field(default_factory=dict)
    cellValuesToTaxonomyLabels: dict[str, str] = field(default_factory=dict)
    cellUnitReplacements: dict[str, str] = field(default_factory=dict)

    @classmethod
    def fromDefaults(cls, defaults: dict, taxonomy: Taxonomy) -> ConverterConfig:
        qname = taxonomy.QNameMaker.fromString

        dataTypesToUnits = {
            qname(dataType): qname(unitType)
            for dataType, unitType in defaults.get("dataTypesToUnits", {}).items()
        }

        unitIdsToMeasures = {
            unitId: ComplexUnit(
                numerator=[
                    q
                    for m in unitDict.get("numerator", [])
                    if (q := taxonomy.UTR.getQNameForUnitId(m)) is not None
                ],
                denominator=[
                    q
                    for m in unitDict.get("denominator", [])
                    if (q := taxonomy.UTR.getQNameForUnitId(m)) is not None
                ],
            )
            for unitId, unitDict in defaults.get("unitIdsToMeasures", {}).items()
        }

        conceptsToUnits = {
            taxonomy.getConcept(conceptQname): qname(unitQname)
            for conceptQname, unitQname in defaults.get("conceptsToUnits", {}).items()
        }

        return cls(
            dataTypesToUnits=dataTypesToUnits,
            unitIdsToMeasures=unitIdsToMeasures,
            conceptsToUnits=conceptsToUnits,
            cellValuesToTaxonomyLabels=dict(
                defaults.get("cellValuesToTaxonomyLabels", {})
            ),
            cellUnitReplacements=dict(defaults.get("cellUnitReplacements", {})),
        )
