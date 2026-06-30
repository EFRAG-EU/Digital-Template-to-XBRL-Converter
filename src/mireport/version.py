from __future__ import annotations

import json
import logging
import re
import subprocess
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


class VersionHolder(NamedTuple):
    major: int
    minor: int
    patch: int
    suffix: str

    def __str__(self) -> str:
        return f"{self.major}.{self.minor}.{self.patch}{self.suffix}"

    @property
    def _core(self) -> tuple[int, int, int]:
        return (self.major, self.minor, self.patch)

    @property
    def is_prerelease(self) -> bool:
        return self.suffix.startswith("-")

    @property
    def prerelease(self) -> str | None:
        if not self.is_prerelease:
            return None
        pre, _, _ = self.suffix[1:].partition("+")
        return pre or None

    @property
    def build_metadata(self) -> str | None:
        _, _, meta = self.suffix.partition("+")
        return meta or None

    @property
    def strip_build_metadata(self) -> VersionHolder:
        pre = f"-{p}" if (p := self.prerelease) else ""
        return VersionHolder(self.major, self.minor, self.patch, pre)

    def __lt__(self, other: object) -> bool:
        if not isinstance(other, VersionHolder):
            return NotImplemented
        if self._core != other._core:
            return self._core < other._core
        # semver §11.3: pre-release < release; §10: build metadata ignored for precedence
        if self.is_prerelease == other.is_prerelease:
            if self.is_prerelease:
                return (
                    self.suffix < other.suffix
                )  # lexical between pre-release variants
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
