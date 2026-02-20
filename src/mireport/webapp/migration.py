import logging
from enum import StrEnum
from pathlib import PurePath

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
from migration_tool import migrate_workbook_as_bytes

from mireport.excelprocessor import OUR_VERSION_HOLDER, ExcelProcessor
from mireport.filesupport import FilelikeAndFileName

from .blueprints import convert_bp

L = logging.getLogger(__name__)


class MigrationOutcome(StrEnum):
    SUCCESS = "success"
    MISSING = "report_missing"
    INVALID = "report_invalid"
    MIGRATION_OPTIONAL = "migration_optional"
    MIGRATION_REQUIRED = "migration_required"


def doMigrationChecks(conversion: dict) -> tuple[MigrationOutcome, str]:
    upload = FilelikeAndFileName(*conversion["excel"])
    check_results = ExcelProcessor.checkReport(upload.fileLike())
    version = str(check_results.reported_version) if check_results else "unknown"

    if check_results is None:
        return (
            MigrationOutcome.MISSING,
            version,
        )  # can't do anything if we can't read the report
    elif check_results.version_is_same:
        return MigrationOutcome.SUCCESS, version  # up-to-date version
    elif check_results.validation_is_incomplete:
        return MigrationOutcome.INVALID, version  # invalid report, can't proceed
    elif check_results.version_major_minor_same:
        return (
            MigrationOutcome.MIGRATION_OPTIONAL,
            version,
        )  # optional migration offered
    else:
        return (
            MigrationOutcome.MIGRATION_REQUIRED,
            version,
        )  # older (major) version, must migrate


def checkMigration(conversion: dict) -> Response | None:
    outcome, conversion["template_version"] = doMigrationChecks(conversion)
    response = None
    match outcome:
        case MigrationOutcome.MIGRATION_REQUIRED:
            response = make_response(
                redirect(
                    url_for(
                        "basic.migrationPage",
                        id=conversion["id"],
                    ),
                    code=303,
                )
            )
        case MigrationOutcome.INVALID:
            flash("Report validation is not complete", "error")
            response = make_response(redirect(url_for("basic.index")))
        case MigrationOutcome.MISSING:
            flash("Report missing for migration", "error")
            response = make_response(redirect(url_for("basic.index")))
        case MigrationOutcome.MIGRATION_OPTIONAL:
            pass  # Continue with conversion
        case MigrationOutcome.SUCCESS:
            pass  # Continue with conversion
    conversion["migration_outcome"] = str(outcome)
    return response


@convert_bp.route("/migrationPage/<id>", methods=["GET"])
def migrationPage(id: str) -> Response:
    try:
        if id not in session:
            flash("Conversion session expired", "error")
            return make_response(redirect(url_for("basic.index")))

        conversion = session[id]
        version = request.args.get(
            "version", conversion.get("template_version", "unknown")
        )
        excel = FilelikeAndFileName(*conversion["excel"])

        # Parse migration results from query parameters
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
    """Handle migration of old VSME templates to new version."""
    try:
        # Get the file from session
        if id not in session:
            L.warning("MigrationButton: session expired or missing id=%s", id)
            return make_response(jsonify({"error": "Conversion session expired"}), 401)

        conversion = session[id]

        if "excel" not in conversion:
            L.warning("MigrationButton: no excel in session for id=%s", id)
            return make_response(jsonify({"error": "No file found in session"}), 400)

        original_excel = FilelikeAndFileName(*conversion["excel"])
        migrated_bytes, elapsed, migration_issues = migrate_workbook_as_bytes(
            original_excel.fileLike()
        )
        o_path = PurePath(original_excel.filename)
        m_name = o_path.with_stem(f"{o_path.stem}_migrated_to_latest_version").name
        migrated_excel = FilelikeAndFileName(
            fileContent=migrated_bytes, filename=m_name
        )

        # Guard against empty output
        size = len(migrated_excel.fileContent)
        L.info("MigrationButton: generated workbook size=%d bytes for id=%s", size, id)
        if not size:
            L.error("MigrationButton: empty workbook output for id=%s", id)
            return make_response(
                jsonify({"error": "Migration produced empty file"}), 500
            )

        # Store migrated file temporarily in session and redirect with results
        conversion["migrated_excel"] = migrated_excel
        session.modified = True

        # Redirect to migration page with results as query parameters
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
    if id not in session:
        return make_response({"error": "Conversion session expired / not found"}, 404)

    conversion = session[id]

    if "migrated_excel" not in conversion:
        return make_response({"error": "No migrated file found"}, 404)

    migrated_excel = FilelikeAndFileName(*conversion.get("migrated_excel"))
    return send_file(
        migrated_excel.fileLike(),
        as_attachment=True,
        download_name=migrated_excel.filename,
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
