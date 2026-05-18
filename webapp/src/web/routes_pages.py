"""HTML pages."""

from flask import Blueprint, render_template

from common import storage

from .auth import current_user, login_required, storage_email

bp = Blueprint("pages", __name__)


@bp.route("/", methods=["GET"])
def index():
    user = current_user()
    if not user:
        return render_template("index.html", result=None, logged_in=False)
    token = storage.set_current_user(storage_email(user["email"]))
    try:
        return render_template("index.html", result=None, logged_in=True)
    finally:
        storage.reset_current_user(token)


@bp.route("/stats", methods=["GET"])
@login_required
def stats():
    return render_template("stats.html")
