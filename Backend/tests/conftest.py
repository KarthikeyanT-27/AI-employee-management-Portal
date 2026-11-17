import os
import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("APP_TIMEZONE", "Asia/Kolkata")

ADMIN_EMAIL = "karthiksai9865@gmail.com"
ADMIN_PW = "Start#Admin2026"


class Client:
    """Test client that always sends the CSRF header like the real frontend does."""
    def __init__(self, app):
        self.c = app.test_client()

    def _req(self, method, url, json=None, **kw):
        headers = {"X-Requested-With": "XMLHttpRequest", **kw.pop("headers", {})}
        return getattr(self.c, method)(url, json=json, headers=headers, **kw)

    def get(self, url, **kw): return self._req("get", url, **kw)
    def post(self, url, json=None, **kw): return self._req("post", url, json=json, **kw)
    def put(self, url, json=None, **kw): return self._req("put", url, json=json, **kw)
    def delete(self, url, **kw): return self._req("delete", url, **kw)

    def login(self, email, password, portal="hr"):
        return self.post("/api/auth/login", {"email": email, "password": password, "portal": portal})


@pytest.fixture()
def app():
    from app import create_app
    from models import db
    from services.notifications import broker
    broker._subs.clear()                      # isolate live-notification subscribers between tests
    path = tempfile.mktemp(suffix=".db")
    # TEST_DATABASE_URL=postgresql+psycopg2://... runs the whole suite on PostgreSQL (what production uses)
    uri = os.getenv("TEST_DATABASE_URL") or f"sqlite:///{path}"
    if os.getenv("TEST_DATABASE_URL"):
        from sqlalchemy import create_engine
        eng = create_engine(uri)
        from sqlalchemy import text as _t
        with eng.begin() as conn:
            conn.execute(_t("DROP SCHEMA public CASCADE")); conn.execute(_t("CREATE SCHEMA public"))
        eng.dispose()
    application = create_app({"SQLALCHEMY_DATABASE_URI": uri, "TESTING": True,
                              "ADMIN_EMAIL": ADMIN_EMAIL, "ADMIN_INITIAL_PASSWORD": ADMIN_PW,
                              "SEED_DEMO_DATA": False, "SECRET_KEY": "x" * 40})
    yield application
    with application.app_context():
        db.session.remove()
        db.engine.dispose()
    try:
        os.remove(path)
    except OSError:
        pass


@pytest.fixture()
def admin(app):
    """Admin client, logged in and with the forced first password change already done."""
    c = Client(app)
    assert c.login(ADMIN_EMAIL, ADMIN_PW).status_code == 200
    r = c.post("/api/auth/change-password", {"current_password": ADMIN_PW, "new_password": "Changed#Admin1"})
    assert r.status_code == 200, r.json
    return c
