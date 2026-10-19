"""ConverterConfig: the typed, parsed-once view of the converter defaults dict."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from mireport.taxonomy import Concept, QName, Taxonomy


@dataclass(frozen=True, slots=True)
class ConverterConfig:
    """Unit and label overrides supplied by the disclosure defaults."""

    dataTypesToUnits: dict[QName, QName] = field(default_factory=dict)
    conceptsToUnits: dict[Concept, QName] = field(default_factory=dict)
    cellValuesToTaxonomyLabels: dict[str, str] = field(default_factory=dict)
    cellUnitReplacements: dict[str, str] = field(default_factory=dict)

    def unitForDataType(self, dataType: QName) -> QName | None:
        """The configured unit for ``dataType``, matched by local name if not by QName: the
        config spans taxonomies, and the UTR qualifies some item types differently from the
        DTR."""
        if (unit := self.dataTypesToUnits.get(dataType)) is not None:
            return unit
        return next(
            (
                unit
                for configured, unit in self.dataTypesToUnits.items()
                if configured.localName == dataType.localName
            ),
            None,
        )

    @classmethod
    def fromDefaults(
        cls, defaults: Mapping[str, Any], taxonomy: Taxonomy
    ) -> ConverterConfig:
        qname = taxonomy.QNameMaker.fromString

        dataTypesToUnits = {
            qname(dataType): qname(unitType)
            for dataType, unitType in defaults.get("dataTypesToUnits", {}).items()
        }

        # Keyed by local name: the config spans taxonomies with different
        # prefixes, and not every taxonomy defines every listed concept.
        conceptsToUnits = {
            concept: qname(unitQname)
            for conceptName, unitQname in defaults.get("conceptsToUnits", {}).items()
            if (concept := taxonomy.resolveConcept(conceptName, by_name=True))
            is not None
        }

        return cls(
            dataTypesToUnits=dataTypesToUnits,
            conceptsToUnits=conceptsToUnits,
            cellValuesToTaxonomyLabels=dict(
                defaults.get("cellValuesToTaxonomyLabels", {})
            ),
            cellUnitReplacements=dict(defaults.get("cellUnitReplacements", {})),
        )
