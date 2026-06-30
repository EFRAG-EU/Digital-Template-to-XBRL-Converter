"""Form parsing helpers for the upload route."""

from __future__ import annotations

from collections.abc import Mapping

IMAGE_UPLOAD_FIELDS: tuple[tuple[str, str], ...] = (
    ("logo", "image_logo"),
    ("cover", "image_cover"),
    ("background", "image_background"),
)


def first_str(form: Mapping[str, str], *fields: str) -> str:
    """First non-empty trimmed value among ``fields`` in ``form``."""
    return next((v for f in fields if (v := form.get(f, "").strip())), "")
