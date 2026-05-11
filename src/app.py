import os
from pathlib import Path

from dotenv import load_dotenv
from flask import Flask

from web import auth, routes_api, routes_pages, uploads

load_dotenv()

MAX_UPLOAD_BYTES = 10 * 1024 * 1024  # 10 MB
UPLOAD_DIR = Path(__file__).resolve().parent.parent / "uploads"


def create_app() -> Flask:
    app = Flask(__name__)
    app.config["SECRET_KEY"] = os.environ.get("FLASK_SECRET_KEY", "dev-secret")
    app.config["MAX_CONTENT_LENGTH"] = MAX_UPLOAD_BYTES
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

    auth.init_auth(app)
    app.register_blueprint(auth.bp)
    app.register_blueprint(routes_pages.bp)
    app.register_blueprint(routes_api.bp)
    app.register_blueprint(uploads.bp)

    return app


app = create_app()


if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=5001)
