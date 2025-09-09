from datetime import date, datetime
from typing import Literal

DecimalPlaces = int | Literal["INF"]
FactValue = int | float | bool | str | date | datetime
