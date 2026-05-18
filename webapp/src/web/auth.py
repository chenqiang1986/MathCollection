"""Google OAuth flow + login/upload-permission decorators."""

import os
from functools import wraps

from authlib.integrations.flask_client import OAuth
from flask import (
    Blueprint,
    Flask,
    flash,
    jsonify,
    redirect,
    request,
    session,
    url_for,
)
from common import storage
from common.db_setup.setup import init_user

UPLOAD_WHITELIST = {"chenqiang19860101@gmail.com", "chenhenrybunny@gmail.com"}
GUEST_USER = "guest"
GOOGLE_DISCOVERY_URL = "https://accounts.google.com/.well-known/openid-configuration"

bp = Blueprint("auth", __name__)
oauth = OAuth()


def current_user() -> dict | None:
    return session.get("user")


def is_whitelisted(email: str | None) -> bool:
    return bool(email) and email.strip().lower() in UPLOAD_WHITELIST


def storage_email(email: str | None) -> str:
    return email if is_whitelisted(email) else GUEST_USER


def login_required(view):
    @wraps(view)
    def wrapper(*args, **kwargs):
        user = current_user()
        if not user or not user.get("email"):
            if request.method == "GET" and request.accept_mimetypes.accept_html:
                return redirect(url_for("auth.login", next=request.path))
            return jsonify({"error": "login required"}), 401
        token = storage.set_current_user(storage_email(user["email"]))
        try:
            return view(*args, **kwargs)
        finally:
            storage.reset_current_user(token)
    return wrapper


def upload_allowed_required(view):
    @wraps(view)
    def wrapper(*args, **kwargs):
        user = current_user()
        if not user or not is_whitelisted(user.get("email")):
            if request.method == "GET" and request.accept_mimetypes.accept_html:
                flash("Your account is not allowed to upload.", "error")
                return redirect(url_for("pages.index"))
            return jsonify({"error": "upload not permitted for this account"}), 403
        return view(*args, **kwargs)
    return wrapper


def init_auth(app: Flask) -> None:
    client_id = os.environ.get("GOOGLE_CLIENT_ID")
    client_secret = os.environ.get("GOOGLE_CLIENT_SECRET")
    if not client_id or not client_secret:
        raise RuntimeError(
            "GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET must be set in environment"
        )
    oauth.init_app(app)
    oauth.register(
        name="google",
        client_id=client_id,
        client_secret=client_secret,
        server_metadata_url=GOOGLE_DISCOVERY_URL,
        client_kwargs={"scope": "openid email profile"},
    )

    @app.context_processor
    def inject_user():
        user = current_user()
        return {
            "current_user": user,
            "can_upload": is_whitelisted((user or {}).get("email")),
        }


@bp.route("/login", methods=["GET"])
def login():
    next_url = request.args.get("next") or url_for("pages.index")
    session["post_login_redirect"] = next_url
    redirect_uri = url_for("auth.auth_callback", _external=True)
    return oauth.google.authorize_redirect(redirect_uri)


@bp.route("/auth/callback", methods=["GET"])
def auth_callback():
    try:
        token = oauth.google.authorize_access_token()
    except Exception as e:
        flash(f"Login failed: {e}", "error")
        return redirect(url_for("pages.index"))
    userinfo = token.get("userinfo") or oauth.google.userinfo(token=token)
    email = (userinfo or {}).get("email")
    if not email:
        flash("Login failed: no email returned by Google.", "error")
        return redirect(url_for("pages.index"))
    session["user"] = {
        "email": email,
        "name": userinfo.get("name") or email,
        "picture": userinfo.get("picture"),
    }
    session.permanent = True
    token = storage.set_current_user(storage_email(email))
    try:
        init_user()
    finally:
        storage.reset_current_user(token)
    next_url = session.pop("post_login_redirect", None) or url_for("pages.index")
    return redirect(next_url)


@bp.route("/logout", methods=["GET", "POST"])
def logout():
    session.pop("user", None)
    session.pop("post_login_redirect", None)
    return redirect(url_for("pages.index"))
