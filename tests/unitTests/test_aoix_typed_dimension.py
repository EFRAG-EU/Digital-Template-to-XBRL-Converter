"""What is handed to aoix for typed dimensions and divide units.

aoix deprecated the 'typed' keyword and the 'complex-units' aspect; mireport still holds both in
the older shapes internally and converts at the point of emission.
"""

from __future__ import annotations

import pytest

from mireport.report.fact import _aoixTypedDimension


def test_a_typed_dimension_names_its_wrapper_then_gives_the_plain_value() -> None:
    assert _aoixTypedDimension(
        "tpt:TargetIdentifierAxis", '"<tpt:Id>TGT-001</tpt:Id>"'
    ) == [
        "typed-value-wrapper=tpt:Id",
        'tpt:TargetIdentifierAxis="TGT-001"',
    ]


def test_escaped_text_is_unescaped_because_aoix_escapes_it_again() -> None:
    held = '"<t:W>R&amp;D &lt;1&gt; it&apos;s</t:W>"'
    assert _aoixTypedDimension("t:Axis", held) == [
        "typed-value-wrapper=t:W",
        't:Axis="R&D <1> it\'s"',
    ]


def test_a_double_quote_in_the_value_keeps_the_legacy_keyword() -> None:
    held = '"<t:W>say &quot;hi&quot;</t:W>"'
    assert _aoixTypedDimension("t:Axis", held) == [f"typed t:Axis={held}"]


@pytest.mark.parametrize("held", ['"a/b"', '"utr:tCO2e/iso4217:GBP"'])
def test_a_divide_unit_is_written_unquoted(held: str) -> None:
    from mireport.report.fact import Fact

    out = Fact._aoixAspects({"complex-units": held})
    assert out == [f"units={held.strip(chr(34))}"]
