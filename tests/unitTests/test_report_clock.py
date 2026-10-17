from __future__ import annotations

from datetime import UTC, datetime

import pytest

from mireport.report.inlinereport import _now_utc


def test_source_date_epoch_pins_the_generation_time(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SOURCE_DATE_EPOCH", "1767225600")
    assert _now_utc() == datetime(2026, 1, 1, tzinfo=UTC)


def test_without_source_date_epoch_it_is_the_current_time(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("SOURCE_DATE_EPOCH", raising=False)
    before = datetime.now(UTC)
    assert before <= _now_utc() <= datetime.now(UTC)
