import operator
import re

import pytest

from mireport.version import (
    OUR_VERSION,
    OUR_VERSION_HOLDER,
    VersionHolder,
    VersionInformationTuple,
)

_PACKAGE_INSTALLED = OUR_VERSION != "(unknown version)"

# Official semver regex — oracle for tests, not used in production code.
_SEMVER_RE = re.compile(
    r"^(?P<major>0|[1-9]\d*)\.(?P<minor>0|[1-9]\d*)\.(?P<patch>0|[1-9]\d*)"
    r"(?:-(?P<prerelease>(?:0|[1-9]\d*|\d*[a-zA-Z-][0-9a-zA-Z-]*)"
    r"(?:\.(?:0|[1-9]\d*|\d*[a-zA-Z-][0-9a-zA-Z-]*))*))?"
    r"(?:\+(?P<buildmetadata>[0-9a-zA-Z-]+(?:\.[0-9a-zA-Z-]+)*))?$"
)

# Shared invalid version strings — used by TestVersionHolderParse for both parse() and parse_safe().
_INVALID_VERSION_STRINGS = ["", "1.0", "1.0.x", "not-a-version", "bad"]


class TestVersionInformationTuple:
    @pytest.mark.parametrize(
        "name,ver,expected",
        [
            ("MyTool", "1.2.3", "MyTool (version 1.2.3)"),
            ("Tool", "0.0.1-alpha", "Tool (version 0.0.1-alpha)"),
        ],
    )
    def test_str(self, name, ver, expected):
        assert str(VersionInformationTuple(name, ver)) == expected


class TestVersionHolderParse:
    @pytest.mark.parametrize(
        "version_str,expected",
        [
            ("1.0.0", VersionHolder(1, 0, 0, "")),
            ("1.2.3", VersionHolder(1, 2, 3, "")),
            ("1.0.0-alpha", VersionHolder(1, 0, 0, "-alpha")),
            ("1.0.0-rc.1", VersionHolder(1, 0, 0, "-rc.1")),
            ("1.0.0+build.1", VersionHolder(1, 0, 0, "+build.1")),
            ("1.0.0-alpha+build.1", VersionHolder(1, 0, 0, "-alpha+build.1")),
            ("  1.2.3  ", VersionHolder(1, 2, 3, "")),
            ("0.1.0", VersionHolder(0, 1, 0, "")),
            ("0.00.0", VersionHolder(0, 0, 0, "")),  # leniency: leading zeros accepted
        ],
    )
    def test_parse_valid(self, version_str, expected):
        assert VersionHolder.parse(version_str) == expected

    @pytest.mark.parametrize("version_str", _INVALID_VERSION_STRINGS)
    def test_parse_invalid(self, version_str):
        with pytest.raises(ValueError):
            VersionHolder.parse(version_str)
        assert VersionHolder.parse_safe(version_str) is None

    def test_parse_safe_valid(self):
        assert VersionHolder.parse_safe("1.2.3") == VersionHolder(1, 2, 3, "")

    def test_parse_safe_strips_whitespace(self):
        assert VersionHolder.parse_safe("  1.2.3  ") == VersionHolder(1, 2, 3, "")

    @pytest.mark.parametrize(
        "holder,expected_str",
        [
            (VersionHolder(1, 2, 3, ""), "1.2.3"),
            (VersionHolder(1, 2, 3, "-alpha"), "1.2.3-alpha"),
            (VersionHolder(1, 0, 0, "+build.1"), "1.0.0+build.1"),
            (VersionHolder(0, 0, 1, "-rc.1+meta"), "0.0.1-rc.1+meta"),
        ],
    )
    def test_str(self, holder, expected_str):
        assert str(holder) == expected_str


class TestVersionHolderProperties:
    @pytest.mark.parametrize(
        "version_str,expected_prerelease,expected_buildmetadata",
        [
            ("1.0.0", None, None),
            ("1.0.0-alpha", "alpha", None),
            ("1.0.0-beta", "beta", None),
            ("1.0.0-rc.1", "rc.1", None),
            ("1.0.0+build.1", None, "build.1"),
            ("1.0.0-alpha+build.1", "alpha", "build.1"),
        ],
    )
    def test_semver_structure(
        self, version_str, expected_prerelease, expected_buildmetadata
    ):
        m = _SEMVER_RE.match(version_str)
        assert m is not None, f"{version_str!r} did not match the official semver regex"
        assert m.group("prerelease") == expected_prerelease
        assert m.group("buildmetadata") == expected_buildmetadata

        v = VersionHolder.parse(version_str)
        assert v.prerelease == expected_prerelease
        assert v.build_metadata == expected_buildmetadata

    @pytest.mark.parametrize(
        "version_str,expected",
        [
            ("1.0.0", False),
            ("1.0.0-alpha", True),
            ("1.0.0-rc.1", True),
            ("1.0.0+build.1", False),
            ("1.0.0-alpha+build.1", True),
        ],
    )
    def test_is_prerelease(self, version_str, expected):
        assert VersionHolder.parse(version_str).is_prerelease == expected

    @pytest.mark.parametrize(
        "version_str,expected",
        [
            ("1.0.0", "1.0.0"),
            ("1.0.0+build.1", "1.0.0"),
            ("1.0.0-alpha+build.1", "1.0.0-alpha"),
            ("1.0.0-alpha", "1.0.0-alpha"),
        ],
    )
    def test_strip_build_metadata(self, version_str, expected):
        assert str(VersionHolder.parse(version_str).strip_build_metadata) == expected


class TestVersionHolderEquality:
    @pytest.mark.parametrize(
        "a,b,expected",
        [
            ("1.0.0", "1.0.0", True),
            ("1.0.0-alpha", "1.0.0-alpha", True),
            ("1.0.0-alpha", "1.0.0", False),
            ("1.0.0", "2.0.0", False),
            ("1.0.0-alpha", "1.0.0-beta", False),
        ],
    )
    def test_equality(self, a, b, expected):
        assert (VersionHolder.parse(a) == VersionHolder.parse(b)) == expected

    def test_equality_with_non_version_returns_false(self):
        # tuple.__eq__ returns False (not NotImplemented) when compared to an unlike type
        assert VersionHolder.parse("1.0.0") != "1.0.0"

    def test_hashable(self):
        hash(VersionHolder.parse("1.0.0"))  # must not raise

    def test_equal_instances_same_hash(self):
        a = VersionHolder.parse("1.0.0-alpha")
        b = VersionHolder.parse("1.0.0-alpha")
        assert a == b
        assert hash(a) == hash(b)

    def test_usable_in_set(self):
        versions = {
            VersionHolder.parse("1.0.0"),
            VersionHolder.parse("1.0.0-alpha"),
            VersionHolder.parse("1.0.0"),
        }
        assert len(versions) == 2
        assert VersionHolder.parse("1.0.0") in versions


class TestVersionHolderOrdering:
    @pytest.mark.parametrize(
        "a,b,expected",
        [
            # numeric ordering
            ("1.0.0", "2.0.0", True),
            ("1.0.0", "1.1.0", True),
            ("1.0.0", "1.0.1", True),
            ("2.0.0", "1.0.0", False),
            ("1.0.0", "1.0.0", False),
            # pre-release vs release (semver §11.3)
            ("1.0.0-alpha", "1.0.0", True),
            ("1.0.0", "1.0.0-alpha", False),
            # pre-release ordering (lexical within same major.minor.patch)
            ("1.0.0-alpha", "1.0.0-beta", True),
            ("1.0.0-beta", "1.0.0-alpha", False),
            # different patch
            ("1.0.0", "1.0.1-alpha", True),
            # build metadata treated as non-pre-release (semver §10 — same precedence as release)
            ("1.0.0+build.1", "1.0.0", False),
            ("1.0.0", "1.0.0+build.1", False),
            ("1.0.0-alpha", "1.0.0+build.1", True),
            ("1.0.0-alpha+build.1", "1.0.0", True),
            # build-metadata variants also have same precedence per §10 (a != b but neither a<b nor b<a)
            ("1.0.0+build.1", "1.0.0+build.2", False),
            ("1.0.0+build.2", "1.0.0+build.1", False),
        ],
    )
    def test_less_than(self, a, b, expected):
        assert (VersionHolder.parse(a) < VersionHolder.parse(b)) == expected

    @pytest.mark.parametrize(
        "method,name",
        [
            (VersionHolder.__lt__, "__lt__"),
            (VersionHolder.__gt__, "__gt__"),
            (VersionHolder.__le__, "__le__"),
            (VersionHolder.__ge__, "__ge__"),
        ],
    )
    def test_comparison_non_version_returns_not_implemented(self, method, name):
        result = method(VersionHolder.parse("1.0.0"), "1.0.0")
        assert result is NotImplemented, (
            f"{name} should return NotImplemented for non-VersionHolder"
        )

    @pytest.mark.parametrize(
        "a,b,op,expected",
        [
            ("1.0.0", "1.0.0-alpha", operator.gt, True),
            ("1.0.0-alpha", "1.0.0", operator.gt, False),
            ("1.0.0+build.1", "1.0.0", operator.gt, False),  # §10: equal precedence
            ("1.0.0-alpha", "1.0.0", operator.le, True),
            ("1.0.0", "1.0.0", operator.le, True),
            ("1.0.0", "1.0.0+build.1", operator.le, True),  # §10: equal precedence
            ("1.0.0", "1.0.0", operator.ge, True),
            ("1.0.0", "1.0.0-alpha", operator.ge, True),
            ("1.0.0-alpha", "1.0.0", operator.ge, False),
            ("1.0.0", "1.0.0+build.1", operator.ge, True),  # §10: equal precedence
        ],
    )
    def test_derived_operators(self, a, b, op, expected):
        assert op(VersionHolder.parse(a), VersionHolder.parse(b)) == expected

    @pytest.mark.parametrize(
        "unsorted,expected",
        [
            # basic ordering across all semver precedence rules
            (
                ["1.0.0", "2.0.0-alpha", "1.0.0-beta", "2.0.0", "1.0.1"],
                ["1.0.0-beta", "1.0.0", "1.0.1", "2.0.0-alpha", "2.0.0"],
            ),
            # §10: build metadata has equal precedence — stable sort preserves input order (release first)
            (
                [
                    "1.0.0",
                    "2.0.0-alpha",
                    "1.0.0-beta",
                    "2.0.0",
                    "1.0.1",
                    "1.0.0+build.1",
                ],
                [
                    "1.0.0-beta",
                    "1.0.0",
                    "1.0.0+build.1",
                    "1.0.1",
                    "2.0.0-alpha",
                    "2.0.0",
                ],
            ),
            # §10: build metadata has equal precedence — stable sort preserves input order (build first)
            (
                [
                    "1.0.0+build.1",
                    "2.0.0-alpha",
                    "1.0.0-beta",
                    "2.0.0",
                    "1.0.1",
                    "1.0.0",
                ],
                [
                    "1.0.0-beta",
                    "1.0.0+build.1",
                    "1.0.0",
                    "1.0.1",
                    "2.0.0-alpha",
                    "2.0.0",
                ],
            ),
        ],
    )
    def test_sortable(self, unsorted, expected):
        assert [
            str(v) for v in sorted(VersionHolder.parse(s) for s in unsorted)
        ] == expected


class TestModuleExports:
    @pytest.mark.skipif(not _PACKAGE_INSTALLED, reason="package not pip-installed")
    def test_our_version_importable(self):
        assert isinstance(OUR_VERSION, str)
        assert len(OUR_VERSION) > 0
        assert isinstance(OUR_VERSION_HOLDER, VersionHolder)
        assert str(OUR_VERSION_HOLDER) == OUR_VERSION
        assert _SEMVER_RE.match(OUR_VERSION) is not None, (
            f"OUR_VERSION {OUR_VERSION!r} is not valid semver"
        )
