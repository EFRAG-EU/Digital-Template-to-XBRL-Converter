import logging
from enum import StrEnum
from pathlib import PurePath
from typing import Any

from flask import (
    Response,
    flash,
    json,
    jsonify,
    make_response,
    redirect,
    render_template,
    request,
    send_file,
    session,
    url_for,
)

try:
    from migration_tool import migrate_workbook_as_bytes

    MIGRATION_WORKING = True
except ImportError:
    logging.getLogger(__name__).warning(
        "Migration tool not available, migration functionality will be disabled"
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
from .conversion import Conversion, ConversionStore

L = logging.getLogger(__name__)


class MigrationOutcome(StrEnum):
    SUCCESS = "success"
    MISSING = "report_missing"
    NOT_COMPLETE = "validation_not_complete"
    INVALID_FORMAT = "invalid_report_format"
    NOT_REFRESHED = "report_not_refreshed"
    MIGRATION_OPTIONAL = "migration_optional"
    MIGRATION_REQUIRED = "migration_required"


def do_migration_checks(conv: Conversion) -> tuple[MigrationOutcome, str]:
    upload = conv.excel
    assert upload is not None, "do_migration_checks requires an uploaded Excel"
    check_results = XlsxProcessor.checkReport(upload.fileLike())
    version = str(check_results.reported_version) if check_results else "unknown"

    if check_results is None:
        return MigrationOutcome.MISSING, version
    if version == "0.0.0":
        return MigrationOutcome.INVALID_FORMAT, version
    if check_results.migration_status is False:
        return MigrationOutcome.NOT_REFRESHED, version
    if check_results.version_is_same:
        return MigrationOutcome.SUCCESS, version
    if check_results.version_major_minor_same:
        return MigrationOutcome.MIGRATION_OPTIONAL, version
    if check_results.validation_is_incomplete:
        return MigrationOutcome.NOT_COMPLETE, version
    return MigrationOutcome.MIGRATION_REQUIRED, version


def check_migration(conv: Conversion) -> Response | None:
    """Run migration checks and return a redirect response if conversion must stop."""
    outcome, conv.template_version = do_migration_checks(conv)
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
            return None
    return None


@convert_bp.route("/migrationPage/<id>", methods=["GET"])
def migrationPage(id: str) -> Response:
    try:
        if (conv := ConversionStore.from_flask().get(id)) is None:
            flash("Conversion session expired", "error")
            return make_response(redirect(url_for("basic.index")))

        version = request.args.get("version", conv.template_version or "unknown")
        excel = conv.excel
        assert excel is not None

        elapsed = request.args.get("elapsed", type=float)
        issues_json = request.args.get("issues", "[]")
        migration_issues: list | dict = []
        if issues_json:
            try:
                parsed_issues = json.loads(issues_json)
                if isinstance(parsed_issues, (list, dict)):
                    migration_issues = parsed_issues
                else:
                    L.warning(
                        "Invalid type for migration issues: %s",
                        type(parsed_issues),
                    )
            except (TypeError, ValueError) as parse_err:
                L.warning("Failed to parse migration issues JSON: %s", parse_err)

        return Response(
            render_template(
                "migration_page.html.jinja",
                conversion_id=id,
                filename=excel.filename,
                version=version,
                newest_version=OUR_VERSION_HOLDER,
                elapsed=elapsed,
                migration_issues=migration_issues,
            )
        )
    except Exception as e:
        L.exception("Exception during migration page display", exc_info=e)
        flash(f"Migration page failed to load: {str(e)}", "error")
        return make_response(redirect(url_for("basic.index")))


@convert_bp.route("/migrationButton/<id>", methods=["POST"])
def migrationButton(id: str) -> Response:
    """Migrate an old VSME template to the current version."""
    try:
        if (conv := ConversionStore.from_flask().get(id)) is None:
            L.warning("MigrationButton: session expired or missing id=%s", id)
            return make_response(jsonify({"error": "Conversion session expired"}), 401)
        if (original_excel := conv.excel) is None:
            L.warning("MigrationButton: no excel in session for id=%s", id)
            return make_response(jsonify({"error": "No file found in session"}), 400)

        migrated_bytes, elapsed, migration_issues = migrate_workbook_as_bytes(
            original_excel.fileLike()
        )
        o_path = PurePath(original_excel.filename)
        m_name = o_path.with_stem(f"{o_path.stem}_migrated_to_latest_version").name
        migrated_excel = FilelikeAndFileName(
            fileContent=migrated_bytes, filename=m_name
        )

        size = len(migrated_excel.fileContent)
        L.info("MigrationButton: generated workbook size=%d bytes for id=%s", size, id)
        if not size:
            L.error("MigrationButton: empty workbook output for id=%s", id)
            return make_response(
                jsonify({"error": "Migration produced empty file"}), 500
            )

        conv.migrated_excel = migrated_excel
        session.modified = True

        return make_response(
            redirect(
                url_for(
                    "basic.migrationPage",
                    id=id,
                    elapsed=elapsed,
                    issues=json.dumps(migration_issues),
                ),
                code=303,
            )
        )
    except Exception as e:
        L.exception("Exception during migration", exc_info=e)
        return make_response(jsonify({"error": str(e)}), 500)


@convert_bp.route("/downloadMigrated/<id>", methods=["GET"])
def downloadMigrated(id: str) -> Response:
    """Download the migrated file from the session."""
    if (conv := ConversionStore.from_flask().get(id)) is None:
        return make_response({"error": "Conversion session expired / not found"}, 404)
    if (migrated_excel := conv.migrated_excel) is None:
        return make_response({"error": "No migrated file found"}, 404)
    return send_file(
        migrated_excel.fileLike(),
        as_attachment=True,
        download_name=migrated_excel.filename,
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
