"""Flask app for the Excel → Inline XBRL converter.

``create_app`` lives here. The routes are in :mod:`.routes` (and
:mod:`.migration`); configuration, the session model, the conversion pipeline
and the other helpers are in the sibling modules.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from dotenv import load_dotenv
from flask import Flask
from flask_session import Session  # type: ignore

import mireport
from mireport import loadBuiltInTaxonomyJSON
from mireport.conversionresults import MessageType, Severity

from .app_setup import (
    DEPLOYMENT_DATETIME,
    brokenApp,
    configure_arelle,
    configure_logging,
    configure_session_backend,
    normalise_feature_flags,
    report_pdf_converter,
)
from .background import configureStepStore
from .forms import MAX_FILE_SIZE
from .locales import make_locale_json
from .routes import register_routes
from .template_helpers import format_timedelta, getUploadFilename


def create_app(test_config: Mapping[str, Any] | None = None) -> Flask:
    # Get logging working
    configure_logging()

    # Get taxonomy related objects loaded
    loadBuiltInTaxonomyJSON()

    app = Flask(__name__, static_folder=None)
    app.config["MAX_CONTENT_LENGTH"] = MAX_FILE_SIZE
    app.config["LOCALE_JSON"] = make_locale_json()
    if test_config is not None:
        # Tests are hermetic: only the supplied config, never the developer's
        # ".env" (e.g. redis sessions, captcha on) or FLASK_* environment.
        app.config.from_mapping(test_config)
    else:
        # Load configuration from any ".env" file plus FLASK_* environment
        # variables.
        load_dotenv()
        app.config.from_prefixed_env()

    if not configure_session_backend(app):
        return brokenApp()
    configureStepStore(app)

    # Normalise feature flags so request-time checks can rely on real booleans
    normalise_feature_flags(app)

    # app looks to be working, install routes
    register_routes(app)

    # Discover all the taxonomy packages up front
    configure_arelle(app)

    report_pdf_converter(app)

    # Install enumeration classes for use in templates
    app.jinja_env.globals.update(
        {
            Severity.__name__: Severity,
            MessageType.__name__: MessageType,
            "deployment_datetime": DEPLOYMENT_DATETIME,
            "mireport_version": mireport.__version__,
            format_timedelta.__name__: format_timedelta,
            getUploadFilename.__name__: getUploadFilename,
        }
    )

    # Use server-side sessions
    Session(app)
    return app


if __name__ == "__main__":
    create_app().run(debug=True)
