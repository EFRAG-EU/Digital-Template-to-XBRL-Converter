from __future__ import annotations

import logging
from enum import StrEnum
from pathlib import PurePath
from typing import TYPE_CHECKING, Any

from flask import (
    Response,
    flash,
    jsonify,
    make_response,
    redirect,
    render_template,
    request,
    send_file,
    url_for,
)

try:
    from migration_tool import migrate_workbook_as_bytes

    MIGRATION_WORKING = True
except ImportError:
    logging.getLogger(__name__).warning(
        "Migration tool not available, migration functionality will be disabled",
        exc_info=True,
    )
    MIGRATION_WORKING = False

    def migrate_workbook_as_bytes(old_wb: Any) -> tuple[bytes, float, list[str]]:
        raise NotImplementedError("Migration tool not available")


from mireport.filesupport import FilelikeAndFileName
from mireport.xlsx_template_reader.processor import (
    OUR_VERSION_HOLDER,
    XlsxProcessor,
)

from .blueprints import convert_bp
from .conversion import ConversionStore

if TYPE_CHECKING:
    from .conversion import Conversion

L = logging.getLogger(__name__)


class MigrationOutcome(StrEnum):
    SUCCESS = "success"
    MISSING = "report_missing"
    NOT_COMPLETE = "validation_not_complete"
    INVALID_FORMAT = "invalid_report_format"
    NOT_REFRESHED = "report_not_refreshed"
    MIGRATION_OPTIONAL = "migration_optional"
    MIGRATION_REQUIRED = "migration_required"


def doMigrationChecks(conv: Conversion) -> tuple[MigrationOutcome, str]:
    upload = conv.excel
    assert upload is not None, "doMigrationChecks needs an uploaded Excel file"
    check_results = XlsxProcessor.checkReport(upload.fileLike())
    version = str(check_results.reported_version) if check_results else "unknown"

    if check_results is None:
        # can't do anything if we can't read the report
        return MigrationOutcome.MISSING, version
    if version == "0.0.0":
        # can't determine version, likely invalid report
        return MigrationOutcome.INVALID_FORMAT, version
    if check_results.migration_status is False:
        # report not refreshed after migration
        return MigrationOutcome.NOT_REFRESHED, version
    if check_results.version_is_same:
        # up-to-date version
        return MigrationOutcome.SUCCESS, version
    if check_results.version_major_minor_same:
        # optional migration offered
        return MigrationOutcome.MIGRATION_OPTIONAL, version
    # Definitely an old report that we want to force migrate to the latest version
    if check_results.validation_is_incomplete:
        # invalid report, migration cannot proceed.
        return MigrationOutcome.NOT_COMPLETE, version
    # older (major) version, must migrate
    return MigrationOutcome.MIGRATION_REQUIRED, version


def checkMigration(conv: Conversion) -> Response | None:
    """Run the migration checks, recording the outcome on ``conv``.

    Returns a redirect if the conversion must not go ahead, otherwise None.
    """
    outcome, conv.template_version = doMigrationChecks(conv)
    conv.migration_outcome = outcome
    match outcome:
        case MigrationOutcome.NOT_REFRESHED:
            flash("Open the migrated file and save it before conversion", "error")
            return make_response(redirect(url_for("basic.index")))
        case MigrationOutcome.MIGRATION_REQUIRED:
            return make_response(
                redirect(url_for("basic.migrationPage", id=conv.id), code=303)
            )
        case MigrationOutcome.NOT_COMPLETE:
            flash("Report validation is not complete", "error")
            return make_response(redirect(url_for("basic.index")))
        case MigrationOutcome.INVALID_FORMAT:
            flash("Invalid report format", "error")
            return make_response(redirect(url_for("basic.index")))
        case MigrationOutcome.MISSING:
            flash(
                "The uploaded file is not recognised as a valid digital template.",
                "error",
            )
            return make_response(redirect(url_for("basic.index")))
        case MigrationOutcome.MIGRATION_OPTIONAL | MigrationOutcome.SUCCESS:
            return None  # Continue with conversion
    return None


@convert_bp.route("/migrationPage/<id>", methods=["GET"])
def migrationPage(id: str) -> Response:
    try:
        if (conv := ConversionStore.fromFlask().get(id)) is None:
            flash("Conversion session expired", "error")
            return make_response(redirect(url_for("basic.index")))

        version = request.args.get("version", conv.template_version or "unknown")
        excel = conv.excel
        assert excel is not None

        return Response(
            render_template(
                "migration_page.html.jinja",
                conversion_id=id,
                filename=excel.filename,
                version=version,
                newest_version=OUR_VERSION_HOLDER.strip_build_metadata,
                has_migration_results=conv.has_migration_results,
                elapsed=conv.migration_elapsed,
                migration_issues=conv.migration_issues,
                migration_error=conv.migration_error,
            )
        )
    except Exception as e:
        L.exception("Exception during migration page display", exc_info=e)
        flash(f"Migration page failed to load: {e!s}", "error")
        return make_response(redirect(url_for("basic.index")))


@convert_bp.route("/migrationButton/<id>", methods=["POST"])
def migrationButton(id: str) -> Response:
    """Handle migration of old VSME templates to new version."""
    try:
        # Get the file from session
        if (conv := ConversionStore.fromFlask().get(id)) is None:
            L.warning("MigrationButton: session expired or missing id=%s", id)
            return make_response(jsonify({"error": "Conversion session expired"}), 401)

        if (original_excel := conv.excel) is None:
            L.warning("MigrationButton: no excel in session for id=%s", id)
            return make_response(jsonify({"error": "No file found in session"}), 400)

        # Clear any stale error from a previous failed attempt
        conv.migration_error = None
        conv.migrated_excel = None

        try:
            migrated_bytes, elapsed, migration_issues = migrate_workbook_as_bytes(
                original_excel.fileLike()
            )
        except NotImplementedError:
            L.error("Migration tool not available for id=%s", id)
            flash("Migration functionality is not available on this server.", "error")
            return make_response(redirect(url_for("basic.index")))
        except Exception as e:
            L.exception("Migration failed for id=%s", id)
            conv.migration_error = f"Migration failed: {e}"
            conv.migration_elapsed = None
            return make_response(
                redirect(url_for("basic.migrationPage", id=id), code=303)
            )

        o_path = PurePath(original_excel.filename)
        m_name = o_path.with_stem(f"{o_path.stem}_migrated_to_latest_version").name

        L.info(
            "MigrationButton: generated workbook size=%d bytes for id=%s",
            len(migrated_bytes),
            id,
        )

        # Store migrated file and results in session, then redirect to results view
        conv.migrated_excel = FilelikeAndFileName(
            fileContent=migrated_bytes, filename=m_name
        )
        conv.migration_elapsed = elapsed
        conv.migration_issues = list(migration_issues)

        return make_response(redirect(url_for("basic.migrationPage", id=id), code=303))

    except Exception:
        L.exception("Unexpected error in migrationButton for id=%s", id)
        flash("An error occurred during migration.", "error")
        return make_response(redirect(url_for("basic.index")))


@convert_bp.route("/downloadMigrated/<id>", methods=["GET", "HEAD"])
def downloadMigrated(id: str) -> Response:
    """Download the migrated file from the session."""
    if (conv := ConversionStore.fromFlask().get(id)) is None:
        return make_response({"error": "Conversion session expired / not found"}, 404)

    if (migrated_excel := conv.migrated_excel) is None:
        return make_response({"error": "No migrated file found"}, 404)

    if request.method == "HEAD":
        return Response(status=200, headers={"X-File-Ready": "true"})

    return send_file(
        migrated_excel.fileLike(),
        as_attachment=True,
        download_name=migrated_excel.filename,
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
