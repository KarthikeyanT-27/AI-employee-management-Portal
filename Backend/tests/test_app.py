import json
import queue
from datetime import timedelta

import pytest

from .conftest import ADMIN_EMAIL, ADMIN_PW, Client


def make_user(admin, name, email, account_type="employee", password="Temp#Pass123", **extra):
    r = admin.post("/api/employees", {"name": name, "email": email, "account_type": account_type,
                                      "password": password, **extra})
    assert r.status_code == 201, r.json
    return r.json["employee"]


def login_changed(app, email, temp, new, portal):
    c = Client(app)
    assert c.login(email, temp, portal).status_code == 200
    assert c.post("/api/auth/change-password", {"current_password": temp, "new_password": new}).status_code == 200
    return c


# ----------------------------------------------------------------- production hygiene
def test_health_and_no_demo_data(app):
    c = Client(app)
    h = c.get("/api/health")
    assert h.status_code == 200 and h.json["database"] is True and h.json["model_ready"] is True
    # old demo credentials must not exist
    assert c.login("admin@company.com", "Admin@12345").status_code == 401
    assert c.login("hr@company.com", "Hr@123456").status_code == 401
    from models import Employee
    with app.app_context():
        assert Employee.query.count() == 1                       # only the bootstrapped admin
        assert Employee.query.first().email == ADMIN_EMAIL


def test_admin_bootstrap_forces_password_change(app):
    c = Client(app)
    r = c.login(ADMIN_EMAIL, ADMIN_PW)
    assert r.status_code == 200 and r.json["user"]["must_change_password"] is True
    blocked = c.get("/api/employees")
    assert blocked.status_code == 403 and blocked.json["code"] == "PASSWORD_CHANGE_REQUIRED"
    assert c.post("/api/auth/change-password", {"current_password": ADMIN_PW, "new_password": "Changed#Admin1"}).status_code == 200
    assert c.get("/api/employees").status_code == 200
    assert c.login(ADMIN_EMAIL, ADMIN_PW).status_code == 401      # old password is dead


def test_random_admin_password_when_none_supplied(tmp_path):
    from app import create_app
    from models import Employee
    app = create_app({"SQLALCHEMY_DATABASE_URI": f"sqlite:///{tmp_path/'x.db'}", "TESTING": True,
                      "ADMIN_EMAIL": "a@b.co", "ADMIN_INITIAL_PASSWORD": "", "SECRET_KEY": "x" * 40})
    with app.app_context():
        assert Employee.query.filter_by(email="a@b.co", account_type="admin").one().must_change_password


def test_csrf_header_required(app):
    raw = app.test_client()
    assert raw.post("/api/auth/login", json={"email": "a", "password": "b"}).status_code == 403


def test_production_config_rejects_unsafe_settings(tmp_path):
    from app import create_app
    with pytest.raises(RuntimeError) as e:
        create_app({"IS_PROD": True, "SECRET_KEY": "short", "SEED_DEMO_DATA": True,
                    "SQLALCHEMY_DATABASE_URI": f"sqlite:///{tmp_path/'p.db'}"})
    msg = str(e.value)
    assert "SECRET_KEY" in msg and "SEED_DEMO_DATA" in msg and "PostgreSQL" in msg


def test_security_headers_and_csp(app):
    r = Client(app).get("/login.html")
    assert "script-src 'self'" in r.headers["Content-Security-Policy"]
    assert r.headers["X-Frame-Options"] == "DENY"


def test_lockout_is_persistent(app):
    c = Client(app)
    for _ in range(5):
        assert c.login("nobody@x.com", "Wrong#Pass1").status_code == 401
    assert c.login("nobody@x.com", "Wrong#Pass1").status_code == 429
    from models import LoginAttempt
    with app.app_context():
        assert LoginAttempt.query.filter_by(email="nobody@x.com").one().locked_until is not None


def test_timezone_helper_uses_ist():
    from timeutil import now_local, utcnow
    delta = now_local() - utcnow()
    assert timedelta(hours=5, minutes=25) < delta < timedelta(hours=5, minutes=35)


# -------------------------------------------------------------- roles & permissions
def test_only_admin_creates_hr_and_employee_cannot_reach_hr_api(app, admin):
    make_user(admin, "Hema HR", "hema@corp.test", "hr")
    hr = login_changed(app, "hema@corp.test", "Temp#Pass123", "Hema#Pass456", "hr")
    r = hr.post("/api/employees", {"name": "X Admin", "email": "x@corp.test", "account_type": "admin", "password": "Temp#Pass123"})
    assert r.status_code == 403
    make_user(hr, "Ravi Emp", "ravi@corp.test")
    emp = login_changed(app, "ravi@corp.test", "Temp#Pass123", "Ravi#Pass789", "employee")
    assert emp.get("/api/employees").status_code == 403
    assert emp.get("/api/payroll").status_code == 403
    assert emp.get("/api/ml/predictions").status_code == 403


# ------------------------------------------------------- realtime notifications
def _drain(q):
    out = []
    while True:
        try:
            out.append(q.get_nowait())
        except queue.Empty:
            return out


def test_leave_flow_notifies_both_sides_live_and_stored(app, admin):
    from services.notifications import broker
    hr_rec = make_user(admin, "Hema HR", "hema@corp.test", "hr")
    emp_rec = make_user(admin, "Ravi Emp", "ravi@corp.test")
    emp = login_changed(app, "ravi@corp.test", "Temp#Pass123", "Ravi#Pass789", "employee")

    admin_q, hr_q, emp_q = (broker.subscribe(i) for i in (1, hr_rec["id"], emp_rec["id"]))
    from datetime import date
    start = (date.today() + timedelta(days=3)).isoformat()
    r = emp.post("/api/leaves", {"start_date": start, "end_date": start, "leave_type": "Casual", "reason": "trip"})
    assert r.status_code == 201
    leave_id = r.json["leave"]["id"]

    # live delivery to every HR/admin user, and NOT to the requester
    for q in (admin_q, hr_q):
        evs = [e for e in _drain(q) if e["kind"] == "leave"]
        assert len(evs) == 1 and "Ravi Emp" in evs[0]["message"] and evs[0]["link"] == "leaves" and not evs[0]["silent"]
    assert not [e for e in _drain(emp_q) if e["kind"] == "leave"]

    # stored: HR sees it after a "reload"
    hr = Client(app)
    hr.login("hema@corp.test", "Temp#Pass123", "hr")
    hr.post("/api/auth/change-password", {"current_password": "Temp#Pass123", "new_password": "Hema#Pass456"})
    n = hr.get("/api/notifications").json
    assert n["unread"] == 1 and n["notifications"][0]["title"] == "New leave request"

    # HR decides -> employee gets a live + stored notification
    assert hr.put(f"/api/leaves/{leave_id}/decision", {"decision": "Approved"}).status_code == 200
    evs = [e for e in _drain(emp_q) if e["kind"] == "leave"]
    assert len(evs) == 1 and "approved" in evs[0]["message"]
    mine = emp.get("/api/notifications").json
    assert mine["unread"] == 1 and mine["notifications"][0]["link"] == "leave"

    # read state
    nid = mine["notifications"][0]["id"]
    assert emp.post(f"/api/notifications/{nid}/read").status_code == 200
    assert emp.get("/api/notifications").json["unread"] == 0
    # another user cannot read/mark someone else's notification
    assert hr.post(f"/api/notifications/{nid}/read").status_code == 404


def test_checkin_sends_silent_refresh_only(app, admin):
    from services.notifications import broker
    make_user(admin, "Ravi Emp", "ravi@corp.test")
    emp = login_changed(app, "ravi@corp.test", "Temp#Pass123", "Ravi#Pass789", "employee")
    q = broker.subscribe(1)
    from timeutil import now_local
    if now_local().weekday() < 7:
        assert emp.post("/api/attendance/check-in").status_code in (201, 409)
    evs = _drain(q)
    assert evs and all(e["silent"] and e["id"] is None for e in evs)
    assert admin.get("/api/notifications").json["unread"] == 0     # nothing added to the bell


def test_rolled_back_change_sends_nothing(app):
    from models import db
    from services.notifications import broker, notify_users
    q = broker.subscribe(1)
    with app.app_context():
        notify_users([1], "system", "Should not arrive", "rolled back")
        db.session.rollback()
        db.session.commit()
    assert _drain(q) == []


def test_sse_stream_endpoint_delivers_event(app, admin):
    from services.notifications import broker, notify_users
    from models import db
    make_user(admin, "Hema HR", "hema@corp.test", "hr")
    resp = admin.get("/api/notifications/stream")
    assert resp.status_code == 200 and resp.mimetype == "text/event-stream"
    chunks = iter(resp.response)
    assert b"event: ready" in next(chunks)
    with app.app_context():
        notify_users([1], "system", "Hello", "live push")
        db.session.commit()
    chunk = next(chunks).decode()
    assert chunk.startswith("event: notification") and "live push" in chunk
    payload = json.loads(chunk.split("data: ", 1)[1])
    assert payload["title"] == "Hello" and payload["is_read"] is False
    resp.close()
    assert broker.total() == 0          # subscription cleaned up on disconnect


def test_payroll_and_attrition_notifications(app, admin):
    from services.notifications import broker
    emp_rec = make_user(admin, "Ravi Emp", "ravi@corp.test", salary=40000, role_id=2)
    q_emp = broker.subscribe(emp_rec["id"])
    r = admin.post("/api/payroll/generate", {"month": "2026-10"})
    assert r.status_code == 201 and r.json["generated"] == 1
    evs = _drain(q_emp)
    assert evs and evs[0]["kind"] == "payroll" and "2026-10" in evs[0]["message"] and evs[0]["link"] == "payslips"

    # ML + notification to the other HR users
    make_user(admin, "Hema HR", "hema@corp.test", "hr")
    q_hr = broker.subscribe(3)
    assert admin.post("/api/ml/predict-all").json["count"] == 1
    pred = admin.get("/api/ml/predictions").json["predictions"]
    assert pred and pred[0]["risk_label"] in ("Low", "Moderate", "High")
    assert any(e["kind"] == "attrition" for e in _drain(q_hr))


def test_genai_fallback_without_key(app, admin):
    make_user(admin, "Ravi Emp", "ravi@corp.test", salary=40000)
    r = admin.post("/api/genai/hr-insights")
    assert r.status_code == 200 and r.json["source"] == "local-fallback"


def test_naive_timestamps_are_serialised_as_utc(app, admin):
    make_user(admin, "Ravi Emp", "ravi@corp.test")
    emp = login_changed(app, "ravi@corp.test", "Temp#Pass123", "Ravi#Pass789", "employee")
    emp.post("/api/satisfaction", {"score": 7, "comment": "ok"})
    ts = emp.get("/api/satisfaction").json["surveys"][0]["submitted_at"]
    assert ts.endswith("Z")
