"""The converter's routes on the single ``basic`` blueprint.

Handlers stay thin: conversion state goes through :class:`ConversionStore`, the
pipeline is in :mod:`.conversion_service`, and everything else is in the
sibling modules. :func:`register_routes` puts the blueprint on an app; the
migration routes come with it, as this module imports :mod:`.migration`.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from secrets import token_hex
from typing import TYPE_CHECKING, Any

from flask import (
    Response,
    abort,
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

import mireport
from mireport.conversionresults import ConversionResultsBuilder
from mireport.filesupport import FilelikeAndFileName
from mireport.report.theme import ColourPalette, ReportTheme
from mireport.stringutil import truthy

from .app_setup import DEPLOYMENT_DATETIME, getArelle
from .blueprints import convert_bp
from .captcha import generate_captcha, validateCaptchaAndCsrf
from .conversion import ConversionStore
from .conversion_service import doConversion, ensureViewer, ensureXbrlJson
from .forms import IMAGE_UPLOAD_FIELDS, MAX_FILE_SIZE, MAX_PDF_COUNT, first_str
from .migration import MigrationOutcome, checkMigration
from .template_helpers import getUploadFilename
from .viewer import MEMBER_CSP, serveViewerFile

if TYPE_CHECKING:
    from flask import Flask

L = logging.getLogger(__name__)


def register_routes(app: Flask) -> None:
    """Install the blueprint (all of the web app's routes) on ``app``."""
    app.register_blueprint(convert_bp, url_prefix=app.config.get("PREFIX", "/"))


@convert_bp.route("/")
def index() -> Response:
    enable_captcha = current_app.config["ENABLE_CAPTCHA"]
    captcha_id, captcha_question = (
        generate_captcha() if enable_captcha else (None, None)
    )
    return Response(
        render_template(
            "excel-to-xbrl-converter.html.jinja",
            existing_conversions=ConversionStore.fromFlask().hasActive(),
            ENABLE_CAPTCHA=enable_captcha,
            captcha_id=captcha_id,
            captcha_question=captcha_question,
            colour_palettes=list(ColourPalette),
            default_palette=ReportTheme.DEFAULT_COLOUR,
            enable_pdfs=current_app.config["ENABLE_SUPPLEMENTARY_PDFS"],
            max_pdf_count=MAX_PDF_COUNT,
        )
    )


@convert_bp.errorhandler(413)
def request_entity_too_large(error: type[Exception] | int) -> Response:
    return make_response(
        {
            "error": f"File too large (maximum supported is {MAX_FILE_SIZE:,} bytes)",
        },
        413,
    )


@convert_bp.before_request
def generate_csrf_token() -> None:
    """Generate a CSRF token and store it in the session."""
    if "csrf_token" not in session:
        session["csrf_token"] = token_hex(16)


@convert_bp.after_app_request
def add_deployment_header(response: Response) -> Response:
    response.headers["X-Deployment-Datetime"] = DEPLOYMENT_DATETIME.isoformat(
        timespec="seconds"
    )
    return response


@convert_bp.route(f"/locales/available_{mireport.__version__}.json")
def available_locales() -> Response:
    return jsonify(current_app.config["LOCALE_JSON"])


@convert_bp.route("/debug_session")
def debug_session() -> Response:
    if not current_app.debug:
        abort(404)
    session.modified = True  # Ensure session is saved
    interesting: dict[str, Any] = {
        "session_id": request.cookies.get("session"),
        "session_lifetime": str(current_app.config.get("PERMANENT_SESSION_LIFETIME")),
        "session_modified": session.modified,
    }

    def dumpSessionRecursive(obj: Any, depth: int = 0) -> Any:
        if depth > 20:
            return "..."
        if isinstance(obj, dict):
            result = {}
            for k, v in obj.items():
                # Ensure key is a string for JSON
                key = str(k) if not isinstance(k, str) else k
                result[key] = dumpSessionRecursive(v, depth + 1)
            return result
        elif isinstance(obj, list):
            return [dumpSessionRecursive(item, depth + 1) for item in obj]
        elif isinstance(obj, (str, int, float, bool)) or obj is None:
            return obj
        else:
            # Fallback for non-JSON-serializable types
            return repr(obj[:10]) + "(truncated)" if len(obj) > 10 else repr(obj)

    interesting["session_data"] = dumpSessionRecursive(session)
    return jsonify(interesting)


@convert_bp.route("/upload", methods=["POST"])
def upload() -> Response:
    if "file" not in request.files:
        return make_response({"error": "No file part"}, 400)

    if (
        current_app.config["ENABLE_CAPTCHA"]
        and (rejection := validateCaptchaAndCsrf()) is not None
    ):
        return rejection

    xlsx_blobs = request.files.getlist("file")
    if len(xlsx_blobs) > 1:
        return make_response({"error": "Too many files"}, 400)
    blob = xlsx_blobs[0]
    if blob.filename == "":
        return make_response(
            {
                "error": "No file specified",
                "file": None,
            },
            400,
        )
    elif "." not in blob.filename or "xlsx" != blob.filename.lower().split(".")[-1]:
        return make_response(
            {
                "error": "Invalid file format (only .xlsx files supported)",
                "file": blob.filename,
            },
            400,
        )
    conv = ConversionStore.fromFlask().create(ConversionResultsBuilder().conversionId)
    conv.date = datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S")
    conv.excel = FilelikeAndFileName(
        fileContent=blob.stream.read(), filename=blob.filename
    )

    if first_str(request.form, "localeOption") == "manual":
        conv.locale_str = first_str(request.form, "locale")

    conv.style_palette = first_str(request.form, "style_colour", "style_palette")
    conv.style_mode = first_str(request.form, "style_mode")

    for field_name, conv_key in IMAGE_UPLOAD_FIELDS:
        if field_name not in request.files:
            continue
        img_files = request.files.getlist(field_name)
        if len(img_files) > 1:
            return make_response({"error": f"Too many {field_name} files"}, 400)
        img_file = img_files[0]
        if img_file.filename:
            conv.setImage(
                conv_key,
                FilelikeAndFileName(
                    fileContent=img_file.stream.read(), filename=img_file.filename
                ),
            )

    # Supplementary PDFs. An untouched file input still submits an empty part,
    # so blank filenames are dropped rather than rejected.
    pdf_files = [f for f in request.files.getlist("pdfs") if f.filename]
    if pdf_files and not current_app.config["ENABLE_SUPPLEMENTARY_PDFS"]:
        return make_response({"error": "Supplementary PDFs are not accepted"}, 400)
    if len(pdf_files) > MAX_PDF_COUNT:
        return make_response(
            {"error": f"Too many PDF files (maximum is {MAX_PDF_COUNT})"}, 400
        )
    for pdf_file in pdf_files:
        assert pdf_file.filename is not None
        if "pdf" != pdf_file.filename.lower().rpartition(".")[2]:
            return make_response(
                {
                    "error": "Invalid file format (only .pdf files supported)",
                    "file": pdf_file.filename,
                },
                400,
            )
    if pdf_files:
        conv.pdfs = [
            FilelikeAndFileName(
                fileContent=f.stream.read(), filename=f.filename or "annex.pdf"
            )
            for f in pdf_files
        ]

    return make_response(redirect(url_for("basic.convert", id=conv.id), code=303))


@convert_bp.route("/conversions/<string:id>", methods=["GET"])
def convert(id: str) -> Response:
    try:
        skip_migration = truthy(request.args.get("skip_migration", ""))

        if (conv := ConversionStore.fromFlask().get(id)) is None:
            return make_response(
                render_template(
                    "conversion-results.html.jinja",
                    expired=True,
                    conversion_result=None,
                ),
                404,
            )

        if not conv.has_results:
            if (
                not skip_migration
                and current_app.config["ENABLE_MIGRATION"]
                and (migrationResponse := checkMigration(conv)) is not None
            ):
                # Migration deemed to be required so no conversion done at this stage.
                return migrationResponse

            results = doConversion(
                conv, getArelle(), current_app.config["PDF_SUPPLEMENTARY_MODE"]
            )
            if conv.has_partial_fact_concepts and not conv.has_external_values:
                return make_response(
                    redirect(url_for("basic.partial_facts", id=id), code=303)
                )
            conv.results = results

        devInfo = request.args.get("show_developer_messages") == "true"

        offer_migration: bool = current_app.config["ENABLE_MIGRATION"] and (
            conv.migration_outcome == str(MigrationOutcome.MIGRATION_OPTIONAL)
        )

        return Response(
            render_template(
                "conversion-results.html.jinja",
                conversion_result=conv.results,
                offer_migration=offer_migration,
                conversion_id=id,
                dev=devInfo,
                conversion_date=conv.date,
                upload_filename=getUploadFilename(id),
            )
        )
    except Exception as e:
        if current_app.debug:
            raise
        else:
            L.exception("Exception during conversion", exc_info=e)
            return make_response({"error": str(e)}, 500)


@convert_bp.route("/conversions/<string:id>/partial-facts", methods=["GET"])
def partial_facts(id: str) -> Response:
    if (conv := ConversionStore.fromFlask().get(id)) is None:
        return make_response(redirect(url_for("basic.index")))
    concepts = conv.partial_fact_concepts
    if not concepts:
        return make_response(redirect(url_for("basic.convert", id=id), code=303))
    return Response(
        render_template(
            "partial-facts.html.jinja",
            conversion_id=id,
            concepts=concepts,
            upload_filename=getUploadFilename(id),
        )
    )


@convert_bp.route("/conversions/<string:id>/partial-facts", methods=["POST"])
def partial_facts_submit(id: str) -> Response:
    if (conv := ConversionStore.fromFlask().get(id)) is None:
        return make_response(redirect(url_for("basic.index")))
    external_values: dict[str, FilelikeAndFileName] = {}
    for concept_info in conv.partial_fact_concepts:
        qname = concept_info["qname"]
        field_name = f"docx_{qname}"
        files = request.files.getlist(field_name)
        if not files or not files[0].filename:
            flash(
                f"Please upload a Word document for: {concept_info['label']}",
                category="error",
            )
            return make_response(
                redirect(url_for("basic.partial_facts", id=id), code=303)
            )
        docx_file = files[0]
        external_values[qname] = FilelikeAndFileName(
            fileContent=docx_file.stream.read(), filename=docx_file.filename
        )
    conv.external_values = external_values
    return make_response(redirect(url_for("basic.convert", id=id), code=303))


@convert_bp.route("/downloadFile/<string:id>/<string:ftype>/", methods=["GET", "HEAD"])
def downloadFile(id: str, ftype: str) -> Response:
    """Download the converted file from the session."""
    if (conv := ConversionStore.fromFlask().get(id)) is None:
        return make_response({"error": "No file found"}, 404)

    if not conv.has_zip:
        return make_response(
            {"error": "No report generated. Nothing to download."}, 404
        )

    match ftype:
        case "viewer":
            stuff, members = ensureViewer(conv, getArelle())
            if members and request.method != "HEAD":
                # A document set viewer fetches its members by name relative to
                # itself, so it has to be served in place rather than downloaded.
                return make_response(redirect(url_for("basic.viewer", id=id), code=303))
        case "json":
            stuff = ensureXbrlJson(conv, getArelle())
        case "zip" | "excel":
            if (cached := conv.cachedOutput(ftype)) is None:
                return make_response({"error": "No file found"}, 404)
            stuff = cached
        case _:
            return make_response({"error": f"File type {ftype} not found."}, 404)

    if request.method == "HEAD":
        return Response(status=200, headers={"X-File-Ready": "true"})

    return send_file(
        stuff.fileLike(),
        as_attachment=True,
        download_name=stuff.filename,
        mimetype="text/html",
    )


@convert_bp.route("/conversions/")
def conversions() -> Response:
    return Response(
        render_template(
            "conversions.html.jinja",
            conversions=ConversionStore.fromFlask().active(),
            lifetime=current_app.config["PERMANENT_SESSION_LIFETIME"],
        )
    )


@convert_bp.route("/delete/<string:id>", methods=["POST"])
def delete(id: str) -> Response:
    ConversionStore.fromFlask().delete(id)
    return make_response(redirect(url_for("basic.conversions"), code=303))


@convert_bp.route("/delete/_all", methods=["POST"])
def delete_all() -> Response:
    ConversionStore.fromFlask().deleteActive()
    return make_response(redirect(url_for("basic.conversions"), code=303))


@convert_bp.route("/viewer/<string:id>/", methods=["GET", "HEAD"])
def viewer(id: str) -> Response:
    if (conv := ConversionStore.fromFlask().get(id)) is None or not conv.has_zip:
        return make_response({"error": "No file found"}, 404)

    stuff, _ = ensureViewer(conv, getArelle())
    return serveViewerFile(stuff)


@convert_bp.route("/viewer/<string:id>/<string:filename>", methods=["GET", "HEAD"])
def viewerDocumentSetMember(id: str, filename: str) -> Response:
    """Serve a document set member where the viewer expects to find it.

    The viewer data lists its fellow document set members by filename and
    fetches them relative to itself, i.e. from this route.
    """
    if (conv := ConversionStore.fromFlask().get(id)) is None or not conv.has_zip:
        return make_response({"error": "No file found"}, 404)

    _, members = ensureViewer(conv, getArelle())
    if (member := members.get(filename)) is None:
        return make_response({"error": f"File {filename} not found."}, 404)
    response = serveViewerFile(member)
    response.headers["Content-Security-Policy"] = MEMBER_CSP
    response.headers["X-Content-Type-Options"] = "nosniff"
    return response
