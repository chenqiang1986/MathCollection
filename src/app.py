import os
import uuid
from functools import wraps
from pathlib import Path

from authlib.integrations.flask_client import OAuth
from dotenv import load_dotenv
from flask import (
    Flask,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    send_from_directory,
    session,
    url_for,
)
from werkzeug.utils import secure_filename

import agent_self_planning as agent
import storage

load_dotenv()

ALLOWED_EXTENSIONS = {"png", "jpg", "jpeg", "gif", "webp"}
MAX_UPLOAD_BYTES = 10 * 1024 * 1024  # 10 MB
DEFAULT_PAGE_SIZE = 5
MAX_PAGE_SIZE = 50
MAX_SAMPLE_SIZE = 200

UPLOAD_DIR = Path(__file__).resolve().parent.parent / "uploads"

GOOGLE_DISCOVERY_URL = "https://accounts.google.com/.well-known/openid-configuration"


def _parse_float(name: str) -> float | None:
    raw = request.args.get(name)
    if raw is None or raw == "":
        return None
    try:
        return float(raw)
    except ValueError:
        return None


def _parse_filters() -> dict:
    category = request.args.get("category") or None
    full_range_max = _parse_float("range_max")
    return {
        "category": category,
        "min_time": _parse_float("min_time"),
        "max_time": _parse_float("max_time"),
        "full_range_max": full_range_max,
    }


def _current_user() -> dict | None:
    return session.get("user")


def login_required(view):
    @wraps(view)
    def wrapper(*args, **kwargs):
        user = _current_user()
        if not user or not user.get("email"):
            if request.method == "GET" and request.accept_mimetypes.accept_html:
                return redirect(url_for("login", next=request.path))
            return jsonify({"error": "login required"}), 401
        token = storage.set_current_user(user["email"])
        try:
            storage.init_index()
            return view(*args, **kwargs)
        finally:
            storage.reset_current_user(token)
    return wrapper


def create_app() -> Flask:
    app = Flask(__name__)
    app.config["SECRET_KEY"] = os.environ.get("FLASK_SECRET_KEY", "dev-secret")
    app.config["MAX_CONTENT_LENGTH"] = MAX_UPLOAD_BYTES
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

    client_id = os.environ.get("GOOGLE_CLIENT_ID")
    client_secret = os.environ.get("GOOGLE_CLIENT_SECRET")
    if not client_id or not client_secret:
        raise RuntimeError(
            "GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET must be set in environment"
        )

    oauth = OAuth(app)
    oauth.register(
        name="google",
        client_id=client_id,
        client_secret=client_secret,
        server_metadata_url=GOOGLE_DISCOVERY_URL,
        client_kwargs={"scope": "openid email profile"},
    )

    @app.context_processor
    def inject_user():
        return {"current_user": _current_user()}

    @app.route("/login", methods=["GET"])
    def login():
        next_url = request.args.get("next") or url_for("index")
        session["post_login_redirect"] = next_url
        redirect_uri = url_for("auth_callback", _external=True)
        return oauth.google.authorize_redirect(redirect_uri)

    @app.route("/auth/callback", methods=["GET"])
    def auth_callback():
        try:
            token = oauth.google.authorize_access_token()
        except Exception as e:
            flash(f"Login failed: {e}", "error")
            return redirect(url_for("index"))
        userinfo = token.get("userinfo") or oauth.google.userinfo(token=token)
        email = (userinfo or {}).get("email")
        if not email:
            flash("Login failed: no email returned by Google.", "error")
            return redirect(url_for("index"))
        session["user"] = {
            "email": email,
            "name": userinfo.get("name") or email,
            "picture": userinfo.get("picture"),
        }
        session.permanent = True
        next_url = session.pop("post_login_redirect", None) or url_for("index")
        return redirect(next_url)

    @app.route("/logout", methods=["GET", "POST"])
    def logout():
        session.pop("user", None)
        session.pop("post_login_redirect", None)
        return redirect(url_for("index"))

    @app.route("/", methods=["GET"])
    def index():
        user = _current_user()
        if not user:
            return render_template("index.html", result=None, logged_in=False)
        token = storage.set_current_user(user["email"])
        try:
            storage.init_index()
            return render_template("index.html", result=None, logged_in=True)
        finally:
            storage.reset_current_user(token)

    @app.route("/stats", methods=["GET"])
    @login_required
    def stats():
        return render_template("stats.html")

    @app.route("/api/stats/categories", methods=["GET"])
    @login_required
    def api_stats_categories():
        return jsonify({"categories": storage.category_counts()})

    @app.route("/api/stats/difficulty", methods=["GET"])
    @login_required
    def api_stats_difficulty():
        category = request.args.get("category") or None
        return jsonify(
            {
                "category": category,
                "buckets": storage.difficulty_distribution(category),
            }
        )

    @app.route("/upload", methods=["POST"])
    @login_required
    def upload():
        file = request.files.get("image")
        if not file or not file.filename:
            flash("No file selected.", "error")
            return redirect(url_for("index"))

        ext = file.filename.rsplit(".", 1)[-1].lower() if "." in file.filename else ""
        if ext not in ALLOWED_EXTENSIONS:
            flash(f"Unsupported file type: .{ext}", "error")
            return redirect(url_for("index"))

        image_bytes = file.read()
        if not image_bytes:
            flash("Uploaded file is empty.", "error")
            return redirect(url_for("index"))

        safe_name = f"{uuid.uuid4()}_{secure_filename(file.filename)}"
        saved_path = UPLOAD_DIR / safe_name
        saved_path.write_bytes(image_bytes)

        with_solution = bool(request.form.get("with_solution"))

        try:
            result = agent.process_image(
                image_path=saved_path,
                source_image=safe_name,
                with_solution=with_solution,
            )
        except Exception as e:
            flash(f"Agent error: {e}", "error")
            return redirect(url_for("index"))

        return render_template("index.html", result=result, logged_in=True)

    @app.route("/figures/<path:filename>", methods=["GET"])
    @login_required
    def figures(filename):
        return send_from_directory(storage.figures_dir(), filename)

    @app.route("/api/summary", methods=["GET"])
    @login_required
    def api_summary():
        return jsonify(storage.index_summary())

    @app.route("/api/problems", methods=["GET"])
    @login_required
    def api_problems():
        filters = _parse_filters()
        try:
            page = max(1, int(request.args.get("page", 1)))
        except ValueError:
            page = 1
        try:
            page_size = int(request.args.get("page_size", DEFAULT_PAGE_SIZE))
        except ValueError:
            page_size = DEFAULT_PAGE_SIZE
        page_size = max(1, min(MAX_PAGE_SIZE, page_size))

        total, ids = storage.query_index(
            page=page, page_size=page_size, **filters
        )
        problems = [p for p in (storage.get_problem(pid) for pid in ids) if p]
        return jsonify(
            {
                "total": total,
                "page": page,
                "page_size": page_size,
                "problems": problems,
            }
        )

    @app.route("/api/problems/<problem_id>", methods=["DELETE"])
    @login_required
    def api_delete_problem(problem_id):
        deleted = storage.delete_problem(problem_id)
        if not deleted:
            return jsonify({"error": "not found"}), 404
        return jsonify({"deleted": problem_id})

    @app.route("/api/sample", methods=["GET"])
    @login_required
    def api_sample():
        filters = _parse_filters()
        try:
            n = int(request.args.get("n", DEFAULT_PAGE_SIZE))
        except ValueError:
            n = DEFAULT_PAGE_SIZE
        n = max(1, min(MAX_SAMPLE_SIZE, n))
        ids = storage.sample_index(n, **filters)
        problems = [p for p in (storage.get_problem(pid) for pid in ids) if p]
        return jsonify({"problems": problems})

    return app


app = create_app()


if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=5001)
