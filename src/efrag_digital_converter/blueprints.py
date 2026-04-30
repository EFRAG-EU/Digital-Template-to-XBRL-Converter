from flask import Blueprint

convert_bp = Blueprint(
    "basic", __name__, template_folder="templates", static_folder="static"
)
