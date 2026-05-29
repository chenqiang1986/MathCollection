import os

from dotenv import load_dotenv
from flask import Flask, url_for
from werkzeug.middleware.proxy_fix import ProxyFix

from webapp.src.web import auth, routes_api, routes_pages, uploads

load_dotenv()

MAX_UPLOAD_BYTES = 10 * 1024 * 1024  # 10 MB


class ForwardedPrefixMiddleware:
    """Promote X-Forwarded-Prefix into SCRIPT_NAME so url_for() emits
    upstream-correct URLs when the app is mounted under a path by a reverse
    proxy (e.g. https://domain/math)."""

    def __init__(self, app):
        self.app = app

    def __call__(self, environ, start_response):
        prefix = environ.get("HTTP_X_FORWARDED_PREFIX", "").strip()
        if prefix:
            prefix = "/" + prefix.strip("/")
            environ["SCRIPT_NAME"] = prefix
            path = environ.get("PATH_INFO", "")
            if path.startswith(prefix):
                environ["PATH_INFO"] = path[len(prefix):] or "/"
        return self.app(environ, start_response)


def create_app() -> Flask:
    app = Flask(__name__)
    app.config["SECRET_KEY"] = os.environ.get("FLASK_SECRET_KEY", "dev-secret")
    app.config["MAX_CONTENT_LENGTH"] = MAX_UPLOAD_BYTES

    app.wsgi_app = ForwardedPrefixMiddleware(
        ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)
    )

    @app.context_processor
    def inject_static_url():
        """`static_url('js/app.js')` is `url_for('static', ...)` plus a `v=`
        query of the file's mtime. The URL changes whenever the asset does,
        so browsers and the Cloudflare edge fetch the new file immediately
        instead of serving a stale copy from their cache TTL."""
        def static_url(filename: str) -> str:
            try:
                version = int(os.stat(os.path.join(app.static_folder, filename)).st_mtime)
            except OSError:
                version = 0
            return url_for("static", filename=filename, v=version)
        return {"static_url": static_url}

    auth.init_auth(app)
    app.register_blueprint(auth.bp)
    app.register_blueprint(routes_pages.bp)
    app.register_blueprint(routes_api.bp)
    app.register_blueprint(uploads.bp)

    return app


app = create_app()


if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=5000)
