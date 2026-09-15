from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable, Iterable, Sequence

from mireport.diagnostics import Diagnostic
from mireport.stringutil import normalizeLabelText, stripLabelSuffix
from mireport.taxonomy import (
    STANDARD_LABEL_ROLE,
    Concept,
    HypercubeDeclaration,
    HypercubeType,
    Taxonomy,
)

_FDV_HINT_PREFIX = (
    "XBRL WGN 'Guidance on the use of dimensions' section 3.5 (full "
    "dimensional validity): "
)


def _locations(declarations: Iterable[HypercubeDeclaration]) -> list[str]:
    return [f"{d.roleUri} [{d.hypercube.qname}]" for d in declarations]


class TaxonomyChecker:
    def __init__(self, taxonomy: Taxonomy):
        self.taxonomy = taxonomy

    def reportIssues(self) -> list[Diagnostic]:
        return [
            *self.reportLabelCollisions(),
            *self.reportInconsistentDimensionDomains(),
            *self.reportUnresolvedEnumerationDomains(),
            *self.reportOpenPositiveHypercubes(),
            *self.reportNegativeHypercubesWithoutPositive(),
            *self.reportClosedNegativeHypercubes(),
            *self.reportConceptsWithoutHypercube(),
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
        different (role, hypercube) declarations.

        Taxonomy._lookupDomainByDimension unions the domain across every base
        set a dimension appears in, so getDomainMembersForExplicitDimension()
        can admit member/dimension combinations that were never valid
        together.
        """
        domainsByDimension: dict[
            Concept, dict[tuple[str, Concept], frozenset[Concept]]
        ] = defaultdict(dict)
        for declaration in self.taxonomy.hypercubeDeclarations:
            for eds in declaration.explicitDimensions:
                domainsByDimension[eds.dimension][
                    (declaration.roleUri, declaration.hypercube)
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

    def reportOpenPositiveHypercubes(self) -> list[Diagnostic]:
        """WGN section 3.5, recommendation 1: a positive ("all") hypercube
        should be closed."""
        bad = [
            d
            for d in self.taxonomy.hypercubeDeclarations
            if d.type is HypercubeType.Positive and not d.closed
        ]
        if not bad:
            return []
        return [
            Diagnostic.warning(
                f"{len(bad)} positive hypercube(s) are open",
                concepts=sorted({d.hypercube.qname for d in bad}),
                locations=_locations(bad),
                hint=(
                    _FDV_HINT_PREFIX
                    + "a positive hypercube should be closed, otherwise its "
                    "primary items get no dimensional validation for any "
                    "dimension it does not declare. mireport cannot model an "
                    "open hypercube and ignores it, so its primary items have "
                    "no usable dimensions here either."
                ),
            )
        ]

    def reportClosedNegativeHypercubes(self) -> list[Diagnostic]:
        """WGN section 3.5, recommendation 4: a negative ("notAll") hypercube
        should be open."""
        bad = [
            d
            for d in self.taxonomy.hypercubeDeclarations
            if d.type is HypercubeType.Negative and d.closed
        ]
        if not bad:
            return []
        return [
            Diagnostic.warning(
                f"{len(bad)} negative hypercube(s) are closed",
                concepts=sorted({d.hypercube.qname for d in bad}),
                locations=_locations(bad),
                hint=(
                    _FDV_HINT_PREFIX
                    + "a negative hypercube should be open; xbrldt:closed on a "
                    "negative hypercube has no agreed meaning. mireport cannot "
                    "model a negative hypercube and ignores it."
                ),
            )
        ]

    def reportNegativeHypercubesWithoutPositive(self) -> list[Diagnostic]:
        """WGN section 3.5, recommendation 2: a negative ("notAll") hypercube
        only belongs in a base set that also holds a positive ("all") one --
        on its own it excludes nothing from anything."""
        byRole: dict[str, list[HypercubeDeclaration]] = defaultdict(list)
        for d in self.taxonomy.hypercubeDeclarations:
            byRole[d.roleUri].append(d)
        bad = [
            d
            for declarations in byRole.values()
            if not any(x.type is HypercubeType.Positive for x in declarations)
            for d in declarations
            if d.type is HypercubeType.Negative
        ]
        if not bad:
            return []
        return [
            Diagnostic.warning(
                f"{len(bad)} negative hypercube(s) are in a base set with no "
                "positive hypercube",
                concepts=sorted({d.hypercube.qname for d in bad}),
                locations=_locations(bad),
                hint=(
                    _FDV_HINT_PREFIX + "a negative hypercube only excludes dimension "
                    "combinations from what a positive hypercube in the same "
                    "base set admits; on its own it constrains nothing."
                ),
            )
        ]

    def reportConceptsWithoutHypercube(self) -> list[Diagnostic]:
        """WGN section 3.5, recommendation 3 (via section 3.4): in a taxonomy
        that uses dimensions, every concept should be a primary item of at
        least one hypercube -- even a hypercube with no dimensions.

        A concept in no hypercube gets *no* dimensional validation at all, so
        a fact for it may carry any dimension value, valid or not. Membership
        of an open or negative hypercube still counts here -- it is an
        association, however unhelpfully shaped, and the other three checks
        above cover the shape.
        """
        if not self.taxonomy.hypercubes:
            return []  # a taxonomy that does not use dimensions at all
        covered = {
            concept
            for declaration in self.taxonomy.hypercubeDeclarations
            for concept in declaration.primaryItems
        }
        orphans = sorted(self._reportableConcepts() - covered)
        if not orphans:
            return []
        return [
            Diagnostic.warning(
                f"{len(orphans)} concept(s) are not a primary item of any hypercube",
                concepts=[c.qname for c in orphans],
                hint=(
                    _FDV_HINT_PREFIX
                    + "in a taxonomy that uses dimensions, a concept belonging "
                    "to no hypercube receives no dimensional validation, so a "
                    "fact for it may carry any dimension value at all. "
                    "Associate it with a hypercube -- one with no dimensions "
                    "if it takes none."
                ),
            )
        ]

    def _reportableConcepts(self) -> frozenset[Concept]:
        """Concepts that could be the concept of a fact. XDT requires
        hypercube and dimension items to be declared abstract, so
        isReportable (not abstract) already excludes them -- there is no
        separate hypercube/dimension check to make. Whether a reportable
        concept is also, say, a domain member elsewhere is irrelevant to full
        dimensional validity -- if it appears as no cube's primary item, its
        facts get no dimensional validation, full stop."""
        return frozenset(
            concept for concept in self.taxonomy.concepts if concept.isReportable
        )
