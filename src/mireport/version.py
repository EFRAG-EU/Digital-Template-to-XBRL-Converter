import re
from importlib.metadata import PackageNotFoundError, version
from typing import NamedTuple, Self


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

    @classmethod
    def parse(cls, version_str: str) -> Self:
        version_str = version_str.strip()
        match = re.match(r"^(\d+)\.(\d+)\.(\d+)(.*)?$", version_str)
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


try:
    OUR_VERSION = version("EFRAG-DigitalTemplateToXBRL-Converter")
    OUR_VERSION_HOLDER = VersionHolder.parse(OUR_VERSION)
except PackageNotFoundError:
    OUR_VERSION = "(unknown version)"
    OUR_VERSION_HOLDER = VersionHolder(0, 0, 0, "(unknown version)")
