from typing import NamedTuple
from mireport.xml import QName

class Unit(NamedTuple):
    """
    Holds the units for a fact. Immutable and hashable.
    """

    numerator: tuple[QName, ...]
    denominator: tuple[QName, ...] | None = None

    @staticmethod
    def _format_unit_list(units: tuple[QName, ...]) -> str:
        s = "*".join(sorted(map(str, units)))
        return s

    def __str__(self) -> str:
        numerator = self._format_unit_list(self.numerator)
        if not self.denominator:
            return numerator
        denominator = self._format_unit_list(self.denominator)
        return f"({numerator})/({denominator})"

    @property
    def has_denominator(self) -> bool:
        return bool(self.denominator)
