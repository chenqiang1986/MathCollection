import os
import uuid
from pathlib import Path

from dotenv import load_dotenv
from flask import Flask, flash, redirect, render_template, request, url_for
from werkzeug.utils import secure_filename

import agent_self_planning as agent
import storage

load_dotenv()

ALLOWED_EXTENSIONS = {"png", "jpg", "jpeg", "gif", "webp"}
MAX_UPLOAD_BYTES = 10 * 1024 * 1024  # 10 MB

UPLOAD_DIR = Path(__file__).resolve().parent.parent / "uploads"


def create_app() -> Flask:
    app = Flask(__name__)
    app.config["SECRET_KEY"] = os.environ.get("FLASK_SECRET_KEY", "dev-secret")
    app.config["MAX_CONTENT_LENGTH"] = MAX_UPLOAD_BYTES
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

    @app.route("/", methods=["GET"])
    def index():
        return render_template(
            "index.html",
            problems=storage.list_problems(),
            result=None,
        )

    @app.route("/upload", methods=["POST"])
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

        return render_template(
            "index.html",
            problems=storage.list_problems(),
            result=result,
        )

    return app


app = create_app()


if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=5001)
