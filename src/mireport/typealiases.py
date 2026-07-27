from __future__ import annotations

from collections.abc import Mapping
from datetime import date, datetime
from typing import Literal

DecimalPlaces = int | Literal["INF"]
FactValue = int | float | bool | str | date | datetime

LabelsByRole = Mapping[str, str]
LabelsByLang = Mapping[str, LabelsByRole]
