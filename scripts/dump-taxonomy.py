import mireport
from mireport.excelprocessor import VSME_DEFAULTS
from mireport.taxonomy import getTaxonomy, listTaxonomies


def main() -> None:
    mireport.loadTaxonomyJSON()
    entry_point = VSME_DEFAULTS["taxonomyEntryPoints"]["supportedEntryPoint"]
    print("Available taxonomies:", *listTaxonomies(), sep="\n\t")
    print(f"Ready to show {entry_point} ")
    input("Press Enter to continue...")
    vsme = getTaxonomy(entry_point)
    for group in vsme.presentation:
        print(f"{group.definition} [{group.roleUri}]")
        for relationship in group.relationships:
            concept = relationship.concept
            print(
                "\t" * relationship.depth,
                concept.getStandardLabel(),
                f"[{concept.qname} {concept.dataType}]",
            )


if __name__ == "__main__":
    main()
