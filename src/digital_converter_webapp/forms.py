"""Limits and form parsing helpers for the upload route."""

from __future__ import annotations

from collections.abc import Mapping

MAX_FILE_SIZE = 16 * 2**20  # 16 MiB
MAX_PDF_COUNT = 10  # supplementary PDFs per conversion

# (form field, session key) for each optional image that themes the report.
IMAGE_UPLOAD_FIELDS: tuple[tuple[str, str], ...] = (
    ("logo", "image_logo"),
    ("cover", "image_cover"),
    ("background", "image_background"),
)


def first_str(form: Mapping[str, str], *fields: str) -> str:
    """First non-empty trimmed value among ``fields`` in ``form``."""
    return next((v for f in fields if (v := form.get(f, "").strip())), "")
