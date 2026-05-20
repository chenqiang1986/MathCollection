"""Upload form handler and per-user figure serving.

Uploads only save the raw file and insert a `pending` row in the
per-user queue DB. The offline worker (`worker/`) picks up pending
rows, runs the agent, and writes problems back. The webapp no longer
touches the agent at all.
"""

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
from common import storage
from webapp.src.web.auth import login_required, upload_allowed_required

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

    with_solution = bool(request.form.get("with_solution"))

    queued = 0
    retried = 0
    already_queued = 0
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
        result = storage.enqueue_raw(safe_name, with_solution=with_solution)
        if result == "new":
            queued += 1
        elif result == "retried":
            retried += 1
        else:
            already_queued += 1

    if queued:
        flash(
            f"Queued {queued} file(s) for offline processing.",
            "success",
        )
    if retried:
        flash(
            f"Re-queued {retried} previously processed/failed file(s) "
            "for another run.",
            "success",
        )
    if already_queued:
        flash(
            f"{already_queued} file(s) were already in flight; skipped.",
            "success",
        )
    return redirect(url_for("pages.index"))


@bp.route("/figures/<path:filename>", methods=["GET"])
@login_required
def serve_figure(filename):
    return send_from_directory(storage.figures_dir(), filename)


@bp.route("/raw/<path:filename>", methods=["GET"])
@login_required
def serve_raw(filename):
    return send_from_directory(storage.raw_uploads_dir(), filename)
