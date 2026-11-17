"""AI-Enhanced Employee Management Application - Flask entry point.

Development : python app.py                         ->  http://127.0.0.1:5000
Production  : gunicorn wsgi:app -k gevent -w 1 ...   (see render.yaml / DEPLOYMENT.md)
"""
import logging

from flask import Flask, jsonify, request, send_from_directory
from sqlalchemy import event, inspect, text
from werkzeug.exceptions import HTTPException
from werkzeug.middleware.proxy_fix import ProxyFix

from config import Config, validate_production
from models import db
from routes import analytics, attendance, auth, employees, notifications, payroll
from services.genai_service import GenAIService
from services.ml_service import AttritionModel

# Columns added after the first release. create_all() creates new tables but never alters existing ones,
# so databases made by an older version are upgraded here (safe to run on every start).
COLUMN_UPGRADES = [
    ("employees", "must_change_password", "BOOLEAN NOT NULL DEFAULT FALSE"),
    ("attendance", "note", "VARCHAR(60)"),
    ("employees", "hr_satisfaction", "FLOAT"),
]

CSP = ("default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; "
       "connect-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'")


def upgrade_schema(app):
    insp = inspect(db.engine)
    for table, column, ddl in COLUMN_UPGRADES:
        if table in insp.get_table_names() and column not in {c["name"] for c in insp.get_columns(table)}:
            with db.engine.begin() as conn:
                conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}"))
            app.logger.warning("Database upgraded: added %s.%s", table, column)
    for table in db.metadata.tables.values():          # indexes added for speed
        for index in table.indexes:
            index.create(db.engine, checkfirst=True)


def tune_sqlite(app):
    """SQLite (development only): WAL journal + relaxed fsync make concurrent reads/writes much faster."""
    if db.engine.dialect.name != "sqlite":
        return

    @event.listens_for(db.engine, "connect")
    def _pragmas(dbapi_conn, _):
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA journal_mode=WAL")
        cur.execute("PRAGMA synchronous=NORMAL")
        cur.execute("PRAGMA foreign_keys=ON")
        cur.close()


def create_app(config_overrides: dict | None = None) -> Flask:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    app = Flask(__name__, static_folder=Config.FRONTEND_DIR, static_url_path="")
    app.config.from_object(Config)
    if config_overrides:
        app.config.update(config_overrides)
    app.json.sort_keys = False  # keep API field order (e.g. report sections)

    if app.config["IS_PROD"]:
        validate_production(app.config)
    elif not app.config["SECRET_KEY"]:
        app.config["SECRET_KEY"] = "dev-only-change-me"       # development convenience only
    if app.config["TRUST_PROXY"]:
        # Render/Netlify terminate HTTPS and forward the real client IP + scheme in X-Forwarded-* headers
        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)

    db.init_app(app)
    app.extensions["attrition_model"] = AttritionModel(app.config["MODEL_PATH"], app.config["MODEL_META_PATH"])
    app.extensions["genai"] = GenAIService(app.config)

    for module in (auth, employees, attendance, payroll, analytics, notifications):
        app.register_blueprint(module.bp)

    @app.get("/")
    def index():
        return send_from_directory(app.static_folder, "login.html")

    @app.get("/api/health")
    def health():
        """Used by the host's health check (and any uptime monitor). 503 if the database is unreachable."""
        try:
            db.session.execute(text("SELECT 1"))
            db_ok = True
        except Exception:  # noqa: BLE001
            db.session.rollback()
            db_ok = False
        body = {"status": "ok" if db_ok else "degraded", "database": db_ok,
                "model_ready": app.extensions["attrition_model"].ready,
                "genai_configured": app.extensions["genai"].configured,
                "google_signin": bool(app.config["GOOGLE_CLIENT_ID"] and app.config["GOOGLE_CLIENT_SECRET"])}
        return jsonify(body), (200 if db_ok else 503)

    @app.before_request
    def csrf_guard():
        """State-changing API calls must carry a custom header. A forged cross-site form or <img> request cannot
        add it (browsers block custom headers cross-origin without CORS approval), which defeats CSRF in addition
        to the SameSite=Lax session cookie."""
        if request.path.startswith("/api/") and request.method in ("POST", "PUT", "PATCH", "DELETE"):
            if request.headers.get("X-Requested-With") != "XMLHttpRequest":
                return jsonify({"error": "Missing X-Requested-With header"}), 403

    @app.after_request
    def security_headers(resp):
        resp.headers.setdefault("X-Content-Type-Options", "nosniff")
        resp.headers.setdefault("X-Frame-Options", "DENY")
        resp.headers.setdefault("Referrer-Policy", "same-origin")
        resp.headers.setdefault("Permissions-Policy", "geolocation=(), camera=(), microphone=()")
        if not request.path.startswith("/api/"):
            resp.headers.setdefault("Content-Security-Policy", CSP)
        if app.config["IS_PROD"]:
            resp.headers.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
        if request.path.startswith("/api/"):
            resp.headers["Cache-Control"] = "no-store"
        return resp

    @app.errorhandler(Exception)
    def handle_error(exc):
        if isinstance(exc, HTTPException):
            if request.path.startswith("/api/"):
                return jsonify({"error": exc.description}), exc.code
            return exc
        app.logger.exception("Unhandled error")
        return jsonify({"error": "Internal server error"}), 500

    with app.app_context():
        tune_sqlite(app)
        db.create_all()
        upgrade_schema(app)
        from seed import ensure_seed
        ensure_seed(app)
    return app


if __name__ == "__main__":
    # threaded=True so the live-notification stream (one open request per browser tab) does not block other requests
    create_app().run(host="127.0.0.1", port=5000, debug=False, threaded=True)
