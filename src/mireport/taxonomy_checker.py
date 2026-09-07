from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable, Sequence

from mireport.diagnostics import Diagnostic
from mireport.stringutil import normalizeLabelText, stripLabelSuffix
from mireport.taxonomy import STANDARD_LABEL_ROLE, Concept, Taxonomy


class TaxonomyChecker:
    def __init__(self, taxonomy: Taxonomy):
        self.taxonomy = taxonomy

    def reportIssues(self) -> list[Diagnostic]:
        return [
            *self.reportLabelCollisions(),
            *self.reportInconsistentDimensionDomains(),
            *self.reportUnresolvedEnumerationDomains(),
        ]

    def reportLabelCollisions(self) -> list[Diagnostic]:
        def _find_collisions(
            lookup: dict[str, frozenset[Concept]],
        ) -> dict[frozenset[Concept], list[str]]:
            bad: dict[frozenset[Concept], list[str]] = defaultdict(list)
            for bad_label, concepts in lookup.items():
                if len(concepts) > 1:
                    bad[concepts].append(bad_label)
            return bad

        def _apply_transforms(
            label: str, transforms: Sequence[Callable[[str], str]]
        ) -> set[str]:
            """Return the set of transformed versions of a label (including intermediates)."""
            results: set[str] = set()
            current = label
            for fn in transforms:
                current = fn(current)
                results.add(current)
            return results

        def _is_suffix_only_collision(
            bad_label: str, concepts: frozenset[Concept]
        ) -> bool:
            """Return True if the collision only exists because of suffix stripping."""
            matching: set[Concept] = set()
            for concept in concepts:
                for labelsByRole in concept._labels.values():
                    actual = labelsByRole.get(STANDARD_LABEL_ROLE)
                    if actual is None:
                        continue
                    norm = normalizeLabelText(actual)
                    if norm == bad_label or norm.lower() == bad_label:
                        matching.add(concept)
            return len(matching) < 2

        def _collect_collisions(
            heading: str,
            lookup: dict[str, frozenset[Concept]],
            label_transforms: Sequence[Callable[[str], str]] = (),
            skip_suffix_collisions: bool = False,
        ) -> list[Diagnostic]:
            bad = _find_collisions(lookup)
            diagnostics: list[Diagnostic] = []

            for concepts, bad_labels in bad.items():
                if skip_suffix_collisions:
                    bad_labels = [
                        lbl
                        for lbl in bad_labels
                        if not _is_suffix_only_collision(lbl, concepts)
                    ]
                    if not bad_labels:
                        continue
                langs_for_label: dict[str, str] = {}
                for bad_label in bad_labels:
                    lang_codes: list[str] = []
                    for concept in concepts:
                        for lang, labelsByRole in concept._labels.items():
                            actual = labelsByRole.get(STANDARD_LABEL_ROLE)
                            if actual is None:
                                continue
                            if actual == bad_label or (
                                label_transforms
                                and bad_label
                                in _apply_transforms(actual, label_transforms)
                            ):
                                lang_codes.append(lang)
                    unique_langs = set(lang_codes)
                    if len(unique_langs) == 1:
                        langs_for_label[bad_label] = next(iter(unique_langs))
                    else:
                        langs_for_label[bad_label] = ", ".join(sorted(unique_langs))

                diagnostics.append(
                    Diagnostic.warning(
                        heading,
                        concepts=tuple(c.qname for c in sorted(concepts)),
                        labels=[
                            f"{lbl} [{langs_for_label[lbl]}]"
                            for lbl in sorted(bad_labels)
                        ],
                    )
                )
            return diagnostics

        return [
            *_collect_collisions(
                "More than one concept has the same standard label",
                self.taxonomy._lookupConceptsByStandardLabel,
            ),
            *_collect_collisions(
                "More than one concept has the same normalised standard label",
                self.taxonomy._lookupConceptsByPretendLabel,
                label_transforms=[normalizeLabelText, stripLabelSuffix, str.lower],
                skip_suffix_collisions=True,
            ),
        ]

    def reportInconsistentDimensionDomains(self) -> list[Diagnostic]:
        """Warn when an explicit dimension is given different domains in
        different (role, hypercube) signatures.

        Taxonomy._lookupDomainByDimension unions the domain across every base
        set a dimension appears in, so getDomainMembersForExplicitDimension()
        can admit member/dimension combinations that were never valid
        together.
        """
        domainsByDimension: dict[
            Concept, dict[tuple[str, Concept], frozenset[Concept]]
        ] = defaultdict(dict)
        for signature in self.taxonomy.dimensionSignatures:
            for eds in signature.explicitDimensions:
                domainsByDimension[eds.dimension][
                    (signature.roleUri, signature.hypercube)
                ] = eds.domain

        diagnostics: list[Diagnostic] = []
        for dimension, domainsByLocation in domainsByDimension.items():
            if len({domain for domain in domainsByLocation.values()}) < 2:
                continue
            domainLines = [
                f"{roleUri} [{hypercube.qname}]: "
                f"{', '.join(sorted(str(m.qname) for m in domain))}"
                for (roleUri, hypercube), domain in sorted(
                    domainsByLocation.items(),
                    key=lambda kv: (kv[0][0], str(kv[0][1].qname)),
                )
            ]
            diagnostics.append(
                Diagnostic.warning(
                    "Explicit dimension is given different domains in different hypercubes",
                    concepts=(dimension.qname,),
                    domains=domainLines,
                    hint=(
                        "getDomainMembersForExplicitDimension() unions these "
                        "domains, so it can admit member combinations that "
                        "were never valid together."
                    ),
                )
            )
        return diagnostics

    def reportUnresolvedEnumerationDomains(self) -> list[Diagnostic]:
        """Warn for an enum2 concept whose extensible-enumeration domain
        resolved to no members -- catches a taxonomy baked before
        extraction-time enum2 validation existed."""
        diagnostics: list[Diagnostic] = []
        for concept in sorted(self.taxonomy.concepts):
            if not (concept.isEnumerationSingle or concept.isEnumerationSet):
                continue
            if concept.getEEDomain():
                continue
            diagnostics.append(
                Diagnostic.warning(
                    "Extensible enumeration concept has no resolved domain members",
                    concepts=(concept.qname,),
                )
            )
        return diagnostics
