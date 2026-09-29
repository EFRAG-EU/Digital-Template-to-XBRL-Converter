from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable, Iterable, Sequence
from itertools import chain

from mireport.diagnostics import Diagnostic
from mireport.stringutil import normalizeLabelText, stripLabelSuffix
from mireport.taxonomy import (
    MEASUREMENT_GUIDANCE_LABEL_ROLE,
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

_ENGLISH_LANGUAGE = "en"


def _locations(declarations: Iterable[HypercubeDeclaration]) -> list[str]:
    return [f"{d.roleUri} [{d.hypercube.qname}]" for d in declarations]


class TaxonomyChecker:
    def __init__(self, taxonomy: Taxonomy):
        self.taxonomy = taxonomy

    def reportIssues(self) -> list[Diagnostic]:
        checks: tuple[Callable[[], list[Diagnostic]], ...] = (
            self.reportLabelCollisions,
            self.reportInconsistentDimensionDomains,
            self.reportUnresolvedEnumerationDomains,
            self.reportOpenPositiveHypercubes,
            self.reportNegativeHypercubesWithoutPositive,
            self.reportClosedNegativeHypercubes,
            self.reportConceptsWithoutHypercube,
            self.reportConceptsWithoutStandardLabel,
            self.reportConceptsMissingExpectedLanguageLabels,
            self.reportConceptsWithoutReferences,
            self.reportNumericConceptsWithoutMeasurementGuidance,
            self.reportMeasurementGuidanceNotResolvingToAUnit,
            self.reportMeasurementGuidanceOnNonNumericConcepts,
        )
        return list(chain.from_iterable(check() for check in checks))

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

    def reportConceptsWithoutStandardLabel(self) -> list[Diagnostic]:
        """Every concept should have an English standard label -- VSME's
        authoritative language regardless of which language happens to have
        the most labels in a given build (see Taxonomy.defaultLanguage,
        which this deliberately does not use), and the label every other
        label role and every UI surface falls back to.

        Concept.getStandardLabel() does BCP-47 base-language matching, so an
        "en-GB"-only label still counts."""
        bad = sorted(
            concept
            for concept in self.taxonomy.concepts
            if concept.getStandardLabel(_ENGLISH_LANGUAGE) is None
        )
        if not bad:
            return []
        return [
            Diagnostic.warning(
                f"{len(bad)} concept(s) have no English standard label",
                concepts=[c.qname for c in bad],
            )
        ]

    def reportConceptsMissingExpectedLanguageLabels(self) -> list[Diagnostic]:
        """Beyond the hard English requirement above, flag a concept that
        falls short of its own namespace's translation effort: if a strict
        majority of a namespace's concepts (grouped by qname prefix, e.g.
        "vsme" vs. an external code list like "nace") carry a standard label
        in some language, that language is "expected" there, and any
        concept in that namespace lacking it is notable.

        A namespace nobody has translated at all (0% in every language)
        raises nothing here -- this is about consistency of an existing
        translation effort, not a mandate to start one."""
        conceptsByPrefix: dict[str, list[Concept]] = defaultdict(list)
        for concept in self.taxonomy.concepts:
            conceptsByPrefix[concept.qname.prefix].append(concept)

        candidateLanguages = self.taxonomy.supportedLanguages - {_ENGLISH_LANGUAGE}
        missingByLanguage: dict[str, list[Concept]] = defaultdict(list)
        for concepts in conceptsByPrefix.values():
            total = len(concepts)
            for lang in candidateLanguages:
                covered = {
                    concept
                    for concept in concepts
                    if concept.getStandardLabel(lang) is not None
                }
                if len(covered) * 2 <= total:
                    continue  # not a majority language for this namespace
                missingByLanguage[lang].extend(
                    concept for concept in concepts if concept not in covered
                )

        diagnostics: list[Diagnostic] = []
        for lang, missing in sorted(missingByLanguage.items()):
            if not missing:
                continue
            ordered = sorted(missing)
            diagnostics.append(
                Diagnostic.warning(
                    f"{len(ordered)} concept(s) are missing a standard label "
                    f"in {lang!r}, though most concepts in their namespace "
                    "have one",
                    concepts=[c.qname for c in ordered],
                )
            )
        return diagnostics

    def reportConceptsWithoutReferences(self) -> list[Diagnostic]:
        """Every reportable concept should be backed by at least one
        reference (authoritative literature, disclosure guidance, etc.).
        Abstract concepts (hypercubes, dimensions, domain members, headings)
        are excluded -- they are structural, not reportable, and routinely
        carry no reference of their own."""
        bad = sorted(
            concept for concept in self._reportableConcepts() if not concept.references
        )
        if not bad:
            return []
        return [
            Diagnostic.warning(
                f"{len(bad)} reportable concept(s) have no references",
                concepts=[c.qname for c in bad],
            )
        ]

    def reportNumericConceptsWithoutMeasurementGuidance(self) -> list[Diagnostic]:
        """A reportable, numeric, non-monetary concept whose data type has at
        least one candidate unit in the UTR should carry a
        measurementGuidance label naming it -- Concept.getRequiredUnitQNames()
        reads that label to pick the concept's unit. A data type with zero
        UTR candidates is not flagged: there is nothing a label could name.

        This still applies with exactly one candidate today, even though
        UnitResolver already auto-resolves that case (see
        UnitResolver.unitFor) -- the UTR can grow a second candidate for the
        same data type in a later release, at which point resolution becomes
        ambiguous with no guidance in place to catch it. Naming today's only
        candidate now is cheap and forward-compatible; each finding lists the
        concept's actual current candidate(s) so it's actionable without
        reaching for the UTR yourself.
        """
        diagnostics: list[Diagnostic] = []
        for concept in sorted(self._reportableConcepts()):
            if (
                not concept.isNumeric
                or concept.isMonetary
                or MEASUREMENT_GUIDANCE_LABEL_ROLE in concept.labelRoles
            ):
                continue
            candidates = self.taxonomy.UTR.getUnitsForDataType(concept.dataType)
            if not candidates:
                continue
            candidateNames = sorted(str(u) for u in candidates)
            if len(candidateNames) == 1:
                text = (
                    "Concept has no measurementGuidance label; the UTR's "
                    f"only candidate unit for its data type is {candidateNames[0]}"
                )
            else:
                text = (
                    "Concept has no measurementGuidance label; the UTR's "
                    f"candidate units for its data type are {', '.join(candidateNames)}"
                )
            diagnostics.append(
                Diagnostic.warning(
                    text,
                    concepts=(concept.qname,),
                    candidateUnits=candidateNames,
                    hint=(
                        "Concept.getRequiredUnitQNames() reads this label to "
                        "pick the concept's unit; without it, unit "
                        "resolution picks an arbitrary UTR candidate once "
                        "there is more than one."
                    ),
                )
            )
        return diagnostics

    def reportMeasurementGuidanceNotResolvingToAUnit(self) -> list[Diagnostic]:
        """A measurementGuidance label should resolve to one of the UTR's
        candidate units for the concept's data type --
        Concept.getRequiredUnitQNames() returns None if it doesn't, whether
        from a typo, an unresolvable QName, or text that names no valid unit
        at all. Only checked when the data type has UTR candidates to
        resolve to -- otherwise there is nothing the label could match, see
        reportNumericConceptsWithoutMeasurementGuidance()."""
        bad = sorted(
            concept
            for concept in self._reportableConcepts()
            if concept.isNumeric
            and MEASUREMENT_GUIDANCE_LABEL_ROLE in concept.labelRoles
            and self.taxonomy.UTR.getUnitsForDataType(concept.dataType)
            and concept.getRequiredUnitQNames() is None
        )
        if not bad:
            return []
        return [
            Diagnostic.warning(
                f"{len(bad)} concept(s) have a measurementGuidance label that "
                "does not resolve to a valid unit",
                concepts=[c.qname for c in bad],
                hint=(
                    "Concept.getRequiredUnitQNames() could not match this "
                    "label's text against any of the UTR's candidate units "
                    "for the concept's data type; check for a typo or an "
                    "unregistered unit reference."
                ),
            )
        ]

    def reportMeasurementGuidanceOnNonNumericConcepts(self) -> list[Diagnostic]:
        """A measurementGuidance label only makes sense on a numeric concept
        -- getRequiredUnitQNames() ignores it for any other concept."""
        bad = sorted(
            concept
            for concept in self.taxonomy.concepts
            if not concept.isNumeric
            and MEASUREMENT_GUIDANCE_LABEL_ROLE in concept.labelRoles
        )
        if not bad:
            return []
        return [
            Diagnostic.warning(
                f"{len(bad)} non-numeric concept(s) have a measurementGuidance label",
                concepts=[c.qname for c in bad],
                hint=(
                    "measurementGuidance is only read for numeric concepts; "
                    "on a non-numeric concept it is ignored."
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
