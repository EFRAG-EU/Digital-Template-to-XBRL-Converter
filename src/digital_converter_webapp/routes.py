"""The converter's routes on the single ``basic`` blueprint.

Handlers stay thin: conversion state goes through :class:`ConversionStore`, the
pipeline is in :mod:`.conversion_service`, and everything else is in the
sibling modules. :func:`register_routes` puts the blueprint on an app; the
migration routes come with it, as this module imports :mod:`.migration`.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from pathlib import PurePath
from secrets import token_hex
from time import monotonic, sleep
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
from mireport.comparatives import PRIOR_REPORT_EXTENSIONS
from mireport.conversionresults import ConversionResultsBuilder, Step
from mireport.filesupport import FilelikeAndFileName
from mireport.report.theme import ColourPalette, ReportTheme
from mireport.stringutil import truthy

from .app_setup import DEPLOYMENT_DATETIME, getArelle
from .background import StepStore, reportResults, viewerFiles
from .blueprints import convert_bp
from .captcha import generate_captcha, validateCaptchaAndCsrf
from .conversion import Conversion, ConversionStore
from .conversion_service import doConversion
from .forms import IMAGE_UPLOAD_FIELDS, MAX_FILE_SIZE, MAX_PDF_COUNT, first_str
from .migration import MigrationOutcome, checkMigration
from .template_helpers import getUploadFilename
from .viewer import MEMBER_CSP, serveViewerFile
from .workbooks import Workbooks

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
    # Last year's report is optional; an untouched file input submits an empty name.
    prior = request.files.get("prior")
    priorName = prior.filename if prior is not None else None
    if priorName and PurePath(priorName).suffix.lower() not in PRIOR_REPORT_EXTENSIONS:
        return make_response(
            {
                "error": "Invalid file format for last year's report (only "
                f"{', '.join(PRIOR_REPORT_EXTENSIONS)} files supported)",
                "file": priorName,
            },
            400,
        )
    conv = ConversionStore.fromFlask().create(ConversionResultsBuilder().conversionId)
    conv.date = datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S")
    conv.excel = FilelikeAndFileName(
        fileContent=blob.stream.read(), filename=blob.filename
    )
    if prior is not None and priorName:
        conv.priorReport = FilelikeAndFileName(
            fileContent=prior.stream.read(), filename=priorName
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
        if (conv := ConversionStore.fromFlask().get(id)) is None:
            return make_response(
                render_template(
                    "conversion-results.html.jinja",
                    expired=True,
                    results=None,
                ),
                404,
            )

        if not conv.has_results:
            # Each workbook is parsed once, to be checked and then converted.
            workbooks = Workbooks()
            # This year's workbook, then last year's: each may be kept as it is.
            for workbook, skip in (
                (conv, "skip_migration"),
                (conv.prior, "skip_prior_migration"),
            ):
                if (
                    not truthy(request.args.get(skip, ""))
                    and current_app.config["ENABLE_MIGRATION"]
                    and (migrationResponse := checkMigration(workbook, workbooks))
                    is not None
                ):
                    # Migration deemed to be required so no conversion done at this stage.
                    return migrationResponse

            results = doConversion(
                conv,
                getArelle(),
                current_app.config["PDF_SUPPLEMENTARY_MODE"],
                excelParsingWarningsAsDevInfo=current_app.config[
                    "EXCEL_PARSING_WARNINGS_AS_DEVINFO"
                ],
                workbooks=workbooks,
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
                results=reportResults(conv),
                offer_migration=offer_migration,
                conversion_id=id,
                dev=devInfo,
                conversion_date=conv.date,
                upload_filename=getUploadFilename(id),
                prior_filename=prior.filename if (prior := conv.priorReport) else "",
            )
        )
    except Exception as e:
        if current_app.debug:
            raise
        else:
            L.exception("Exception during conversion", exc_info=e)
            return make_response({"error": str(e)}, 500)


VALIDATION_ERRORS_MESSAGE = (
    "Validation found errors in the XBRL report, so it can't be downloaded."
    " Please review the messages on the results page."
)


@convert_bp.route("/conversions/<string:id>/status", methods=["GET"])
def conversionStatus(id: str) -> Response:
    """The results page's validation banner, downloads and messages as they
    now stand, for the page to swap in while the steps run."""
    if (conv := ConversionStore.fromFlask().get(id)) is None or (
        results := reportResults(conv)
    ) is None:
        return make_response({"error": "No conversion found"}, 404)
    dev = request.args.get("show_developer_messages") == "true"
    return make_response(
        {
            "complete": results.isComplete,
            "downloadsAllowed": results.downloadsAllowed,
            "error": VALIDATION_ERRORS_MESSAGE if results.hasValidationErrors else None,
            "html": {
                "validation-status": render_template(
                    "_validation-status.html.jinja", results=results
                ),
                "downloads": render_template("_downloads.html.jinja", results=results),
                "messages": render_template(
                    "_messages.html.jinja", results=results, dev=dev
                ),
            },
        }
    )


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


def _heldBack(conv: Conversion, *, viewer: bool = False) -> Response | None:
    """Why the report can't be had yet, if it can't: 409 once validation has
    found errors, 202 (try again) while it is being validated, or its
    ``viewer`` built. A HEAD, the page's polling, is answered at once; a GET,
    someone following the link, waits a while for validation first."""
    # Inside the 60s that gunicorn allows a request in production.
    waitSeconds = current_app.config.get("DOWNLOAD_WAIT_SECONDS", 50)
    deadline = monotonic() + (waitSeconds if request.method == "GET" else 0)
    while (results := reportResults(conv)) is not None:
        if not results.downloadsAllowed:
            return make_response({"error": VALIDATION_ERRORS_MESSAGE}, 409)
        if not (
            results.validationPending
            or (viewer and not results.steps[Step.VIEWER].isFinished)
        ):
            return None
        if monotonic() >= deadline:
            return make_response(
                {
                    "status": "The XBRL report is still being validated. Try again shortly."
                },
                202,
                {"X-File-Ready": "false"},
            )
        sleep(0.5)
    return None


@convert_bp.route("/downloadFile/<string:id>/<string:ftype>/", methods=["GET", "HEAD"])
def downloadFile(id: str, ftype: str) -> Response:
    """Download the converted file from the session."""
    if (conv := ConversionStore.fromFlask().get(id)) is None:
        return make_response({"error": "No file found"}, 404)

    if not conv.has_zip:
        return make_response(
            {"error": "No report generated. Nothing to download."}, 404
        )

    if ftype in ("viewer", "zip", "json") and (
        held := _heldBack(conv, viewer=ftype == "viewer")
    ):
        return held

    match ftype:
        case "viewer":
            stuff, members = viewerFiles(conv)
            if members and request.method != "HEAD":
                # A document set viewer fetches its members by name relative to
                # itself, so it has to be served in place rather than downloaded.
                return make_response(redirect(url_for("basic.viewer", id=id), code=303))
        case "zip" | "excel" | "json":
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
    conversions = ConversionStore.fromFlask()
    if conversions.get(id) is not None:
        StepStore.fromFlask().forget(id)
    conversions.delete(id)
    return make_response(redirect(url_for("basic.conversions"), code=303))


@convert_bp.route("/delete/_all", methods=["POST"])
def delete_all() -> Response:
    conversions = ConversionStore.fromFlask()
    for id in conversions.active():
        StepStore.fromFlask().forget(id)
    conversions.deleteActive()
    return make_response(redirect(url_for("basic.conversions"), code=303))


@convert_bp.route("/viewer/<string:id>/", methods=["GET", "HEAD"])
def viewer(id: str) -> Response:
    if (conv := ConversionStore.fromFlask().get(id)) is None or not conv.has_zip:
        return make_response({"error": "No file found"}, 404)
    if held := _heldBack(conv, viewer=True):
        return held

    stuff, _ = viewerFiles(conv)
    return serveViewerFile(stuff)


@convert_bp.route("/viewer/<string:id>/<string:filename>", methods=["GET", "HEAD"])
def viewerDocumentSetMember(id: str, filename: str) -> Response:
    """Serve a document set member where the viewer expects to find it.

    The viewer data lists its fellow document set members by filename and
    fetches them relative to itself, i.e. from this route.
    """
    if (conv := ConversionStore.fromFlask().get(id)) is None or not conv.has_zip:
        return make_response({"error": "No file found"}, 404)
    if held := _heldBack(conv, viewer=True):
        return held

    _, members = viewerFiles(conv)
    if (member := members.get(filename)) is None:
        return make_response({"error": f"File {filename} not found."}, 404)
    response = serveViewerFile(member)
    response.headers["Content-Security-Policy"] = MEMBER_CSP
    response.headers["X-Content-Type-Options"] = "nosniff"
    return response
