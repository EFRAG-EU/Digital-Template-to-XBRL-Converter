"""Small helpers exposed to Jinja templates."""

from __future__ import annotations

from datetime import timedelta

from .conversion import ConversionStore


def format_timedelta(td: timedelta) -> str:
    """Render ``td`` as a human-friendly ``"2 days 3 hours"``-style string."""
    parts: list[str] = []
    days, remainder = divmod(int(td.total_seconds()), 86400)
    hours, remainder = divmod(remainder, 3600)
    minutes, seconds = divmod(remainder, 60)

    def plural(amount: int, unit: str) -> str:
        return f"{amount} {unit}{'s' if amount > 1 else ''}"

    if days:
        parts.append(plural(days, "day"))
    if hours:
        parts.append(plural(hours, "hour"))
    if minutes:
        parts.append(plural(minutes, "minute"))
    if seconds:
        parts.append(plural(seconds, "second"))
    return " ".join(parts)


def get_upload_filename(id: str) -> str:
    """Return the original Excel filename for ``id`` (empty string if unknown)."""
    conv = ConversionStore.from_flask().get(id)
    if conv is None or (excel := conv.excel) is None:
        return ""
    return excel.filename
