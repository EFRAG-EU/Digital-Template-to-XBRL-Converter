from __future__ import annotations

import json
import logging
import re
import subprocess
from dataclasses import dataclass
from functools import cache, partial
from importlib.metadata import Distribution, PackageNotFoundError, version
from pathlib import Path
from typing import NamedTuple, Self
from urllib.parse import urlparse
from urllib.request import url2pathname

_BUILD_METADATA_SAFE_RE = re.compile(r"[^0-9A-Za-z.-]")
_PACKAGE_NAME = "EFRAG-DigitalTemplateToXBRL-Converter"
_VERSION_PARSE_RE = re.compile(r"^(\d+)\.(\d+)\.(\d+)(.*)$")
L = logging.getLogger(__name__)


class VersionInformationTuple(NamedTuple):
    name: str
    version: str

    def __str__(self) -> str:
        return f"{self.name} (version {self.version})"


@dataclass(frozen=True, slots=True)
class VersionHolder:
    """A parsed semver version.

    Note the deliberate split between equality and ordering:

    * ``__eq__`` is dataclass field identity — exact fields, *including* build
      metadata. So ``1.0.0+build.1 != 1.0.0+build.2``.
    * Ordering follows semver precedence, where build metadata is ignored
      (semver §10). So those same two versions have *equal precedence*: neither
      is ``<`` the other.

    The consequence is that build-metadata-only variants are ``!=`` yet compare
    as equal precedence. This is intentional: ``==`` answers "the same exact
    version?", ordering answers "which takes precedence?".
    """

    major: int
    minor: int
    patch: int
    suffix: str
    # False marks a missing/unparseable version: major/minor/patch are 0 and
    # suffix holds the raw input. Set only by parse_or_invalid(). Participates in
    # __eq__/__hash__, so such a sentinel never == a real version.
    is_valid: bool = True

    def __str__(self) -> str:
        if not self.is_valid:
            return self.suffix  # the raw input, not a misleading "0.0.0<raw>"
        return f"{self.major}.{self.minor}.{self.patch}{self.suffix}"

    @property
    def _core(self) -> tuple[int, int, int]:
        return (self.major, self.minor, self.patch)

    def same_major_minor(self, other: VersionHolder) -> bool:
        return (
            self.is_valid
            and other.is_valid
            and (self.major, self.minor) == (other.major, other.minor)
        )

    def _split_suffix(self) -> tuple[str | None, str | None]:
        # Decode the semver suffix "[-pre][+build]" into its two parts. An
        # invalid version's suffix is raw input, not semver, so it has neither.
        if not self.is_valid:
            return (None, None)
        pre, _, build = self.suffix.partition("+")
        prerelease = pre[1:] if pre.startswith("-") else None
        return (prerelease or None, build or None)

    @property
    def is_prerelease(self) -> bool:
        return self.is_valid and self.suffix.startswith("-")

    @property
    def prerelease(self) -> str | None:
        return self._split_suffix()[0]

    @property
    def build_metadata(self) -> str | None:
        return self._split_suffix()[1]

    @property
    def strip_build_metadata(self) -> VersionHolder:
        if not self.is_valid:
            return self  # stripping a non-version must not fabricate a valid 0.0.0
        pre = f"-{p}" if (p := self.prerelease) else ""
        return VersionHolder(self.major, self.minor, self.patch, pre)

    @staticmethod
    def _prerelease_key(prerelease: str) -> list[tuple[int, int | str]]:
        # semver §11.4: dot-separated identifiers, compared field by field.
        # Numeric identifiers (§11.4.1) compare numerically and rank below
        # alphanumeric ones (§11.4.3); a larger set of fields wins when all
        # preceding fields are equal (§11.4.4 — handled by list length).
        key: list[tuple[int, int | str]] = []
        for identifier in prerelease.split("."):
            if identifier.isdigit():
                key.append((0, int(identifier)))
            else:
                key.append((1, identifier))
        return key

    def __lt__(self, other: object) -> bool:
        if not isinstance(other, VersionHolder):
            return NotImplemented
        if not (self.is_valid and other.is_valid):
            # Ordering a missing/invalid version is meaningless; fail fast rather
            # than silently treating the sentinel as "0.0.0". __gt__/__le__/__ge__
            # all delegate to this, so the guard covers them too. (Equality stays
            # value-based and well-defined.)
            raise TypeError(
                f"cannot order an invalid/missing version: {self!r} vs {other!r}"
            )
        if self._core != other._core:
            return self._core < other._core
        # semver §11.3: pre-release < release; §10: build metadata ignored for precedence
        if self.is_prerelease == other.is_prerelease:
            if self.is_prerelease:
                # §11.4: compare pre-release identifiers, ignoring build metadata
                return self._prerelease_key(
                    self.prerelease or ""
                ) < self._prerelease_key(other.prerelease or "")
            return False  # same-core non-pre-release: same precedence per semver §10
        return self.is_prerelease  # pre-release < release

    def __gt__(self, other: object) -> bool:
        if not isinstance(other, VersionHolder):
            return NotImplemented
        return other < self

    def __le__(self, other: object) -> bool:
        if not isinstance(other, VersionHolder):
            return NotImplemented
        return not (other < self)

    def __ge__(self, other: object) -> bool:
        if not isinstance(other, VersionHolder):
            return NotImplemented
        return not (self < other)

    @classmethod
    def parse(cls, version_str: str) -> Self:
        version_str = version_str.strip()
        match = _VERSION_PARSE_RE.match(version_str)
        if not match:
            raise ValueError(f"Invalid version format: {version_str}")
        major, minor, patch, suffix = match.groups()
        return cls(int(major), int(minor), int(patch), suffix or "")

    @classmethod
    def parse_safe(cls, version_str: str) -> Self | None:
        try:
            return cls.parse(version_str)
        except ValueError:
            return None

    @classmethod
    def parse_or_invalid(cls, version_str: str) -> VersionHolder:
        """Total parse: like parse_safe(), but never returns None. On failure
        returns a version marked is_valid=False carrying the raw input string;
        callers distinguish via .is_valid."""
        return cls.parse_safe(version_str) or cls(0, 0, 0, version_str, is_valid=False)


@cache
def _editable_suffix() -> str:
    try:
        dist = Distribution.from_name(_PACKAGE_NAME)
        if not (raw := dist.read_text("direct_url.json")):
            return ""
        data = json.loads(raw)
        # https://packaging.python.org/en/latest/specifications/direct-url-data-structure/
        # editable (type: boolean): true if the distribution was/is to be
        # installed in editable mode, false otherwise. If absent, default to
        # false.
        editable = bool(data.get("dir_info", {}).get("editable", False))
        if not editable:
            return ""
        if (url := data.get("url", "")).startswith("file://"):
            source_dir = Path(url2pathname(urlparse(url).path))
            _git = partial(
                subprocess.run,
                capture_output=True,
                stdin=subprocess.DEVNULL,
                encoding="utf-8",
                timeout=5,
                check=True,
                cwd=source_dir,
            )
            result = _git(
                [
                    "git",
                    "-c",
                    "i18n.logOutputEncoding=utf-8",
                    "describe",
                    "--tags",
                    "--always",
                    "--dirty=.dirty",
                ]
            )
            output = result.stdout.strip()
            # Long form "tag-N-ghash[.dirty]" → extract just hash[.dirty]
            if m := re.match(r"^.+-\d+-g([0-9a-f]+)(\.dirty)?$", output):
                output = m.group(1) + (m.group(2) or "")
            return f"+git.{_BUILD_METADATA_SAFE_RE.sub('-', output)}"
        return ""
    except Exception:
        L.warning(
            "Failed to determine editable version suffix, falling back to no suffix",
            exc_info=True,
        )
    return ""


try:
    OUR_VERSION = version(_PACKAGE_NAME) + _editable_suffix()
    OUR_VERSION_HOLDER = VersionHolder.parse(OUR_VERSION)
except (PackageNotFoundError, ValueError):
    OUR_VERSION = "(unknown version)"
    OUR_VERSION_HOLDER = VersionHolder(0, 0, 0, "(unknown version)")
