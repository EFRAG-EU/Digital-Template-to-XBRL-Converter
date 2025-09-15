import mireport
from mireport.excelprocessor import VSME_DEFAULTS
from mireport.taxonomy import getTaxonomy, listTaxonomies


def main() -> None:
    mireport.loadTaxonomyJSON()
    entry_point = VSME_DEFAULTS["taxonomyEntryPoints"]["supportedEntryPoint"]
    available = {
        str(num): ep for num, ep in enumerate(sorted(listTaxonomies()), start=1)
    }
    print(
        "Available taxonomies:",
        *[f"{num}: {url}" for num, url in available.items()],
        sep="\n\t",
    )
    requestedEntryPoint = input(
        f"Specify alternate entry point or leave default [{entry_point}]: "
    ).strip()
    if requestedEntryPoint and requestedEntryPoint != entry_point:
        if requestedEntryPoint in available:
            entry_point = available[requestedEntryPoint]
        elif requestedEntryPoint in available.values():
            entry_point = requestedEntryPoint
        else:
            raise SystemExit("Can't access specified entry point.")
    taxonomy = getTaxonomy(entry_point)
    for group in taxonomy.presentation:
        print(f"{group.getLabel()} [{group.roleUri}]")
        for relationship in group.relationships:
            concept = relationship.concept
            print(
                "\t" * relationship.depth,
                concept.getStandardLabel(),
                f"[{concept.qname} {concept.dataType}]",
            )
    print()
    print(
        f"Label languages: {', '.join(sorted(taxonomy.supportedLanguages))}; Default language: {taxonomy.defaultLanguage}"
    )
    print()


if __name__ == "__main__":
    main()
