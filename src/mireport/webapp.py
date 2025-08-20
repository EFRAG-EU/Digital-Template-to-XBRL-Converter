import logging
from datetime import datetime, timedelta, timezone
from random import randint
from secrets import token_hex
from typing import Any

from dotenv import load_dotenv
from flask import (
    Blueprint,
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

from mireport import loadMetaData
from mireport.arelle.report_info import (
    ARELLE_VERSION_INFORMATION,
    ArelleReportProcessor,
)
from mireport.conversionresults import (
    ConversionResults,
    ConversionResultsBuilder,
    MessageType,
    Severity,
)
from mireport.excelprocessor import (
    VSME_DEFAULTS,
    ExcelProcessor,
)
from mireport.filesupport import FilelikeAndFileName
from mireport.localise import EU_LOCALES, get_locale_list

ENABLE_CAPTCHA = False
MAX_FILE_SIZE = 16 * 2**20  # 16 MiB
DEPLOYMENT_DATETIME = datetime.now(timezone.utc)
DEPLOYMENT_DATETIME_FOR_URL = DEPLOYMENT_DATETIME.strftime("%Y%m%dT%H%M%SZ")
L = logging.getLogger(__name__)
LOCALES_CACHE = get_locale_list(EU_LOCALES)

convert_bp = Blueprint(
    "basic", __name__, template_folder="templates", static_folder="static"
)


def create_app() -> Flask:
    # Regardless of how we are invoked, make sure to load configuration from any
    # ".env" file
    load_dotenv()

    # Get logging working
    logging.basicConfig(
        format="%(asctime)s %(levelname)-8s %(message)s",
        datefmt="[%Y-%m-%d %H:%M:%S]",
        level=logging.INFO,
    )
    logging.captureWarnings(True)

    # Get taxonomy related objects loaded
    loadMetaData()

    app = Flask(__name__, static_folder=None)
    app.config["MAX_CONTENT_LENGTH"] = MAX_FILE_SIZE
    app.config.from_prefixed_env()

    global ENABLE_CAPTCHA
    ENABLE_CAPTCHA = bool(app.config.get("ENABLE_CAPTCHA", False))

    if (
        "development" == app.config.get("DEPLOYMENT", "development")
        and "SESSION_TYPE" not in app.config
    ):
        # DEVELOPER MODE. Insecure. DO NOT USE IN PRODUCTION.
        app.config.from_mapping(
            SECRET_KEY="dev",
            SESSION_TYPE="filesystem",
            SESSION_FILE_DIR="flask_session",
            SESSION_PERMANENT="False",
            PERMANENT_SESSION_LIFETIME=timedelta(hours=1),
        )
        L.critical("Deployed in DEVELOPER mode. Insecure.")
    elif app.config["SESSION_TYPE"] == "redis" and "SESSION_REDIS" not in app.config:
        try:
            try:
                from flask_rq import RQ
                from redis import ConnectionError, Redis  # type: ignore
            except ImportError:
                L.critical(
                    "Redis and/or RQ support isn't available. App startup aborted. You need to fix your configuration.".upper()
                )
                return brokenApp()

            redisUrl = app.config.get("REDIS_URL", "redis://127.0.0.1:6379")
            try:
                rs = Redis.from_url(redisUrl)
                rs.ping()
            except ConnectionError:
                L.critical(
                    "Redis isn't running. App startup aborted. You need to fix your configuration.".upper()
                )
                return brokenApp()

            # Should have a working Redis connection and RQ instance at this point.
            app.config["SESSION_REDIS"] = rs
            app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(hours=1)
            rq = RQ()
            rq.init_app(app)

        except Exception as e:
            L.critical(
                "An unknown exception occurred while configuring support for redis and RQ. App startup aborted. You need to fix your configuration.".upper(),
                exc_info=e,
            )
            return brokenApp()
    else:
        L.critical(f"Can't work with current configuration. {app.config=}")
        return brokenApp()

    # app looks to be working, install routes
    app.register_blueprint(convert_bp, url_prefix=app.config.get("PREFIX", "/"))

    # Discover all the taxonomy packages up front
    taxonomyPackageList = ArelleReportProcessor.getTaxonomyPackagesFromDir(
        app.config.get("TAXONOMY_PACKAGE_DIR")
    )

    app.config["TAXONOMY_PACKAGES"] = taxonomyPackageList

    # If config specified work online/offline, respect it otherwise, if not
    # specified, work offline iff we have been given some taxonomy packages
    offline = app.config["ARELLE_WORK_OFFLINE"] = app.config.get(
        "ARELLE_WORK_OFFLINE", bool(taxonomyPackageList)
    )
    if offline:
        L.info(
            f"Configured to use Arelle offline with {len(taxonomyPackageList)} taxonomy packages: [{', '.join(repr(a) for a in sorted(taxonomyPackageList))}]"
        )

    # Install enumeration classes for use in templates
    app.jinja_env.globals.update(
        {
            Severity.__name__: Severity,
            MessageType.__name__: MessageType,
            "deployment_datetime": DEPLOYMENT_DATETIME,
            format_timedelta.__name__: format_timedelta,
            getUploadFilename.__name__: getUploadFilename,
        }
    )

    # Use server-side sessions
    Session(app)
    return app


def brokenApp() -> Flask:
    """Only used when normal configuration is busted so you get a working
    webserver with a reasonable explanation to visitors."""
    broken = Flask(__name__)

    @broken.route("/", defaults={"path": ""})
    @broken.route("/<path:path>")
    def catch_all(path: str) -> Response:
        return make_response(
            {
                "error": "Service unavailable due to configuration issue.",
            },
            503,
        )

    return broken


def getArelle() -> ArelleReportProcessor:
    return ArelleReportProcessor(
        taxonomyPackages=current_app.config["TAXONOMY_PACKAGES"],
        workOffline=current_app.config["ARELLE_WORK_OFFLINE"],
    )


def format_timedelta(td: timedelta) -> str:
    total_seconds = int(td.total_seconds())
    parts = []

    days, remainder = divmod(total_seconds, 86400)  # 86400 seconds in a day
    hours, remainder = divmod(remainder, 3600)
    minutes, seconds = divmod(remainder, 60)

    def plural(amount: int, unit_singular: str) -> str:
        if amount > 1:
            return f"{amount} {unit_singular}s"
        else:
            return f"{amount} {unit_singular}"

    if days:
        parts.append(plural(days, "day"))
    if hours:
        parts.append(plural(hours, "hour"))
    if minutes:
        parts.append(plural(minutes, "minute"))
    if seconds:
        parts.append(plural(seconds, "second"))

    return " ".join(parts)


@convert_bp.route("/")
def index() -> Response:
    return Response(
        render_template(
            "excel-to-xbrl-converter.html.jinja",
            existing_conversions=hasConversions(),
            ENABLE_CAPTCHA=ENABLE_CAPTCHA,
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


@convert_bp.route("/generate_captcha", methods=["GET"])
def generate_captcha() -> dict:
    """Generate a simple math captcha and store the answer in the session."""
    num1 = randint(1, 10)
    num2 = randint(1, 10)
    session.pop("captcha_answer", None)
    session["captcha_answer"] = num1 + num2
    session.modified = True
    return {"question": f"What is {num1} + {num2}?"}


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


@convert_bp.route(f"/locales/available_{DEPLOYMENT_DATETIME_FOR_URL}")
def available_locales() -> Response:
    resp = make_response(jsonify(LOCALES_CACHE))
    resp.headers["Cache-Control"] = "public, max-age=31536000, immutable"
    return resp


@convert_bp.route("/debug_session")
def debug_session() -> Response:
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

    if ENABLE_CAPTCHA is True:
        # Validate captcha
        captcha_input = request.form.get("captcha", type=int)
        captcha_answer = session.pop("captcha_answer", None)
        if not captcha_answer or captcha_input != captcha_answer:
            flash(
                message="Invalid captcha. Please confirm you are human by calculating the correct result and try again.",
                category="error",
            )
            return make_response(redirect(url_for("basic.index")))
        # Validate CSRF token
        csrf_token = request.form.get("csrf_token")
        if not csrf_token or csrf_token != session.get("csrf_token"):
            flash(message="Invalid CSRF token. Please try again.", category="error")
            return make_response(redirect(url_for("basic.index")))

    blobs = request.files.getlist("file")
    if len(blobs) > 1:
        return make_response({"error": "Too many files"}, 400)
    blob = blobs[0]
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
    result = ConversionResultsBuilder()
    conversion = session.setdefault(result.conversionId, {})
    conversion["date"] = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    conversion["excel"] = FilelikeAndFileName(
        fileContent=blob.stream.read(), filename=blob.filename
    )
    return make_response(
        redirect(url_for("basic.convert", id=result.conversionId), code=303)
    )


@convert_bp.route("/conversions/<string:id>", methods=["GET"])
def convert(id: str) -> Response:
    try:
        if id not in session:
            return make_response(
                render_template(
                    "conversion-results.html.jinja",
                    expired=True,
                    conversion_result=None,
                ),
                404,
            )

        conversion = session[id]
        if "results" not in conversion:
            results = doConversion(conversion, id)
            conversion["results"] = results.toDict()
            conversion["successful"] = results.conversionSuccessful

        results = ConversionResults.fromDict(conversion["results"])
        devInfo = request.args.get("show_developer_messages") == "true"

        return Response(
            render_template(
                "conversion-results.html.jinja",
                conversion_result=results,
                dev=devInfo,
                conversion_date=conversion["date"],
                upload_filename=getUploadFilename(id),
            )
        )
    except Exception as e:
        if current_app.debug:
            raise
        else:
            L.exception("Exception during conversion", exc_info=e)
            return make_response({"error": str(e)}, 500)


def getUploadFilename(id: str) -> str:
    conversion = session.get(id)
    if not (conversion and "excel" in conversion):
        return ""

    excel = FilelikeAndFileName(*conversion["excel"])
    return excel.filename


def doConversion(conversion: dict, id: str) -> ConversionResults:
    resultBuilder = ConversionResultsBuilder(conversionId=id)
    try:
        with resultBuilder.processingContext(f"Conversion {id}") as pc:
            upload = FilelikeAndFileName(*conversion["excel"])

            pc.mark(
                "Extracting data from Excel",
                additionalInfo=f"Using file: {upload.filename}",
            )
            excel = ExcelProcessor(upload.fileLike(), resultBuilder, VSME_DEFAULTS)
            report = excel.populateReport()
            if not report.hasFacts:
                resultBuilder.addMessage(
                    "No facts found in InlineReport (likely due to earlier errors). Stopping here.",
                    Severity.ERROR,
                    MessageType.Conversion,
                )
                return resultBuilder.build()

            pc.mark(
                "Generating Inline Report",
                additionalInfo=f"({report.factCount} facts to include)",
            )
            report_package = report.getInlineReportPackage()
            resultBuilder.addMessage(
                f'Inline XBRL report "{report_package.filename}" created (containing {report.factCount} facts)',
                Severity.INFO,
                MessageType.Conversion,
            )
            if not resultBuilder.conversionSuccessful:
                return resultBuilder.build()

            pc.mark(
                "Validating Inline Report",
                additionalInfo=f"Using Arelle (XBRL Certified Software™) [{ARELLE_VERSION_INFORMATION}]",
            )
            arelle_results = getArelle().validateReportPackage(report_package)
            resultBuilder.addMessages(arelle_results.messages)
            conversion["zip"] = report_package
    except Exception as e:
        message = next(iter(e.args), "")
        resultBuilder.addMessage(
            f"Exception encountered during processing. {message=}",
            Severity.ERROR,
            MessageType.Conversion,
        )
        L.exception("Exception encountered", exc_info=e)

    return resultBuilder.build()


@convert_bp.route("/downloadFile/<string:id>/<string:ftype>/", methods=["GET", "HEAD"])
def downloadFile(id: str, ftype: str) -> Response:
    """Download the converted file from the session."""
    if id not in session:
        return make_response({"error": "No file found"}, 404)

    if ftype not in ("json", "viewer", "zip", "excel"):
        return make_response({"error": f"File type {ftype} not found."}, 404)

    session_data = session[id]
    if "zip" not in session_data:
        return make_response(
            {"error": "No report generated. Nothing to download."}, 404
        )

    if ftype not in session_data:
        reportPackage = FilelikeAndFileName(*session_data["zip"])
        arelle = getArelle()
        if ftype == "json":
            session_data[ftype] = arelle.generateXBRLJson(reportPackage).xBRL_JSON
        elif ftype == "viewer":
            session_data[ftype] = arelle.generateInlineViewer(reportPackage).viewer
        else:
            return make_response({"error": "No file found"}, 404)

    if request.method == "HEAD":
        return Response(status=200, headers={"X-File-Ready": "true"})

    stuff = FilelikeAndFileName(*session[id][ftype])
    return send_file(
        stuff.fileLike(),
        as_attachment=True,
        download_name=stuff.filename,
        mimetype="text/html",
    )


def hasConversions() -> bool:
    return bool(getConversions())


def getConversions() -> dict[str, Any]:
    conversions = {
        key: value
        for key, value in session.items()
        if key not in {"_permanent", "csrf_token", "captcha_answer"}
    }
    return conversions


@convert_bp.route("/conversions/")
def conversions() -> Response:
    return Response(
        render_template(
            "conversions.html.jinja",
            conversions=getConversions(),
            lifetime=current_app.config["PERMANENT_SESSION_LIFETIME"],
        )
    )


@convert_bp.route("/delete/<string:id>", methods=["POST"])
def delete(id: str) -> Response:
    session.pop(id, None)
    return make_response(redirect(url_for("basic.conversions"), code=303))


@convert_bp.route("/delete/_all", methods=["POST"])
def delete_all() -> Response:
    for k in getConversions():
        session.pop(k, None)
    return make_response(redirect(url_for("basic.conversions"), code=303))


@convert_bp.route("/viewer/<string:id>/", methods=["GET", "HEAD"])
def viewer(id: str) -> Response:
    conversion = session[id]
    if (stuff := conversion.get("viewer")) is None:
        stuff = (
            getArelle()
            .generateInlineViewer(FilelikeAndFileName(*conversion["zip"]))
            .viewer
        )
        conversion["viewer"] = stuff
        if request.method == "HEAD":
            return Response(status=200, headers={"X-File-Ready": "true"})

    return send_file(
        stuff.fileLike(),
        as_attachment=False,
        download_name=stuff.filename,
        mimetype="text/html",
    )


if __name__ == "__main__":
    create_app().run(debug=True)
