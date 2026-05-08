from collections import defaultdict
from typing import Callable, Sequence

from mireport.stringutil import normalizeLabelText, stripLabelSuffix
from mireport.taxonomy import STANDARD_LABEL_ROLE, Concept, Taxonomy


class TaxonomyChecker:
    def __init__(self, taxonomy: Taxonomy):
        self.taxonomy = taxonomy

    def reportIssues(self) -> None:
        self.reportLabelCollisions()

    def reportLabelCollisions(self) -> None:
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

        def _print_collisions(
            heading: str,
            lookup: dict[str, frozenset[Concept]],
            label_transforms: Sequence[Callable[[str], str]] = (),
            skip_suffix_collisions: bool = False,
        ) -> None:
            bad = _find_collisions(lookup)

            print(heading)
            if not bad:
                print("✅ No collisions found.")
                return

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
                            if actual == bad_label:
                                lang_codes.append(lang)
                            elif label_transforms:
                                if bad_label in _apply_transforms(
                                    actual, label_transforms
                                ):
                                    lang_codes.append(lang)
                    unique_langs = set(lang_codes)
                    if len(unique_langs) == 1:
                        langs_for_label[bad_label] = next(iter(unique_langs))
                    else:
                        langs_for_label[bad_label] = ", ".join(sorted(unique_langs))

                print(
                    "❎ More than one concept with the same label:",
                    "\n".join(map(str, sorted(concepts))),
                    "Labels",
                    "\n".join(
                        f"{lbl} [{langs_for_label[lbl]}]" for lbl in sorted(bad_labels)
                    ),
                    sep="\n",
                )
                print()
            print("🏁 End of check.")

        print("⏹️ Checking taxonomy for label collisions...")
        print()
        _print_collisions(
            "⏹️ Real label collisions ...",
            self.taxonomy._lookupConceptsByStandardLabel,
        )
        print()
        _print_collisions(
            "⏹️ Pretend (normalised) label collisions ...",
            self.taxonomy._lookupConceptsByPretendLabel,
            label_transforms=[normalizeLabelText, stripLabelSuffix, str.lower],
            skip_suffix_collisions=True,
        )
        print()
        print("🏁 End of taxonomy checker report.")
