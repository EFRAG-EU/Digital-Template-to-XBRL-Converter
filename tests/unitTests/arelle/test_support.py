"""Unit tests for support.py's Arelle QName canonicalisation."""

import pytest
from arelle.ModelValue import QName

from mireport.arelle.support import (
    ArelleModelInconsistency,
    ArelleQNameCanonicaliser,
)
from mireport.xml import getBootstrapQNameMaker


def makeCanonicaliser() -> ArelleQNameCanonicaliser:
    return ArelleQNameCanonicaliser(getBootstrapQNameMaker())


class TestConvert:
    def test_converts_fully_qualified_qname(self) -> None:
        canonicaliser = makeCanonicaliser()
        converted = canonicaliser.convert(
            QName("vsme", "https://example.com/vsme", "Thing")
        )
        assert str(converted) == "vsme:Thing"

    @pytest.mark.parametrize(
        "qname",
        [
            QName(None, "https://example.com/vsme", "Thing"),
            QName("vsme", None, "Thing"),
        ],
        ids=["no-prefix", "no-namespace"],
    )
    def test_raises_on_incomplete_qname(self, qname: QName) -> None:
        canonicaliser = makeCanonicaliser()
        with pytest.raises(ArelleModelInconsistency):
            canonicaliser.convert(qname)
