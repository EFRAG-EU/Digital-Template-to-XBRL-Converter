"""Which template version is 'the latest' for a taxonomy, and what it's judged by."""

import sys
import types

import pytest

from mireport.data.disclosures import getTemplateVersionSetting
from mireport.entrypoints import entryPointSetOf
from mireport.template_versions import (
    SupportedVersionSource,
    migrationTargetTemplateVersion,
    resolveSupportedTemplate,
)
from mireport.version import OUR_VERSION_HOLDER, VersionHolder
from mireport.xlsx_template_reader.processor import (
    TemplateCheckResult,
    VersionRelation,
)

CONVERTER_VERSION = OUR_VERSION_HOLDER.strip_build_metadata
VSME_ENTRY_POINT = "https://xbrl.efrag.org/taxonomy/vsme/2026-05-01/vsme-all.xsd"
VS_ENTRY_POINT = "https://xbrl.efrag.org/taxonomy/vs/2026-10-16/vs-all.xsd"
UNLISTED_ENTRY_POINT = "https://example.org/taxonomy/other/all.xsd"


@pytest.fixture()
def migration_target(monkeypatch: pytest.MonkeyPatch):
    """``migration_target("1.3.0")`` fakes the version the installed migration
    tool migrates into; ``migration_target(None)`` fakes there being no tool."""

    def _set(version: str | None) -> None:
        parsed = None if version is None else VersionHolder.parse(version)
        monkeypatch.setattr(
            "mireport.template_versions.migrationTargetTemplateVersion",
            lambda: parsed,
        )

    return _set


class TestConfiguredSettings:
    def test_vs_has_an_explicit_version(self):
        assert getTemplateVersionSetting(entryPointSetOf(VS_ENTRY_POINT)) == "2.0.0"

    def test_vsme_follows_the_migration_tool(self):
        assert getTemplateVersionSetting(entryPointSetOf(VSME_ENTRY_POINT)) == "auto"

    def test_unlisted_entry_point_has_no_setting(self):
        assert getTemplateVersionSetting(entryPointSetOf(UNLISTED_ENTRY_POINT)) is None


class TestResolveSupportedTemplate:
    def test_explicit_version(self, migration_target):
        migration_target(None)
        supported = resolveSupportedTemplate(VS_ENTRY_POINT)
        assert supported.supported_template_version == VersionHolder.parse("2.0.0")
        assert (
            supported.supported_template_version_source
            is SupportedVersionSource.TAXONOMY_CONFIG
        )
        assert supported.migration_enabled is False

    def test_explicit_version_can_migrate_when_the_tool_produces_it(
        self, migration_target
    ):
        migration_target("2.0.0")
        assert resolveSupportedTemplate(VS_ENTRY_POINT).migration_enabled is True

    def test_explicit_version_cannot_migrate_to_a_different_target(
        self, migration_target
    ):
        migration_target("1.3.0")
        assert resolveSupportedTemplate(VS_ENTRY_POINT).migration_enabled is False

    def test_auto_asks_the_migration_tool(self, migration_target):
        migration_target("1.3.0")
        supported = resolveSupportedTemplate(VSME_ENTRY_POINT)
        assert supported.supported_template_version == VersionHolder.parse("1.3.0")
        assert (
            supported.supported_template_version_source
            is SupportedVersionSource.MIGRATION_TOOL
        )
        assert supported.migration_enabled is True

    def test_auto_without_a_tool_knows_no_version(self, migration_target):
        """Not the converter's: its version says nothing about the templates'."""
        migration_target(None)
        supported = resolveSupportedTemplate(VSME_ENTRY_POINT)
        assert supported.supported_template_version is None
        assert (
            supported.supported_template_version_source
            is SupportedVersionSource.MIGRATION_TOOL
        )
        assert supported.migration_enabled is False

    def test_none_disables_migration_even_when_the_tool_agrees(
        self, migration_target, monkeypatch
    ):
        monkeypatch.setattr(
            "mireport.template_versions.getTemplateVersionSetting",
            lambda entryPointSet: "none",
        )
        migration_target(str(CONVERTER_VERSION))
        supported = resolveSupportedTemplate(VSME_ENTRY_POINT)
        assert supported.supported_template_version == CONVERTER_VERSION
        assert (
            supported.supported_template_version_source
            is SupportedVersionSource.CONVERTER
        )
        assert supported.migration_enabled is False

    def test_unlisted_entry_point_uses_the_converter_version(self, migration_target):
        migration_target(str(CONVERTER_VERSION))
        supported = resolveSupportedTemplate(UNLISTED_ENTRY_POINT)
        assert supported.supported_template_version == CONVERTER_VERSION
        assert (
            supported.supported_template_version_source
            is SupportedVersionSource.CONVERTER
        )
        assert supported.migration_enabled is True

    @pytest.mark.parametrize("entry_point", ["", "   ", "not a uri"])
    def test_blank_or_junk_entry_point_uses_the_converter_version(
        self, entry_point, migration_target
    ):
        migration_target(None)
        supported = resolveSupportedTemplate(entry_point)
        assert supported.supported_template_version == CONVERTER_VERSION
        assert (
            supported.supported_template_version_source
            is SupportedVersionSource.CONVERTER
        )

    def test_a_bad_configured_version_fails_loudly(self, migration_target, monkeypatch):
        monkeypatch.setattr(
            "mireport.template_versions.getTemplateVersionSetting",
            lambda entryPointSet: "two",
        )
        with pytest.raises(ValueError, match="two"):
            resolveSupportedTemplate(VS_ENTRY_POINT)

    def test_source_reads_as_the_end_of_a_sentence(self):
        assert f"from {SupportedVersionSource.MIGRATION_TOOL}" == (
            "from the migration tool"
        )


class TestMigrationTargetTemplateVersion:
    @pytest.fixture(autouse=True)
    def _fresh_cache(self):
        migrationTargetTemplateVersion.cache_clear()
        yield
        migrationTargetTemplateVersion.cache_clear()

    @staticmethod
    def _fake_tool(monkeypatch, **attrs):
        module = types.ModuleType("migration_tool")
        for name, value in attrs.items():
            setattr(module, name, value)
        monkeypatch.setitem(sys.modules, "migration_tool", module)

    def test_reads_the_tools_target_version(self, monkeypatch):
        target = types.SimpleNamespace(target_template_version="2.0.0+build.7")
        self._fake_tool(monkeypatch, migration_target=lambda: target)
        assert migrationTargetTemplateVersion() == VersionHolder.parse("2.0.0")

    def test_no_tool_installed(self, monkeypatch):
        monkeypatch.setitem(sys.modules, "migration_tool", None)  # import fails
        assert migrationTargetTemplateVersion() is None

    def test_a_tool_without_the_target_api(self, monkeypatch):
        self._fake_tool(monkeypatch)  # an older tool: no migration_target
        assert migrationTargetTemplateVersion() is None

    def test_a_tool_that_cannot_say(self, monkeypatch):
        def _boom():
            raise ValueError("no version in its template")

        self._fake_tool(monkeypatch, migration_target=_boom)
        assert migrationTargetTemplateVersion() is None

    def test_an_unusable_target_version(self, monkeypatch):
        target = types.SimpleNamespace(target_template_version="soon")
        self._fake_tool(monkeypatch, migration_target=lambda: target)
        assert migrationTargetTemplateVersion() is None


class TestVersionRelation:
    SUPPORTED = VersionHolder.parse("2.1.3")

    @pytest.mark.parametrize(
        "report,expected",
        [
            ("", VersionRelation.MISSING),
            ("   ", VersionRelation.MISSING),
            ("not-a-version", VersionRelation.INVALID),
            ("2.1.3", VersionRelation.SAME),
            ("2.1.4", VersionRelation.NEWER),
            ("2.2.0", VersionRelation.NEWER),
            ("3.0.0", VersionRelation.NEWER),
            (
                "2.1.3+build.9",
                VersionRelation.PATCH_BEHIND,
            ),  # not the same exact version
            ("2.1.2", VersionRelation.PATCH_BEHIND),
            ("2.1.3-rc.1", VersionRelation.PATCH_BEHIND),  # a prerelease is older
            ("2.0.9", VersionRelation.OLDER),
            ("1.9.9", VersionRelation.OLDER),
        ],
    )
    def test_relation(self, report, expected):
        check = TemplateCheckResult(
            report_template_version=VersionHolder.parse_or_invalid(report),
            supported_template_version=self.SUPPORTED,
            supported_template_version_source=SupportedVersionSource.CONVERTER,
            migration_enabled=False,
            validation_is_incomplete=False,
            migration_status=None,
        )
        assert check.report_template_version_relation is expected

    @pytest.mark.parametrize(
        "report,expected",
        [
            ("", VersionRelation.MISSING),
            ("not-a-version", VersionRelation.INVALID),
            ("1.0.0", VersionRelation.UNKNOWN),
            ("99.0.0", VersionRelation.UNKNOWN),
        ],
    )
    def test_relation_to_no_known_version(self, report, expected):
        check = TemplateCheckResult(
            report_template_version=VersionHolder.parse_or_invalid(report),
            supported_template_version=None,
            supported_template_version_source=SupportedVersionSource.MIGRATION_TOOL,
            migration_enabled=False,
            validation_is_incomplete=False,
            migration_status=None,
        )
        assert check.report_template_version_relation is expected
