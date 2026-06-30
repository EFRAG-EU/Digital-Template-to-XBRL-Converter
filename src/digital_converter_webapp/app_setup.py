"""Flask app factory helpers split out of :mod:`digital_converter_webapp`."""

from __future__ import annotations

import logging
from datetime import timedelta

from flask import Flask, Response, current_app, g, make_response

from mireport.arelle.report_info import ArelleReportProcessor

L = logging.getLogger(__name__)


def configure_logging() -> None:
    """Initial logging setup; safe to call multiple times."""
    logging.basicConfig(
        format="%(asctime)s %(levelname)-8s %(message)s",
        datefmt="[%Y-%m-%d %H:%M:%S]",
        level=logging.INFO,
    )
    logging.captureWarnings(True)


def configure_session(app: Flask) -> Flask | None:
    """Apply session/redis configuration.

    Returns a fallback :func:`broken_app` if the configuration is unworkable;
    the caller should ``return`` that to short-circuit ``create_app``.
    """
    if (
        app.config.get("DEPLOYMENT", "development") == "development"
        and "SESSION_TYPE" not in app.config
    ):
        # DEVELOPER MODE.  Insecure.  DO NOT USE IN PRODUCTION.
        app.config.from_mapping(
            SECRET_KEY="dev",
            SESSION_TYPE="filesystem",
            SESSION_FILE_DIR="flask_session",
            SESSION_PERMANENT="False",
            PERMANENT_SESSION_LIFETIME=timedelta(hours=1),
        )
        L.critical("Deployed in DEVELOPER mode. Insecure.")
        return None

    if app.config["SESSION_TYPE"] == "redis" and "SESSION_REDIS" not in app.config:
        return _configure_redis_session(app)

    L.critical(f"Can't work with current configuration. {app.config=}")
    return broken_app()


def _configure_redis_session(app: Flask) -> Flask | None:
    try:
        try:
            from flask_rq import RQ
            from redis import ConnectionError, Redis  # type: ignore
        except ImportError:
            L.critical(
                "REDIS AND/OR RQ SUPPORT ISN'T AVAILABLE. APP STARTUP ABORTED. "
                "YOU NEED TO FIX YOUR CONFIGURATION."
            )
            return broken_app()

        redis_url = app.config.get("REDIS_URL", "redis://127.0.0.1:6379")
        try:
            rs = Redis.from_url(redis_url)
            rs.ping()
        except ConnectionError:
            L.critical(
                "REDIS ISN'T RUNNING. APP STARTUP ABORTED. "
                "YOU NEED TO FIX YOUR CONFIGURATION."
            )
            return broken_app()

        app.config["SESSION_REDIS"] = rs
        app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(hours=1)
        rq = RQ()
        rq.init_app(app)
    except Exception as e:
        L.critical(
            "AN UNKNOWN EXCEPTION OCCURRED WHILE CONFIGURING SUPPORT FOR REDIS "
            "AND RQ. APP STARTUP ABORTED. YOU NEED TO FIX YOUR CONFIGURATION.",
            exc_info=e,
        )
        return broken_app()
    return None


def configure_arelle(app: Flask) -> None:
    """Discover taxonomy packages and decide offline/online mode."""
    packages = ArelleReportProcessor.getTaxonomyPackagesFromDir(
        app.config.get("TAXONOMY_PACKAGE_DIR")
    )
    app.config["TAXONOMY_PACKAGES"] = packages
    offline = app.config["ARELLE_WORK_OFFLINE"] = app.config.get(
        "ARELLE_WORK_OFFLINE", bool(packages)
    )
    if offline:
        L.info(
            f"Configured to use Arelle offline with {len(packages)} "
            f"taxonomy packages: [{', '.join(str(p) for p in sorted(packages))}]"
        )


def get_arelle() -> ArelleReportProcessor:
    """Return a per-request-cached :class:`ArelleReportProcessor`."""
    if (arelle := getattr(g, "arelle", None)) is None:
        arelle = ArelleReportProcessor(
            taxonomyPackages=current_app.config["TAXONOMY_PACKAGES"],
            workOffline=current_app.config["ARELLE_WORK_OFFLINE"],
        )
        g.arelle = arelle
    return arelle


def broken_app() -> Flask:
    """Stand-in app served when configuration is unworkable.

    Returns 503 to every request so visitors get a meaningful response while
    the operator fixes the deployment.
    """
    broken = Flask(__name__)

    @broken.route("/", defaults={"path": ""})
    @broken.route("/<path:path>")
    def catch_all(path: str) -> Response:
        return make_response(
            {"error": "Service unavailable due to configuration issue."},
            503,
        )

    return broken
