"""Session-backed conversion model.

Each user upload becomes one entry in :data:`flask.session`, keyed by its
conversion id.  :class:`Conversion` wraps that per-id dict and exposes typed
accessors that handle the marshalling required for the session backend
(Redis + msgpack in production).

Everything stored in the session must be a primitive that msgpack can round-trip
(``str``/``int``/``bool``/``list``/``dict``/``tuple``).  The setters here are
responsible for downgrading rich types before storing
(``FilelikeAndFileName`` → tuple, ``ConversionResults`` → dict,
:class:`~.migration.MigrationOutcome` → ``str``) and reconstituting them on read.
"""

from __future__ import annotations

from collections.abc import Iterator, MutableMapping
from typing import Any

from flask import session as flask_session

from mireport.conversionresults import ConversionResults
from mireport.filesupport import FilelikeAndFileName

_INTERNAL_KEYS: frozenset[str] = frozenset(
    {"_permanent", "csrf_token", "captcha_answer"}
)

# Literal value of MigrationOutcome.MIGRATION_REQUIRED; avoids importing .migration here.
_MIGRATION_REQUIRED = "migration_required"


class Conversion:
    """Typed view over a single per-id conversion dict held in the session."""

    __slots__ = ("_data",)

    def __init__(self, data: MutableMapping[str, Any]) -> None:
        self._data = data

    @property
    def raw(self) -> MutableMapping[str, Any]:
        """The underlying mutable dict.  Prefer the typed properties."""
        return self._data

    def get(self, key: str, default: Any = None) -> Any:
        return self._data.get(key, default)

    def __contains__(self, key: str) -> bool:
        return key in self._data

    @property
    def id(self) -> str:
        return self._data["id"]

    @property
    def date(self) -> str:
        return self._data.get("date", "")

    @date.setter
    def date(self, value: str) -> None:
        self._data["date"] = value

    @property
    def locale_str(self) -> str | None:
        return self._data.get("locale_str")

    @locale_str.setter
    def locale_str(self, value: str | None) -> None:
        if value:
            self._data["locale_str"] = value
        else:
            self._data.pop("locale_str", None)

    @property
    def style_palette(self) -> str:
        return self._data.get("style_palette", "")

    @style_palette.setter
    def style_palette(self, value: str) -> None:
        self._data["style_palette"] = value

    @property
    def style_mode(self) -> str:
        return self._data.get("style_mode", "")

    @style_mode.setter
    def style_mode(self, value: str) -> None:
        self._data["style_mode"] = value

    @property
    def template_version(self) -> str:
        return self._data.get("template_version", "")

    @template_version.setter
    def template_version(self, value: str) -> None:
        self._data["template_version"] = value

    @property
    def migration_outcome(self) -> str:
        return self._data.get("migration_outcome", "")

    @migration_outcome.setter
    def migration_outcome(self, value: object) -> None:
        self._data["migration_outcome"] = str(value)

    @property
    def successful(self) -> bool:
        return bool(self._data.get("successful", False))

    @successful.setter
    def successful(self, value: bool) -> None:
        self._data["successful"] = bool(value)

    def _get_filelike(self, key: str) -> FilelikeAndFileName | None:
        raw = self._data.get(key)
        return FilelikeAndFileName.from_tuple(raw) if raw is not None else None

    def _set_filelike(self, key: str, value: FilelikeAndFileName | None) -> None:
        if value is None:
            self._data.pop(key, None)
            return
        if not isinstance(value, FilelikeAndFileName):
            raise TypeError(f"{key!r} must be a FilelikeAndFileName")
        self._data[key] = value

    @property
    def excel(self) -> FilelikeAndFileName | None:
        return self._get_filelike("excel")

    @excel.setter
    def excel(self, value: FilelikeAndFileName | None) -> None:
        self._set_filelike("excel", value)

    @property
    def migrated_excel(self) -> FilelikeAndFileName | None:
        return self._get_filelike("migrated_excel")

    @migrated_excel.setter
    def migrated_excel(self, value: FilelikeAndFileName | None) -> None:
        self._set_filelike("migrated_excel", value)

    @property
    def zip(self) -> FilelikeAndFileName | None:
        return self._get_filelike("zip")

    @zip.setter
    def zip(self, value: FilelikeAndFileName | None) -> None:
        self._set_filelike("zip", value)

    def image(self, session_key: str) -> FilelikeAndFileName | None:
        return self._get_filelike(session_key)

    def set_image(self, session_key: str, value: FilelikeAndFileName) -> None:
        self._set_filelike(session_key, value)

    def has_image(self, session_key: str) -> bool:
        return session_key in self._data

    OUTPUT_KEYS: frozenset[str] = frozenset({"zip", "viewer", "json", "excel"})

    def cached_output(self, ftype: str) -> FilelikeAndFileName | None:
        return self._get_filelike(ftype)

    def cache_output(self, ftype: str, value: FilelikeAndFileName) -> None:
        self._set_filelike(ftype, value)

    @property
    def external_values(self) -> dict[str, FilelikeAndFileName]:
        raw = self._data.get("external_values", {})
        return {k: FilelikeAndFileName.from_tuple(v) for k, v in raw.items()}

    @external_values.setter
    def external_values(self, value: dict[str, FilelikeAndFileName]) -> None:
        self._data["external_values"] = {k: tuple(v) for k, v in value.items()}

    @property
    def has_external_values(self) -> bool:
        return "external_values" in self._data

    @property
    def partial_fact_concepts(self) -> list[dict[str, str]]:
        return list(self._data.get("partial_fact_concepts", []))

    @partial_fact_concepts.setter
    def partial_fact_concepts(self, value: list[dict[str, str]]) -> None:
        self._data["partial_fact_concepts"] = [dict(c) for c in value]

    @property
    def has_partial_fact_concepts(self) -> bool:
        return bool(self._data.get("partial_fact_concepts"))

    @property
    def results(self) -> ConversionResults | None:
        raw = self._data.get("results")
        return ConversionResults.fromDict(raw) if raw is not None else None

    @results.setter
    def results(self, value: ConversionResults) -> None:
        self._data["results"] = value.toDict()
        self._data["successful"] = bool(value.conversionSuccessful)

    @property
    def has_results(self) -> bool:
        return "results" in self._data

    @property
    def has_zip(self) -> bool:
        return "zip" in self._data


class ConversionStore:
    """Collection of :class:`Conversion` instances backed by a Flask session.

    Use :meth:`from_flask` inside a request to get one bound to the current
    :data:`flask.session`; pass a plain dict in for unit tests.
    """

    def __init__(self, session: MutableMapping[str, Any]) -> None:
        self._session = session

    @classmethod
    def from_flask(cls) -> "ConversionStore":
        return cls(flask_session)

    def __contains__(self, id: str) -> bool:
        return isinstance(self._session.get(id), dict)

    def get(self, id: str) -> Conversion | None:
        data = self._session.get(id)
        return Conversion(data) if isinstance(data, dict) else None

    def create(self, id: str) -> Conversion:
        data = self._session.setdefault(id, {"id": id})
        return Conversion(data)

    def __iter__(self) -> Iterator[str]:
        return iter(self.active())

    def active(self) -> dict[str, Conversion]:
        """User-visible conversions.

        Excludes session internals and any conversion that has been aborted
        because a mandatory migration was required (its session entry is kept
        so the migration page can still find it, but it should not appear in
        the listing).
        """
        out: dict[str, Conversion] = {}
        for k, v in self._session.items():
            if k in _INTERNAL_KEYS or not isinstance(v, dict):
                continue
            conv = Conversion(v)
            if conv.migration_outcome == _MIGRATION_REQUIRED:
                continue
            out[k] = conv
        return out

    def has_active(self) -> bool:
        return bool(self.active())

    def delete(self, id: str) -> None:
        self._session.pop(id, None)

    def delete_active(self) -> None:
        for k in list(self.active()):
            self._session.pop(k, None)
