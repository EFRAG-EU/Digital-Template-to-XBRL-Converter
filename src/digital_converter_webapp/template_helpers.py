"""Small helpers exposed to Jinja templates."""

from __future__ import annotations

from datetime import timedelta

from .conversion import ConversionStore


def format_timedelta(td: timedelta) -> str:
    total_seconds = int(td.total_seconds())
    parts = []

    days, remainder = divmod(total_seconds, 86400)  # 86400 seconds in a day
    hours, remainder = divmod(remainder, 3600)
    minutes, seconds = divmod(remainder, 60)

    def plural(amount: int, unit_singular: str) -> str:
        if amount > 1:
            return f"{amount} {unit_singular}s"
        else:
            return f"{amount} {unit_singular}"

    if days:
        parts.append(plural(days, "day"))
    if hours:
        parts.append(plural(hours, "hour"))
    if minutes:
        parts.append(plural(minutes, "minute"))
    if seconds:
        parts.append(plural(seconds, "second"))

    return " ".join(parts)


def getUploadFilename(id: str) -> str:
    """The original Excel filename for conversion ``id``, or "" if unknown."""
    conv = ConversionStore.fromFlask().get(id)
    if conv is None or (excel := conv.excel) is None:
        return ""
    return excel.filename
