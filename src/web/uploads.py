"""Upload form handler and per-user figure serving."""

import hashlib

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
from .auth import login_required, upload_allowed_required

ALLOWED_EXTENSIONS = {"png", "jpg", "jpeg", "gif", "webp", "pdf"}

bp = Blueprint("uploads", __name__)


@bp.route("/upload", methods=["POST"])
@login_required
@upload_allowed_required
def upload():
    files = [f for f in request.files.getlist("images") if f and f.filename]
    if not files:
        flash("No file selected.", "error")
        return redirect(url_for("pages.index"))

    raw_dir = storage.raw_uploads_dir()
    raw_dir.mkdir(parents=True, exist_ok=True)

    inputs: list[agent.ProcessImageInput] = []
    for file in files:
        ext = (
            file.filename.rsplit(".", 1)[-1].lower()
            if "." in file.filename
            else ""
        )
        if ext not in ALLOWED_EXTENSIONS:
            flash(
                f"Skipped {file.filename}: unsupported file type .{ext}",
                "error",
            )
            continue
        image_bytes = file.read()
        if not image_bytes:
            flash(f"Skipped {file.filename}: empty file.", "error")
            continue
        hasher = hashlib.sha256()
        hasher.update(image_bytes)
        hasher.update(file.filename.encode("utf-8"))
        safe_name = f"{hasher.hexdigest()}.{ext}"
        saved_path = raw_dir / safe_name
        if not saved_path.exists():
            saved_path.write_bytes(image_bytes)
        inputs.append(
            agent.ProcessImageInput(
                image_path=saved_path, source_image=safe_name
            )
        )

    if not inputs:
        return redirect(url_for("pages.index"))

    with_solution = bool(request.form.get("with_solution"))

    try:
        result = agent.process_images(inputs, with_solution=with_solution)
    except Exception as e:
        flash(f"Agent error: {e}", "error")
        return redirect(url_for("pages.index"))

    return render_template("index.html", result=result, logged_in=True)


@bp.route("/figures/<path:filename>", methods=["GET"])
@login_required
def serve_figure(filename):
    return send_from_directory(storage.figures_dir(), filename)
