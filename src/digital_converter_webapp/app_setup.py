"""Configuration helpers for :func:`digital_converter_webapp.create_app`."""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from enum import StrEnum

from cachelib.file import FileSystemCache
from flask import Flask, Response, current_app, make_response

from mireport.arelle.report_info import ArelleReportProcessor
from mireport.pdf_converter import (
    PATH_ENV_VAR,
    PdfSupplementaryMode,
    PdfToolNotFoundError,
    findConverter,
)
from mireport.stringutil import truthy

from .migration import MIGRATION_WORKING

L = logging.getLogger(__name__)

# When this process started; shown by base.html.jinja and sent as the
# X-Deployment-Datetime header so you can tell which deployment answered.
DEPLOYMENT_DATETIME = datetime.now(UTC)


class ResultsLayout(StrEnum):
    """How the results page lays out the messages: one table, or a collapsible
    section per stage of the conversion."""

    FLAT = "flat"
    SECTIONS = "sections"


# What the templates show as config.BRAND_*; a deployment overrides any of them
# with FLASK_BRAND_* (an empty value hides that element). BRAND_TEMPLATE_DIR,
# unset by default, names a directory holding a replacement
# _brand-content.html.jinja.
BRANDING_DEFAULTS: dict[str, str] = {
    "BRAND_PRODUCT_NAME": "VS and VSME Digital Template to XBRL Converter",
    "BRAND_ORGANISATION_NAME": "EFRAG",
    "BRAND_LOGO_URL": "https://www.efrag.org/themes/custom/dms_theme/logo.svg",
    "BRAND_FAVICON_URL": "https://www.efrag.org/themes/custom/dms_theme/favicon.ico",
    "BRAND_CONTACT_EMAIL": "vsme@efrag.org",
    "BRAND_TEMPLATE_URL": (
        "https://xbrl.efrag.org/downloads/vsme/VSME-Digital-Template-latest.xlsx"
    ),
    "BRAND_SAMPLE_URL": (
        "https://xbrl.efrag.org/downloads/vsme/VSME-Digital-Template-sample-latest.xlsx"
    ),
}


def configure_logging() -> None:
    """Initial logging setup; safe to call more than once."""
    logging.basicConfig(
        format="%(asctime)s %(levelname)-8s %(message)s",
        datefmt="[%Y-%m-%d %H:%M:%S]",
        level=logging.INFO,
    )
    logging.captureWarnings(True)


def normalise_feature_flags(app: Flask) -> None:
    """Turn the feature flags into real booleans (and enums) so request-time
    checks can rely on them."""
    app.config["ENABLE_CAPTCHA"] = truthy(app.config.get("ENABLE_CAPTCHA", False))
    app.config["ENABLE_MIGRATION"] = (
        truthy(app.config.get("ENABLE_MIGRATION", False)) and MIGRATION_WORKING
    )
    app.config["ENABLE_SUPPLEMENTARY_PDFS"] = truthy(
        app.config.get("ENABLE_SUPPLEMENTARY_PDFS", False)
    )
    app.config["EXCEL_PARSING_WARNINGS_AS_DEVINFO"] = truthy(
        app.config.get("EXCEL_PARSING_WARNINGS_AS_DEVINFO", False)
    )
    pdfMode = app.config.get("PDF_SUPPLEMENTARY_MODE", PdfSupplementaryMode.DOCSET)
    try:
        pdfMode = PdfSupplementaryMode(pdfMode)
    except ValueError:
        L.warning(
            f"Ignoring unknown PDF_SUPPLEMENTARY_MODE={pdfMode!r}; using "
            f"{PdfSupplementaryMode.DOCSET!r}."
        )
        pdfMode = PdfSupplementaryMode.DOCSET
    app.config["PDF_SUPPLEMENTARY_MODE"] = pdfMode
    layout = app.config.get("RESULTS_LAYOUT", ResultsLayout.FLAT)
    try:
        layout = ResultsLayout(layout)
    except ValueError:
        L.warning(
            f"Ignoring unknown RESULTS_LAYOUT={layout!r}; using {ResultsLayout.FLAT!r}."
        )
        layout = ResultsLayout.FLAT
    app.config["RESULTS_LAYOUT"] = layout


def applyBrandingDefaults(app: Flask) -> None:
    """Fill in each branding key the configuration left unset."""
    for key, value in BRANDING_DEFAULTS.items():
        app.config.setdefault(key, value)


def configure_session_backend(app: Flask) -> bool:
    """Configure server-side session storage (filesystem in developer mode,
    redis otherwise). Returns False if the configuration is unusable."""
    if (
        "development" == app.config.get("DEPLOYMENT", "development")
        and "SESSION_TYPE" not in app.config
    ):
        # DEVELOPER MODE. Insecure. DO NOT USE IN PRODUCTION.
        # Defaults that respect any SECRET_KEY/SESSION_CACHELIB already
        # configured (e.g. by a test config). "cachelib" with an explicit
        # FileSystemCache replaces the deprecated "filesystem" backend.
        if "SESSION_FILE_DIR" in app.config:
            L.warning(
                "SESSION_FILE_DIR is no longer supported and will be ignored;"
                " set SESSION_CACHELIB to a cachelib instance instead."
            )
        app.config.from_mapping(
            SECRET_KEY=app.config.get("SECRET_KEY") or "dev",
            SESSION_TYPE="cachelib",
            SESSION_CACHELIB=app.config.get("SESSION_CACHELIB")
            or FileSystemCache("flask_session", threshold=500),
            SESSION_PERMANENT=True,
            PERMANENT_SESSION_LIFETIME=timedelta(hours=1),
        )
        L.critical("Deployed in DEVELOPER mode. Insecure.")
        return True

    if app.config.get("SESSION_TYPE") == "redis" and "SESSION_REDIS" not in app.config:
        return _configure_redis_sessions(app)

    L.critical(f"Can't work with current configuration. {app.config=}")
    return False


def _configure_redis_sessions(app: Flask) -> bool:
    """Connect to redis for session storage and set up RQ. Returns False if
    redis support is unavailable or the connection fails."""
    try:
        try:
            from flask_rq import RQ
            from redis import ConnectionError, Redis  # type: ignore
        except ImportError:
            L.critical(
                "Redis and/or RQ support isn't available. App startup aborted. You need to fix your configuration.".upper()
            )
            return False

        redisUrl = app.config.get("REDIS_URL", "redis://127.0.0.1:6379")
        try:
            rs = Redis.from_url(redisUrl)
            rs.ping()
        except ConnectionError:
            L.critical(
                "Redis isn't running. App startup aborted. You need to fix your configuration.".upper()
            )
            return False

        # Should have a working Redis connection and RQ instance at this point.
        app.config["SESSION_REDIS"] = rs
        app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(hours=1)
        rq = RQ()
        rq.init_app(app)
        return True

    except Exception as e:
        L.critical(
            "An unknown exception occurred while configuring support for redis and RQ. App startup aborted. You need to fix your configuration.".upper(),
            exc_info=e,
        )
        return False


def configure_arelle(app: Flask) -> None:
    """Discover the taxonomy packages and decide whether Arelle works offline."""
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
            f"Configured to use Arelle offline with {len(taxonomyPackageList)} taxonomy packages: [{', '.join(str(a) for a in sorted(taxonomyPackageList))}]"
        )


def report_pdf_converter(app: Flask) -> None:
    """Say at startup which PDF converter uploads will get, if any.

    Missing or misconfigured is not fatal — supplementary PDFs are attached
    unconverted — but silence until the first upload is not helpful.
    """
    if not app.config["ENABLE_SUPPLEMENTARY_PDFS"]:
        L.info("Supplementary PDF uploads are disabled.")
        return
    try:
        converter = findConverter()
    except PdfToolNotFoundError as e:
        L.warning(
            f"Supplementary PDFs will be attached unconverted. {e} Set "
            f"{PATH_ENV_VAR} if a converter is installed somewhere off PATH."
        )
        return
    L.info(f"Using PDF converter {converter}.")


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
