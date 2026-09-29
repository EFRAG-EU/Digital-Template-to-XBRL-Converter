"""The token-keyed per-run context shared in-process between
callArelleForTaxonomyInfo() and the taxonomy-info plugin."""

import pytest

from mireport.arelle.diagnostics import ArelleDiagnostic
from mireport.arelle.taxonomy_info_run import TaxonomyInfoRun, TaxonomyInfoRunRegistry
from mireport.entrypoints import entryPointSetOf

ENTRY_POINT = entryPointSetOf("https://example.com/entry.xsd")


def test_open_get_close_lifecycle() -> None:
    token = TaxonomyInfoRunRegistry.open(entryPointSet=ENTRY_POINT)
    try:
        run = TaxonomyInfoRunRegistry.get(token)
        assert run == TaxonomyInfoRun(entryPointSet=ENTRY_POINT)
    finally:
        closed = TaxonomyInfoRunRegistry.close(token)
    assert closed is run
    assert TaxonomyInfoRunRegistry.get(token) is None


def test_diagnostics_added_to_the_run_come_back_on_close() -> None:
    token = TaxonomyInfoRunRegistry.open(entryPointSet=ENTRY_POINT)
    try:
        run = TaxonomyInfoRunRegistry.get(token)
        assert run is not None
        diagnostic = ArelleDiagnostic.warning("w")
        run.diagnostics.append(diagnostic)
    finally:
        closed = TaxonomyInfoRunRegistry.close(token)
    assert closed.diagnostics == [diagnostic]


@pytest.mark.parametrize("token", [None, "no-such-token"], ids=["none", "unknown"])
def test_get_without_a_registered_run_is_none(token: str | None) -> None:
    assert TaxonomyInfoRunRegistry.get(token) is None


def test_second_close_raises() -> None:
    token = TaxonomyInfoRunRegistry.open(entryPointSet=ENTRY_POINT)
    TaxonomyInfoRunRegistry.close(token)
    with pytest.raises(KeyError):
        TaxonomyInfoRunRegistry.close(token)


def test_tokens_are_unique() -> None:
    first = TaxonomyInfoRunRegistry.open(entryPointSet=ENTRY_POINT)
    second = TaxonomyInfoRunRegistry.open(entryPointSet=ENTRY_POINT)
    try:
        assert first != second
    finally:
        TaxonomyInfoRunRegistry.close(first)
        TaxonomyInfoRunRegistry.close(second)
