from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import patch

import pytest

from mireport.arelle import taxonomy_info
from mireport.arelle.support import ArelleRelatedException

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
    options = optionsFor([ENTRY_POINT, LABELS, DOCS])
    assert options.entrypointFile == ENTRY_POINT
    assert options.importFiles == f"{LABELS}|{DOCS}"


def test_no_entry_point_document() -> None:
    with pytest.raises(ArelleRelatedException, match="No entry point document"):
        optionsFor([])
