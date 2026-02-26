from __future__ import annotations

from datetime import date, datetime
from typing import Literal, Mapping

DecimalPlaces = int | Literal["INF"]
FactValue = int | float | bool | str | date | datetime

LabelsByRole = Mapping[str, str]
LabelsByLang = Mapping[str, LabelsByRole]
