"""Upload form handler and per-user figure serving."""

import uuid

from flask import (
    Blueprint,
    flash,
    redirect,
    render_template,
    request,
    send_from_directory,
    url_for,
)
from lib import agent, storage
from werkzeug.utils import secure_filename

from .auth import login_required, upload_allowed_required

ALLOWED_EXTENSIONS = {"png", "jpg", "jpeg", "gif", "webp", "pdf"}

bp = Blueprint("uploads", __name__)


@bp.route("/upload", methods=["POST"])
@login_required
@upload_allowed_required
def upload():
    file = request.files.get("image")
    if not file or not file.filename:
        flash("No file selected.", "error")
        return redirect(url_for("pages.index"))

    ext = file.filename.rsplit(".", 1)[-1].lower() if "." in file.filename else ""
    if ext not in ALLOWED_EXTENSIONS:
        flash(f"Unsupported file type: .{ext}", "error")
        return redirect(url_for("pages.index"))

    image_bytes = file.read()
    if not image_bytes:
        flash("Uploaded file is empty.", "error")
        return redirect(url_for("pages.index"))

    safe_name = f"{uuid.uuid4()}_{secure_filename(file.filename)}"
    raw_dir = storage.raw_uploads_dir()
    raw_dir.mkdir(parents=True, exist_ok=True)
    saved_path = raw_dir / safe_name
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
        return redirect(url_for("pages.index"))

    return render_template("index.html", result=result, logged_in=True)


@bp.route("/figures/<path:filename>", methods=["GET"])
@login_required
def serve_figure(filename):
    return send_from_directory(storage.figures_dir(), filename)
