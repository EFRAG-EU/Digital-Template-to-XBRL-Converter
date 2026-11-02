"""Behavioural tests for the migration gate: doMigrationChecks classification
and what checkMigration does with it.

Everything runs the real read -> checkTemplate -> doMigrationChecks path over
in-memory workbooks. The migration tool's target version is faked (see
``migration_target``) so the outcomes don't depend on which tool is installed.
"""

from html import unescape
from io import BytesIO
from pathlib import Path

import pytest
from cachelib.file import FileSystemCache
from openpyxl import Workbook
from openpyxl.utils.cell import absolute_coordinate, quote_sheetname
from openpyxl.workbook.defined_name import DefinedName

from digital_converter_webapp import create_app
from digital_converter_webapp.conversion import Conversion
from digital_converter_webapp.migration import (
    MIGRATION_WORKING,
    MigrationOutcome,
    checkMigration,
    doMigrationChecks,
)
from mireport.filesupport import FilelikeAndFileName
from mireport.version import OUR_VERSION_HOLDER, VersionHolder
from mireport.xlsx_template_reader.util import loadExcelFromPathOrFileLike

_SHEET = "S"
CONVERTER_VERSION = OUR_VERSION_HOLDER.strip_build_metadata
VSME_ENTRY_POINT = "https://xbrl.efrag.org/taxonomy/vsme/2026-05-01/vsme-all.xsd"
VS_ENTRY_POINT = "https://xbrl.efrag.org/taxonomy/vs/2026-10-16/vs-all.xsd"
SAMPLE_1_3_0 = (
    Path(__file__).parents[2]
    / "digital-templates"
    / "VSME-Digital-Template-Sample-1.3.0.xlsx"
)


def _workbook_bytes(named_cells: dict[str, object]) -> bytes:
    """Serialise a workbook holding the given named single cells to xlsx bytes."""
    wb = Workbook()
    ws = wb.active
    assert ws is not None
    ws.title = _SHEET
    for row, (name, value) in enumerate(named_cells.items(), start=1):
        ws.cell(row=row, column=1).value = value
        attr = f"{quote_sheetname(_SHEET)}!{absolute_coordinate(f'A{row}')}"
        wb.defined_names[name] = DefinedName(name, attr_text=attr)
    buf = BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _conversion(report_cells: dict[str, object] | bytes) -> Conversion:
    data = (
        report_cells
        if isinstance(report_cells, bytes)
        else _workbook_bytes(report_cells)
    )
    conv = Conversion({"id": "test"})
    conv.excel = FilelikeAndFileName(fileContent=data, filename="report.xlsx")
    return conv


def _report(version: object, entry_point: str | None = None, **extra: object):
    cells: dict[str, object] = {"template_reporting_template_version": version}
    if entry_point is not None:
        cells["template_reporting_schemaRef"] = entry_point
    return cells | extra


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

    _set(None)
    return _set


class TestInvalidOrMissingVersion:
    @pytest.mark.parametrize(
        "raw_version,expected_display",
        [
            ("not-a-version", "not-a-version"),  # unparseable
            ("", ""),  # missing/blank
            (None, ""),  # blank cell
        ],
    )
    def test_is_invalid_format(self, raw_version, expected_display, migration_target):
        outcome, report_template_version = doMigrationChecks(
            _conversion(_report(raw_version))
        )
        assert outcome is MigrationOutcome.INVALID_FORMAT
        # display string is the raw input, not the misleading "0.0.0..."
        assert report_template_version == expected_display

    def test_no_version_range_at_all_is_invalid_format(self, migration_target):
        outcome, _ = doMigrationChecks(_conversion({}))
        assert outcome is MigrationOutcome.INVALID_FORMAT

    def test_unreadable_file_is_missing(self, migration_target):
        outcome, report_template_version = doMigrationChecks(
            _conversion(b"not an xlsx")
        )
        assert outcome is MigrationOutcome.MISSING
        assert report_template_version == "unknown"


class TestVersionAgainstTaxonomySupportedVersion:
    def test_vs_template_at_its_configured_version_is_fine(self, migration_target):
        # the migration tool would produce VSME 1.3.0; it must not matter to VS
        migration_target("1.3.0")
        outcome, _ = doMigrationChecks(_conversion(_report("2.0.0", VS_ENTRY_POINT)))
        assert outcome is MigrationOutcome.SUCCESS

    def test_vs_template_newer_than_configured_is_too_new(self, migration_target):
        outcome, _ = doMigrationChecks(_conversion(_report("2.0.1", VS_ENTRY_POINT)))
        assert outcome is MigrationOutcome.VERSION_TOO_NEW

    def test_older_vs_template_without_a_matching_tool_cannot_migrate(
        self, migration_target
    ):
        migration_target("1.3.0")
        outcome, _ = doMigrationChecks(_conversion(_report("1.0.0", VS_ENTRY_POINT)))
        assert outcome is MigrationOutcome.NO_MIGRATION

    def test_older_vs_template_with_a_matching_tool_must_migrate(
        self, migration_target
    ):
        migration_target("2.0.0")
        outcome, _ = doMigrationChecks(_conversion(_report("1.0.0", VS_ENTRY_POINT)))
        assert outcome is MigrationOutcome.MIGRATION_REQUIRED


class TestVsmeTemplatesFollowTheMigrationTool:
    def test_current_tool_target_is_up_to_date(self, migration_target):
        migration_target("1.3.0")
        outcome, _ = doMigrationChecks(_conversion(_report("1.3.0", VSME_ENTRY_POINT)))
        assert outcome is MigrationOutcome.SUCCESS

    def test_patch_behind_offers_optional_migration(self, migration_target):
        migration_target("1.3.0")
        outcome, _ = doMigrationChecks(
            _conversion(_report("1.3.0-rc.1", VSME_ENTRY_POINT))
        )
        assert outcome is MigrationOutcome.MIGRATION_OPTIONAL

    def test_older_major_minor_must_migrate(self, migration_target):
        migration_target("1.3.0")
        outcome, _ = doMigrationChecks(_conversion(_report("1.0.0", VSME_ENTRY_POINT)))
        assert outcome is MigrationOutcome.MIGRATION_REQUIRED

    def test_incomplete_older_report_cannot_migrate(self, migration_target):
        migration_target("1.3.0")
        report = _report(
            "1.0.0",
            VSME_ENTRY_POINT,
            template_overall_validation_status="INCOMPLETE",
            template_label_incomplete="INCOMPLETE",
        )
        outcome, _ = doMigrationChecks(_conversion(report))
        assert outcome is MigrationOutcome.NOT_COMPLETE

    def test_vsme_1_3_0_must_migrate_once_the_tool_targets_vs(self, migration_target):
        migration_target("2.0.0")
        outcome, _ = doMigrationChecks(_conversion(_report("1.3.0", VSME_ENTRY_POINT)))
        assert outcome is MigrationOutcome.MIGRATION_REQUIRED

    @pytest.mark.parametrize("version", ["1.0.0", "1.3.0", "99.0.0"])
    def test_without_a_tool_no_version_is_judged(self, version, migration_target):
        """The converter's version says nothing about VSME templates'."""
        migration_target(None)
        outcome, _ = doMigrationChecks(_conversion(_report(version, VSME_ENTRY_POINT)))
        assert outcome is MigrationOutcome.NO_MIGRATION


class TestNotRefreshed:
    def test_unrefreshed_migrated_report_wins_over_the_version(self, migration_target):
        outcome, _ = doMigrationChecks(
            _conversion(
                _report("1.3.0", VSME_ENTRY_POINT, template_migration_status=None)
            )
        )
        assert outcome is MigrationOutcome.NOT_REFRESHED


@pytest.fixture(scope="module")
def migration_app(tmp_path_factory):
    return create_app(
        {
            "TESTING": True,
            "SESSION_CACHELIB": FileSystemCache(
                str(tmp_path_factory.mktemp("migration-sessions"))
            ),
            "SECRET_KEY": "test-secret",
            "ENABLE_MIGRATION": True,
            "RUN_STEPS_INLINE": True,
        }
    )


class TestCheckMigration:
    """What the gate does with each outcome: unusable versions fall through to
    the conversion, which names the problem and its cell."""

    @pytest.mark.parametrize("version", ["", "not-a-version"])
    def test_invalid_version_continues_to_conversion(
        self, version, migration_app, migration_target
    ):
        conv = _conversion(_report(version))
        with migration_app.test_request_context():
            assert checkMigration(conv) is None
        assert conv.migration_outcome == str(MigrationOutcome.INVALID_FORMAT)
        assert conv.report_template_version == version

    def test_too_new_version_continues_to_conversion(
        self, migration_app, migration_target
    ):
        conv = _conversion(_report("2.0.1", VS_ENTRY_POINT))
        with migration_app.test_request_context():
            assert checkMigration(conv) is None
        assert conv.migration_outcome == str(MigrationOutcome.VERSION_TOO_NEW)

    def test_required_migration_redirects_to_the_migration_page(
        self, migration_app, migration_target
    ):
        migration_target("1.3.0")
        conv = _conversion(_report("1.0.0", VSME_ENTRY_POINT))
        with migration_app.test_request_context():
            response = checkMigration(conv)
        assert response is not None
        assert response.status_code == 303
        assert "/migrationPage/" in response.headers["Location"]

    def test_unreadable_file_redirects_to_the_index(
        self, migration_app, migration_target
    ):
        conv = _conversion(b"not an xlsx")
        with migration_app.test_request_context():
            response = checkMigration(conv)
        assert response is not None
        assert response.headers["Location"] == "/"


class TestVersionlessUploadShowsThePreciseError:
    """The reported bug: a template with a blank version got a bare
    'Invalid report format' flash and no clue where the problem was."""

    def test_results_page_names_the_missing_version_and_its_cell(
        self, migration_app, migration_target
    ):
        wb = loadExcelFromPathOrFileLike(SAMPLE_1_3_0.open("rb"))
        defined = wb.defined_names["template_reporting_template_version"]
        ((sheet, cell),) = defined.destinations
        wb[sheet][cell.replace("$", "")].value = None
        buf = BytesIO()
        wb.save(buf)

        client = migration_app.test_client()
        upload = client.post(
            "/upload",
            data={"file": (BytesIO(buf.getvalue()), "no-version.xlsx")},
            content_type="multipart/form-data",
        )
        assert upload.status_code == 303
        page = client.get(upload.headers["Location"])

        assert page.status_code == 200
        body = page.get_data(as_text=True)
        assert "no version recorded" in body
        assert f"!{cell}" in body  # the cell, named so the user can find it


SAMPLE_1_2_0 = SAMPLE_1_3_0.with_name("VSME-Digital-Template-Sample-1.2.0.xlsx")


def _withPrior(prior: dict[str, object] | bytes, filename: str = "last-year.xlsx"):
    """A conversion of an up-to-date workbook, with last year's report beside it."""
    conv = _conversion(_report("1.3.0", VSME_ENTRY_POINT))
    data = prior if isinstance(prior, bytes) else _workbook_bytes(prior)
    conv.priorReport = FilelikeAndFileName(fileContent=data, filename=filename)
    return conv


class TestCheckPriorMigration:
    """Last year's workbook is only offered migration; the conversion reports
    anything else wrong with it, and other kinds of report have nothing to
    migrate."""

    def test_older_prior_workbook_is_offered_migration(
        self, migration_app, migration_target
    ):
        migration_target("1.3.0")
        conv = _withPrior(_report("1.0.0", VSME_ENTRY_POINT))
        with migration_app.test_request_context():
            assert checkMigration(conv) is None
            response = checkMigration(conv.prior)
        assert response is not None
        assert response.status_code == 303
        assert response.headers["Location"] == "/migrationPage/test?prior=True"
        assert conv.prior.migration_outcome == str(MigrationOutcome.MIGRATION_REQUIRED)
        assert conv.prior.report_template_version == "1.0.0"
        assert conv.migration_outcome == str(MigrationOutcome.SUCCESS)

    @pytest.mark.parametrize(
        "prior",
        [
            pytest.param(_report("1.3.0", VSME_ENTRY_POINT), id="up to date"),
            pytest.param(
                _report("1.3.0", VSME_ENTRY_POINT, template_migration_status=None),
                id="not saved since migrating",
            ),
            pytest.param(
                _report(
                    "1.0.0",
                    VSME_ENTRY_POINT,
                    template_overall_validation_status="INCOMPLETE",
                    template_label_incomplete="INCOMPLETE",
                ),
                id="incomplete",
            ),
            pytest.param(b"not an xlsx", id="unreadable"),
        ],
    )
    def test_any_other_prior_workbook_goes_on_to_the_conversion(
        self, prior, migration_app, migration_target
    ):
        migration_target("1.3.0")
        with migration_app.test_request_context():
            assert checkMigration(_withPrior(prior).prior) is None

    @pytest.mark.parametrize("filename", ["last-year.json", "last-year.xhtml"])
    def test_other_kinds_of_report_are_not_checked(
        self, filename, migration_app, migration_target
    ):
        conv = _withPrior(b"{}", filename)
        with migration_app.test_request_context():
            assert checkMigration(conv.prior) is None
        assert conv.prior.migration_outcome == ""

    def test_no_prior_report_is_not_checked(self, migration_app, migration_target):
        with migration_app.test_request_context():
            assert checkMigration(_conversion({}).prior) is None


def _seed(client, conv: Conversion) -> None:
    with client.session_transaction() as sess:
        sess[conv.id] = dict(conv.raw)


class TestPriorMigrationPage:
    def test_offers_migrating_last_years_workbook_or_using_it_as_it_is(
        self, migration_app
    ):
        client = migration_app.test_client()
        _seed(client, _withPrior(b"old"))
        page = client.get("/migrationPage/test?prior=True")
        assert page.status_code == 200
        body = unescape(page.get_data(as_text=True))
        assert "Last year's report is a VSME Template of an older version" in body
        assert "last-year.xlsx" in body
        assert 'action="/migrationButton/test?prior=True"' in body
        assert "/conversions/test?skip_migration=True&skip_prior_migration=True" in body
        assert "Use last year's workbook as it is" in body

    def test_this_years_page_is_unchanged(self, migration_app):
        client = migration_app.test_client()
        _seed(client, _withPrior(b"old"))
        body = client.get("/migrationPage/test").get_data(as_text=True)
        assert "report.xlsx" in body
        assert 'action="/migrationButton/test"' in body
        assert "skip_prior_migration" not in body
        assert "Convert with old version" in body


@pytest.mark.slow
@pytest.mark.skipif(not MIGRATION_WORKING, reason="needs the migration tool")
class TestMigratingLastYearsWorkbook:
    """The 1.2.0 sample is last year's report for the 1.3.0 one."""

    def _upload(self, client, prior: bytes, priorName: str) -> str:
        upload = client.post(
            "/upload",
            data={
                "file": (BytesIO(SAMPLE_1_3_0.read_bytes()), "this-year.xlsx"),
                "prior": (BytesIO(prior), priorName),
            },
            content_type="multipart/form-data",
        )
        assert upload.status_code == 303
        return upload.headers["Location"]

    def test_migrate_it_or_use_it_as_it_is(self, migration_app, migration_target):
        migration_target("1.3.0")
        client = migration_app.test_client()
        conversion = self._upload(client, SAMPLE_1_2_0.read_bytes(), SAMPLE_1_2_0.name)
        id = conversion.rpartition("/")[2]
        offer = client.get(conversion)
        assert offer.status_code == 303
        assert offer.headers["Location"] == f"/migrationPage/{id}?prior=True"

        migrated = client.post(f"/migrationButton/{id}?prior=True")
        assert migrated.headers["Location"] == f"/migrationPage/{id}?prior=True"
        download = client.get(f"/downloadMigrated/{id}?prior=True")
        assert download.status_code == 200
        assert (
            "VSME-Digital-Template-Sample-1.2.0_migrated_to_latest_version.xlsx"
            in download.headers["Content-Disposition"]
        )
        assert client.get(f"/downloadMigrated/{id}").status_code == 404

        # Given again before Excel has saved it, it is refused.
        again = self._upload(client, download.data, "migrated.xlsx")
        body = unescape(client.get(again).get_data(as_text=True))
        assert (
            "migrated.xlsx has been migrated but not yet saved in Excel. "
            "Open it in Excel, save it, and give it again."
        ) in body

        # Or the old workbook is used as it is.
        page = client.get(
            f"/conversions/{id}?skip_migration=True&skip_prior_migration=True"
        )
        assert page.status_code == 200
        body = unescape(page.get_data(as_text=True))
        assert f"taken from {SAMPLE_1_2_0.name}." in body
        assert f"Last year's report:</span> {SAMPLE_1_2_0.name}" in body
        # Converted as it is, it is listed with the others.
        assert f"/conversions/{id}" in client.get("/conversions/").get_data(
            as_text=True
        )
