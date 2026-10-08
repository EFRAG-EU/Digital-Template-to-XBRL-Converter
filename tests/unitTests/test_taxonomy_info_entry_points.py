from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING
from unittest.mock import patch

import pytest

from mireport.arelle import taxonomy_info
from mireport.arelle.support import ArelleRelatedException
from mireport.entrypoints import EntryPointSet, entryPointSetOf

if TYPE_CHECKING:
    from arelle.RuntimeOptions import RuntimeOptions

ENTRY_POINT = "https://example.com/entry.xsd"
LABELS = "https://example.com/labels.xml"
DOCS = "https://example.com/docs.xml"


def optionsFor(entry_point: str | list[str]) -> RuntimeOptions:
    """Run callArelleForTaxonomyInfo with Arelle stubbed out, returning the
    RuntimeOptions it would have handed to Arelle."""
    with (
        patch.object(taxonomy_info, "Session") as session,
        patch.object(taxonomy_info.ArelleProcessingResult, "fromSession"),
    ):
        taxonomy_info.callArelleForTaxonomyInfo(entry_point, [], "taxonomy.json")
    run = session.return_value.__enter__.return_value.run
    run.assert_called_once()
    return run.call_args.args[0]


def loadedDocuments(options: RuntimeOptions) -> list[str]:
    imports = options.importFiles.split("|") if options.importFiles else []
    return [str(options.entrypointFile), *imports]


def test_single_document_entry_point_imports_nothing() -> None:
    options = optionsFor(ENTRY_POINT)
    assert options.entrypointFile == ENTRY_POINT
    assert options.importFiles is None


def test_single_document_given_as_a_sequence() -> None:
    options = optionsFor([ENTRY_POINT])
    assert options.entrypointFile == ENTRY_POINT
    assert options.importFiles is None


def test_extra_documents_are_imported_into_the_one_dts() -> None:
    # Passing them all as entry points would load each as its own DTS instead.
    # Which one is the entrypointFile is only an Arelle mechanic: the set has
    # no order, so it is the first in sorted order and the rest are imported.
    options = optionsFor([LABELS, ENTRY_POINT, DOCS])
    assert options.entrypointFile == DOCS
    assert options.importFiles == f"{ENTRY_POINT}|{LABELS}"


def test_no_entry_point_document() -> None:
    with pytest.raises(ArelleRelatedException, match="Unusable entry point"):
        optionsFor([])


def test_package_paths_cross_the_arelle_boundary_as_str() -> None:
    packages = [Path("a/one.zip"), Path("b/two.zip")]
    with (
        patch.object(taxonomy_info, "Session") as session,
        patch.object(taxonomy_info.ArelleProcessingResult, "fromSession"),
    ):
        taxonomy_info.callArelleForTaxonomyInfo(
            ENTRY_POINT, packages, Path("taxonomy.json")
        )
    options = session.return_value.__enter__.return_value.run.call_args.args[0]
    assert options.packages == [str(p) for p in packages]


def test_duplicate_documents_are_loaded_once() -> None:
    options = optionsFor([ENTRY_POINT, LABELS, ENTRY_POINT, LABELS, DOCS])
    assert loadedDocuments(options) == [DOCS, ENTRY_POINT, LABELS]


def test_the_run_is_given_the_entry_point_set() -> None:
    with (
        patch.object(taxonomy_info, "Session"),
        patch.object(taxonomy_info.ArelleProcessingResult, "fromSession"),
        patch.object(
            taxonomy_info.TaxonomyInfoRunRegistry,
            "open",
            wraps=taxonomy_info.TaxonomyInfoRunRegistry.open,
        ) as runOpen,
    ):
        taxonomy_info.callArelleForTaxonomyInfo(
            [ENTRY_POINT, LABELS, ENTRY_POINT], [], "taxonomy.json"
        )
    runOpen.assert_called_once_with(
        entryPointSet=entryPointSetOf([ENTRY_POINT, LABELS])
    )
    assert isinstance(runOpen.call_args.kwargs["entryPointSet"], EntryPointSet)
