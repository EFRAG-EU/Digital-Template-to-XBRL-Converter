"""Session-backed conversion model.

Each user upload becomes one entry in :data:`flask.session`, keyed by its
conversion id.  :class:`Conversion` wraps that per-id dict and exposes typed
accessors for what the web app keeps in it.

The session backend serialises what it stores, so everything held here must be
something it can round-trip.  ``FilelikeAndFileName`` is a ``NamedTuple`` and
comes back as a plain list, so reads go through ``FilelikeAndFileName.from_tuple``
and :class:`~mireport.conversionresults.ConversionResults` is stored as a dict.

Mutating a nested dict is not noticed by the session implementation, so every
setter calls the ``onChange`` callback; :meth:`ConversionStore.fromFlask` wires
that to ``session.modified``.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator, MutableMapping
from typing import Any

from flask import session as flask_session

from mireport.conversionresults import ConversionResults
from mireport.filesupport import FilelikeAndFileName

_INTERNAL_KEYS: frozenset[str] = frozenset(
    {"_permanent", "csrf_token", "captcha_answers"}
)

# Value of MigrationOutcome.MIGRATION_REQUIRED, repeated here because importing
# .migration would be circular. test_conversion pins the two together.
MIGRATION_REQUIRED = "migration_required"


class Conversion:
    """Typed view over a single per-id conversion dict held in the session."""

    __slots__ = ("_data", "_onChange", "_prior")

    # Generated files that can be fetched by name from a finished conversion.
    OUTPUT_KEYS: frozenset[str] = frozenset({"zip", "viewer", "json", "excel"})

    def __init__(
        self,
        data: MutableMapping[str, Any],
        onChange: Callable[[], None] | None = None,
        *,
        prior: bool = False,
    ) -> None:
        self._data = data
        self._onChange = onChange
        self._prior = prior

    def _changed(self) -> None:
        if self._onChange is not None:
            self._onChange()

    @property
    def raw(self) -> MutableMapping[str, Any]:
        """The underlying mutable dict.  Prefer the typed properties."""
        return self._data

    @property
    def prior(self) -> Conversion:
        """This conversion as last year's workbook sees it, for the migration
        flow: its ``migrationSource`` is ``priorReport``, and its migration
        state is kept apart from this year's."""
        return Conversion(self._data, self._onChange, prior=True)

    @property
    def isPrior(self) -> bool:
        return self._prior

    def _migrationKey(self, key: str) -> str:
        return f"prior_{key}" if self._prior else key

    def get(self, key: str, default: Any = None) -> Any:
        return self._data.get(key, default)

    def __contains__(self, key: str) -> bool:
        return key in self._data

    # -- plain values ---------------------------------------------------

    def _str(self, key: str) -> str:
        return self._data.get(key, "")

    def _setStr(self, key: str, value: str) -> None:
        self._data[key] = value
        self._changed()

    @property
    def id(self) -> str:
        return self._data["id"]

    @property
    def date(self) -> str:
        return self._str("date")

    @date.setter
    def date(self, value: str) -> None:
        self._setStr("date", value)

    @property
    def locale_str(self) -> str | None:
        return self._data.get("locale_str")

    @locale_str.setter
    def locale_str(self, value: str | None) -> None:
        if value:
            self._data["locale_str"] = value
        else:
            self._data.pop("locale_str", None)
        self._changed()

    @property
    def style_palette(self) -> str:
        return self._str("style_palette")

    @style_palette.setter
    def style_palette(self, value: str) -> None:
        self._setStr("style_palette", value)

    @property
    def style_mode(self) -> str:
        return self._str("style_mode")

    @style_mode.setter
    def style_mode(self, value: str) -> None:
        self._setStr("style_mode", value)

    @property
    def report_template_version(self) -> str:
        return self._str(self._migrationKey("report_template_version"))

    @report_template_version.setter
    def report_template_version(self, value: str) -> None:
        self._setStr(self._migrationKey("report_template_version"), value)

    @property
    def migration_outcome(self) -> str:
        return self._str(self._migrationKey("migration_outcome"))

    @migration_outcome.setter
    def migration_outcome(self, value: object) -> None:
        self._setStr(self._migrationKey("migration_outcome"), str(value))

    @property
    def migration_error(self) -> str | None:
        return self._data.get(self._migrationKey("migration_error"))

    @migration_error.setter
    def migration_error(self, value: str | None) -> None:
        key = self._migrationKey("migration_error")
        if value is None:
            self._data.pop(key, None)
        else:
            self._data[key] = value
        self._changed()

    @property
    def migration_elapsed(self) -> float | None:
        return self._data.get(self._migrationKey("migration_elapsed"))

    @migration_elapsed.setter
    def migration_elapsed(self, value: float | None) -> None:
        self._data[self._migrationKey("migration_elapsed")] = value
        self._changed()

    @property
    def migration_issues(self) -> list[str]:
        return list(self._data.get(self._migrationKey("migration_issues"), []))

    @migration_issues.setter
    def migration_issues(self, value: list[str]) -> None:
        self._data[self._migrationKey("migration_issues")] = list(value)
        self._changed()

    @property
    def has_migration_results(self) -> bool:
        return any(
            self._migrationKey(k) in self._data
            for k in ("migrated_excel", "migration_error")
        )

    @property
    def successful(self) -> bool:
        return bool(self._data.get("successful", False))

    @successful.setter
    def successful(self, value: bool) -> None:
        self._data["successful"] = bool(value)
        self._changed()

    # -- files ----------------------------------------------------------

    def _getFilelike(self, key: str) -> FilelikeAndFileName | None:
        raw = self._data.get(key)
        return FilelikeAndFileName.from_tuple(raw) if raw is not None else None

    def _setFilelike(self, key: str, value: FilelikeAndFileName | None) -> None:
        if value is None:
            self._data.pop(key, None)
        elif isinstance(value, FilelikeAndFileName):
            self._data[key] = value
        else:
            raise TypeError(f"{key!r} must be a FilelikeAndFileName")
        self._changed()

    @property
    def excel(self) -> FilelikeAndFileName | None:
        return self._getFilelike("excel")

    @excel.setter
    def excel(self, value: FilelikeAndFileName | None) -> None:
        self._setFilelike("excel", value)

    @property
    def priorReport(self) -> FilelikeAndFileName | None:
        """Last year's report, whose figures become the comparatives."""
        return self._getFilelike("priorReport")

    @priorReport.setter
    def priorReport(self, value: FilelikeAndFileName | None) -> None:
        self._setFilelike("priorReport", value)

    @property
    def migrated_excel(self) -> FilelikeAndFileName | None:
        return self._getFilelike(self._migrationKey("migrated_excel"))

    @migrated_excel.setter
    def migrated_excel(self, value: FilelikeAndFileName | None) -> None:
        self._setFilelike(self._migrationKey("migrated_excel"), value)

    @property
    def migrationSource(self) -> FilelikeAndFileName | None:
        """The workbook the migration flow is about: this year's, or last year's."""
        return self.priorReport if self._prior else self.excel

    @property
    def zip(self) -> FilelikeAndFileName | None:
        return self._getFilelike("zip")

    @zip.setter
    def zip(self, value: FilelikeAndFileName | None) -> None:
        self._setFilelike("zip", value)

    @property
    def has_zip(self) -> bool:
        return "zip" in self._data

    @property
    def viewer(self) -> FilelikeAndFileName | None:
        return self._getFilelike("viewer")

    @viewer.setter
    def viewer(self, value: FilelikeAndFileName | None) -> None:
        self._setFilelike("viewer", value)

    @property
    def viewer_members(self) -> dict[str, FilelikeAndFileName]:
        """Document set members of the viewer, by the filename it fetches them as."""
        raw = self._data.get("viewer_members", {})
        return {k: FilelikeAndFileName.from_tuple(v) for k, v in raw.items()}

    @viewer_members.setter
    def viewer_members(self, value: dict[str, FilelikeAndFileName]) -> None:
        self._data["viewer_members"] = dict(value)
        self._changed()

    @property
    def json(self) -> FilelikeAndFileName | None:
        return self._getFilelike("json")

    @json.setter
    def json(self, value: FilelikeAndFileName | None) -> None:
        self._setFilelike("json", value)

    def cachedOutput(self, ftype: str) -> FilelikeAndFileName | None:
        """The generated file of this type, if it has been produced."""
        return self._getFilelike(ftype) if ftype in self.OUTPUT_KEYS else None

    def image(self, sessionKey: str) -> FilelikeAndFileName | None:
        return self._getFilelike(sessionKey)

    def setImage(self, sessionKey: str, value: FilelikeAndFileName) -> None:
        self._setFilelike(sessionKey, value)

    def hasImage(self, sessionKey: str) -> bool:
        return sessionKey in self._data

    @property
    def pdfs(self) -> list[FilelikeAndFileName]:
        """Uploaded supplementary PDFs, in the order they were uploaded."""
        return [FilelikeAndFileName.from_tuple(p) for p in self._data.get("pdfs") or []]

    @pdfs.setter
    def pdfs(self, value: list[FilelikeAndFileName]) -> None:
        if value:
            self._data["pdfs"] = list(value)
        else:
            self._data.pop("pdfs", None)
        self._changed()

    @property
    def external_values(self) -> dict[str, FilelikeAndFileName]:
        raw = self._data.get("external_values", {})
        return {k: FilelikeAndFileName.from_tuple(v) for k, v in raw.items()}

    @external_values.setter
    def external_values(self, value: dict[str, FilelikeAndFileName]) -> None:
        self._data["external_values"] = dict(value)
        self._changed()

    @property
    def has_external_values(self) -> bool:
        return "external_values" in self._data

    @property
    def partial_fact_concepts(self) -> list[dict[str, str]]:
        return list(self._data.get("partial_fact_concepts", []))

    @partial_fact_concepts.setter
    def partial_fact_concepts(self, value: list[dict[str, str]]) -> None:
        self._data["partial_fact_concepts"] = [dict(c) for c in value]
        self._changed()

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
        self._changed()

    @property
    def has_results(self) -> bool:
        return "results" in self._data


class ConversionStore:
    """Collection of :class:`Conversion` instances backed by a Flask session.

    Use :meth:`fromFlask` inside a request to get one bound to the current
    :data:`flask.session`; pass a plain dict in for unit tests.
    """

    def __init__(
        self,
        session: MutableMapping[str, Any],
        onChange: Callable[[], None] | None = None,
    ) -> None:
        self._session = session
        self._onChange = onChange

    @classmethod
    def fromFlask(cls) -> ConversionStore:
        def markModified() -> None:
            flask_session.modified = True

        return cls(flask_session, markModified)

    def _wrap(self, data: MutableMapping[str, Any]) -> Conversion:
        return Conversion(data, self._onChange)

    def _isConversion(self, id: str) -> bool:
        """The session also holds the CSRF token, captcha answers, etc.; only
        a dict under any other key is a conversion."""
        return id not in _INTERNAL_KEYS and isinstance(self._session.get(id), dict)

    def __contains__(self, id: str) -> bool:
        return self._isConversion(id)

    def get(self, id: str) -> Conversion | None:
        return self._wrap(self._session[id]) if self._isConversion(id) else None

    def create(self, id: str) -> Conversion:
        data = self._session.setdefault(id, {"id": id})
        return self._wrap(data)

    def __iter__(self) -> Iterator[str]:
        return iter(self.active())

    def active(self) -> dict[str, Conversion]:
        """User-visible conversions.

        Excludes session internals and any conversion that was aborted because
        a mandatory migration was required (its entry is kept so the migration
        page can still find it, but it must not appear in the listing).
        """
        out: dict[str, Conversion] = {}
        for k in self._session:
            if (conv := self.get(k)) is None:
                continue
            if MIGRATION_REQUIRED in (
                conv.migration_outcome,
                conv.prior.migration_outcome,
            ):
                continue
            out[k] = conv
        return out

    def hasActive(self) -> bool:
        return bool(self.active())

    def delete(self, id: str) -> None:
        if self._isConversion(id):
            self._session.pop(id)

    def deleteActive(self) -> None:
        for k in list(self.active()):
            self._session.pop(k, None)
