"""Flask app for the Excel → Inline XBRL converter."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from datetime import datetime, timezone
from random import randint
from secrets import token_hex
from typing import Any, Callable

from dotenv import load_dotenv
from flask import (
    Flask,
    Response,
    current_app,
    flash,
    jsonify,
    make_response,
    redirect,
    render_template,
    request,
    send_file,
    session,
    url_for,
)
from flask_session import Session  # type: ignore

import mireport
from mireport import loadBuiltInTaxonomyJSON
from mireport.conversionresults import ConversionResultsBuilder, MessageType, Severity
from mireport.filesupport import FilelikeAndFileName
from mireport.report.theme import ColourPalette, ReportTheme

from .app_setup import (
    configure_arelle,
    configure_logging,
    configure_session,
    get_arelle,
)
from .blueprints import convert_bp
from .conversion import Conversion, ConversionStore
from .conversion_service import do_conversion
from .forms import IMAGE_UPLOAD_FIELDS, first_str
from .locales import make_locale_json
from .migration import (
    MIGRATION_WORKING,
    MigrationOutcome,
    check_migration,
)
from .template_helpers import format_timedelta, get_upload_filename

ENABLE_CAPTCHA = False
ENABLE_MIGRATION = False
MAX_FILE_SIZE = 16 * 2**20  # 16 MiB
DEPLOYMENT_DATETIME = datetime.now(timezone.utc)
LOCALE_JSON: list[dict[str, str]] = []

L = logging.getLogger(__name__)


def create_app(test_config: Mapping[str, Any] | None = None) -> Flask:
    load_dotenv()
    configure_logging()
    loadBuiltInTaxonomyJSON()

    global LOCALE_JSON
    LOCALE_JSON = make_locale_json()

    app = Flask(__name__, static_folder=None)
    app.config["MAX_CONTENT_LENGTH"] = MAX_FILE_SIZE
    app.config.from_prefixed_env()
    if test_config:
        app.config.update(test_config)

    global ENABLE_CAPTCHA, ENABLE_MIGRATION
    ENABLE_CAPTCHA = bool(app.config.get("ENABLE_CAPTCHA", False))
    ENABLE_MIGRATION = (
        bool(app.config.get("ENABLE_MIGRATION", False)) and MIGRATION_WORKING
    )

    if (fallback := configure_session(app)) is not None:
        return fallback

    app.register_blueprint(convert_bp, url_prefix=app.config.get("PREFIX", "/"))
    configure_arelle(app)

    app.jinja_env.globals.update(
        {
            Severity.__name__: Severity,
            MessageType.__name__: MessageType,
            "deployment_datetime": DEPLOYMENT_DATETIME,
            format_timedelta.__name__: format_timedelta,
            get_upload_filename.__name__: get_upload_filename,
        }
    )

    Session(app)
    return app


@convert_bp.before_request
def generate_csrf_token() -> None:
    if "csrf_token" not in session:
        session["csrf_token"] = token_hex(16)


@convert_bp.after_app_request
def add_deployment_header(response: Response) -> Response:
    response.headers["X-Deployment-Datetime"] = DEPLOYMENT_DATETIME.isoformat(
        timespec="seconds"
    )
    return response


@convert_bp.errorhandler(413)
def request_entity_too_large(error: Any) -> Response:
    return make_response(
        {"error": f"File too large (maximum supported is {MAX_FILE_SIZE:,} bytes)"},
        413,
    )


@convert_bp.route("/")
def index() -> Response:
    return Response(
        render_template(
            "excel-to-xbrl-converter.html.jinja",
            existing_conversions=ConversionStore.from_flask().has_active(),
            ENABLE_CAPTCHA=ENABLE_CAPTCHA,
            colour_palettes=list(ColourPalette),
            default_palette=ReportTheme.DEFAULT_COLOUR,
        )
    )


@convert_bp.route("/generate_captcha", methods=["GET"])
def generate_captcha() -> dict:
    num1, num2 = randint(1, 10), randint(1, 10)
    session.pop("captcha_answer", None)
    session["captcha_answer"] = num1 + num2
    session.modified = True
    return {"question": f"What is {num1} + {num2}?"}


@convert_bp.route(f"/locales/available_{mireport.__version__}.json")
def available_locales() -> Response:
    return jsonify(LOCALE_JSON)


@convert_bp.route("/debug_session")
def debug_session() -> Response:
    session.modified = True
    return jsonify(
        {
            "session_id": request.cookies.get("session"),
            "session_lifetime": str(
                current_app.config.get("PERMANENT_SESSION_LIFETIME")
            ),
            "session_modified": session.modified,
            "session_data": _dump_session(session),
        }
    )


def _dump_session(obj: Any, depth: int = 0) -> Any:
    if depth > 20:
        return "..."
    if isinstance(obj, dict):
        return {str(k): _dump_session(v, depth + 1) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_dump_session(item, depth + 1) for item in obj]
    if isinstance(obj, (str, int, float, bool)) or obj is None:
        return obj
    try:
        return (repr(obj[:10]) + "(truncated)") if len(obj) > 10 else repr(obj)
    except Exception:
        return repr(obj)


@convert_bp.route("/upload", methods=["POST"])
def upload() -> Response:
    if "file" not in request.files:
        return make_response({"error": "No file part"}, 400)

    if ENABLE_CAPTCHA and (failure := _validate_captcha_and_csrf()) is not None:
        return failure

    xlsx_blobs = request.files.getlist("file")
    if len(xlsx_blobs) > 1:
        return make_response({"error": "Too many files"}, 400)
    blob = xlsx_blobs[0]
    if not blob.filename:
        return make_response({"error": "No file specified", "file": None}, 400)
    if "." not in blob.filename or blob.filename.lower().rsplit(".", 1)[-1] != "xlsx":
        return make_response(
            {
                "error": "Invalid file format (only .xlsx files supported)",
                "file": blob.filename,
            },
            400,
        )

    store = ConversionStore.from_flask()
    conv = store.create(ConversionResultsBuilder().conversionId)
    conv.date = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    conv.excel = FilelikeAndFileName(
        fileContent=blob.stream.read(), filename=blob.filename
    )

    if first_str(request.form, "localeOption") == "manual":
        conv.locale_str = first_str(request.form, "locale")
    conv.style_palette = first_str(request.form, "style_colour", "style_palette")
    conv.style_mode = first_str(request.form, "style_mode")

    for form_field, session_key in IMAGE_UPLOAD_FIELDS:
        if form_field not in request.files:
            continue
        files = request.files.getlist(form_field)
        if len(files) > 1:
            return make_response({"error": f"Too many {form_field} files"}, 400)
        img = files[0]
        if img.filename:
            conv.set_image(
                session_key,
                FilelikeAndFileName(
                    fileContent=img.stream.read(), filename=img.filename
                ),
            )

    return make_response(redirect(url_for("basic.convert", id=conv.id), code=303))


def _validate_captcha_and_csrf() -> Response | None:
    captcha_input = request.form.get("captcha", type=int)
    captcha_answer = session.pop("captcha_answer", None)
    if not captcha_answer or captcha_input != captcha_answer:
        flash(
            "Invalid captcha. Please confirm you are human by calculating the "
            "correct result and try again.",
            "error",
        )
        return make_response(redirect(url_for("basic.index")))
    csrf_token = request.form.get("csrf_token")
    if not csrf_token or csrf_token != session.get("csrf_token"):
        flash("Invalid CSRF token. Please try again.", "error")
        return make_response(redirect(url_for("basic.index")))
    return None


@convert_bp.route("/conversions/<string:id>", methods=["GET"])
def convert(id: str) -> Response:
    try:
        store = ConversionStore.from_flask()
        if (conv := store.get(id)) is None:
            return make_response(
                render_template(
                    "conversion-results.html.jinja",
                    expired=True,
                    conversion_result=None,
                ),
                404,
            )

        if not conv.has_results:
            if ENABLE_MIGRATION and (mig := check_migration(conv)) is not None:
                return mig
            results = do_conversion(conv, get_arelle())
            if conv.has_partial_fact_concepts and not conv.has_external_values:
                return make_response(
                    redirect(url_for("basic.partial_facts", id=id), code=303)
                )
            conv.results = results

        offer_migration = ENABLE_MIGRATION and conv.migration_outcome == str(
            MigrationOutcome.MIGRATION_OPTIONAL
        )
        return Response(
            render_template(
                "conversion-results.html.jinja",
                conversion_result=conv.results,
                offer_migration=offer_migration,
                conversion_id=id,
                dev=request.args.get("show_developer_messages") == "true",
                conversion_date=conv.date,
                upload_filename=get_upload_filename(id),
            )
        )
    except Exception as e:
        if current_app.debug:
            raise
        L.exception("Exception during conversion", exc_info=e)
        return make_response({"error": str(e)}, 500)


@convert_bp.route("/conversions/<string:id>/partial-facts", methods=["GET"])
def partial_facts(id: str) -> Response:
    if (conv := ConversionStore.from_flask().get(id)) is None:
        return make_response(redirect(url_for("basic.index")))
    if not (concepts := conv.partial_fact_concepts):
        return make_response(redirect(url_for("basic.convert", id=id), code=303))
    return Response(
        render_template(
            "partial-facts.html.jinja",
            conversion_id=id,
            concepts=concepts,
            upload_filename=get_upload_filename(id),
        )
    )


@convert_bp.route("/conversions/<string:id>/partial-facts", methods=["POST"])
def partial_facts_submit(id: str) -> Response:
    if (conv := ConversionStore.from_flask().get(id)) is None:
        return make_response(redirect(url_for("basic.index")))

    external: dict[str, FilelikeAndFileName] = {}
    for concept_info in conv.partial_fact_concepts:
        qname = concept_info["qname"]
        files = request.files.getlist(f"docx_{qname}")
        if not files or not files[0].filename:
            flash(
                f"Please upload a Word document for: {concept_info['label']}",
                "error",
            )
            return make_response(
                redirect(url_for("basic.partial_facts", id=id), code=303)
            )
        docx_file = files[0]
        external[qname] = FilelikeAndFileName(
            fileContent=docx_file.stream.read(), filename=docx_file.filename
        )
    conv.external_values = external
    return make_response(redirect(url_for("basic.convert", id=id), code=303))


_DOWNLOAD_GENERATORS: dict[
    str, Callable[[Any, FilelikeAndFileName], FilelikeAndFileName]
] = {
    "json": lambda arelle, pkg: arelle.generateXBRLJson(pkg).xBRL_JSON,
    "viewer": lambda arelle, pkg: arelle.generateInlineViewer(pkg).viewer,
}


@convert_bp.route("/downloadFile/<string:id>/<string:ftype>/", methods=["GET", "HEAD"])
def downloadFile(id: str, ftype: str) -> Response:
    if ftype not in Conversion.OUTPUT_KEYS:
        return make_response({"error": f"File type {ftype} not found."}, 404)
    if (conv := ConversionStore.from_flask().get(id)) is None:
        return make_response({"error": "No file found"}, 404)
    if not conv.has_zip:
        return make_response(
            {"error": "No report generated. Nothing to download."}, 404
        )

    if conv.cached_output(ftype) is None:
        if (generator := _DOWNLOAD_GENERATORS.get(ftype)) is None:
            return make_response({"error": "No file found"}, 404)
        assert (report_package := conv.zip) is not None
        conv.cache_output(ftype, generator(get_arelle(), report_package))

    if request.method == "HEAD":
        return Response(status=200, headers={"X-File-Ready": "true"})

    artefact = conv.cached_output(ftype)
    assert artefact is not None
    return send_file(
        artefact.fileLike(),
        as_attachment=True,
        download_name=artefact.filename,
        mimetype="text/html",
    )


@convert_bp.route("/viewer/<string:id>/", methods=["GET", "HEAD"])
def viewer(id: str) -> Response:
    conv = ConversionStore.from_flask().get(id)
    assert conv is not None, "viewer route requires an existing conversion"
    if (existing := conv.cached_output("viewer")) is not None:
        artefact = existing
    else:
        assert (report_package := conv.zip) is not None
        artefact = get_arelle().generateInlineViewer(report_package).viewer
        conv.cache_output("viewer", artefact)
        if request.method == "HEAD":
            return Response(status=200, headers={"X-File-Ready": "true"})
    return send_file(
        artefact.fileLike(),
        as_attachment=False,
        download_name=artefact.filename,
        mimetype="text/html",
    )


@convert_bp.route("/conversions/")
def conversions() -> Response:
    return Response(
        render_template(
            "conversions.html.jinja",
            conversions=ConversionStore.from_flask().active(),
            lifetime=current_app.config["PERMANENT_SESSION_LIFETIME"],
        )
    )


@convert_bp.route("/delete/<string:id>", methods=["POST"])
def delete(id: str) -> Response:
    ConversionStore.from_flask().delete(id)
    return make_response(redirect(url_for("basic.conversions"), code=303))


@convert_bp.route("/delete/_all", methods=["POST"])
def delete_all() -> Response:
    ConversionStore.from_flask().delete_active()
    return make_response(redirect(url_for("basic.conversions"), code=303))


if __name__ == "__main__":
    create_app().run(debug=True)
