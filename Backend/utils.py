"""Shared helpers: auth decorators, password policy, login throttling, validation, parsing."""
import random
import re
from datetime import date, datetime, timedelta
from functools import wraps

from flask import jsonify, request, session
from sqlalchemy.exc import IntegrityError

from models import Employee, LoginAttempt, db
from timeutil import utcnow

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

# Endpoints a user may call while they still must change a temporary password
PASSWORD_CHANGE_ALLOWED = {"auth.me", "auth.logout", "auth.change_password"}


def err(message: str, status: int = 400, **extra):
    return jsonify({"error": message, **extra}), status


# ------------------------------------------------------------------ sessions
def current_user() -> Employee | None:
    uid = session.get("uid")
    if not uid:
        return None
    user = db.session.get(Employee, uid)
    return user if user and user.status == "active" else None


def _gate(user, need=None):
    """Return an error response if the user may not proceed, else None."""
    if not user:
        return err("Authentication required", 401)
    if user.must_change_password and request.endpoint not in PASSWORD_CHANGE_ALLOWED:
        return err("You must change your temporary password first.", 403, code="PASSWORD_CHANGE_REQUIRED")
    if need == "staff" and not user.is_staff:
        return err("HR/Admin access required", 403)
    if need == "admin" and user.account_type != "admin":
        return err("Administrator access required", 403)
    return None


def _decorator(need):
    def deco(fn):
        @wraps(fn)
        def wrapper(*a, **kw):
            user = current_user()
            blocked = _gate(user, need)
            return blocked if blocked else fn(user, *a, **kw)
        return wrapper
    return deco


login_required = _decorator(None)
hr_required = _decorator("staff")      # HR *or* admin
admin_required = _decorator("admin")   # administrators only


# ------------------------------------------------------------ password policy
def check_password_strength(pw: str, email: str = "") -> str | None:
    """Returns an error message, or None if the password is acceptable."""
    if len(pw or "") < 8:
        return "Password must be at least 8 characters"
    if not re.search(r"[A-Za-z]", pw) or not re.search(r"\d", pw):
        return "Password must contain at least one letter and one number"
    if email and pw.lower() == email.lower():
        return "Password must not be the same as the email"
    return None


# ------------------------------------------------------------ login throttling
class LoginThrottle:
    """Brute-force protection stored in the database: 5 failures within 15 min lock that email+IP for 15 min.
    Stored (not in memory) so it survives restarts and the free host's idle sleep."""
    MAX_FAILS, WINDOW = 5, 15 * 60

    @staticmethod
    def key(email: str):
        return (email.lower()[:120], (request.remote_addr or "?")[:64])

    def _row(self, email: str):
        e, ip = self.key(email)
        return LoginAttempt.query.filter_by(email=e, ip=ip).first()

    def locked_for(self, email: str) -> int:
        row = self._row(email)
        if row and row.locked_until and row.locked_until > utcnow():
            return int((row.locked_until - utcnow()).total_seconds())
        return 0

    def fail(self, email: str):
        now = utcnow()
        e, ip = self.key(email)
        row = self._row(email)
        if not row:
            row = LoginAttempt(email=e, ip=ip, fail_count=0, window_start=now)
            db.session.add(row)
        if (now - row.window_start).total_seconds() > self.WINDOW:
            row.fail_count, row.window_start = 0, now
        row.fail_count += 1
        if row.fail_count >= self.MAX_FAILS:
            row.locked_until = now + timedelta(seconds=self.WINDOW)
            row.fail_count, row.window_start = 0, now
        try:
            db.session.commit()
        except IntegrityError:          # two simultaneous failures created the row: the other one already counted
            db.session.rollback()
        if random.random() < 0.02:      # keep the table small
            LoginAttempt.query.filter(LoginAttempt.window_start < now - timedelta(days=1),
                                      (LoginAttempt.locked_until.is_(None)) | (LoginAttempt.locked_until < now)) \
                .delete(synchronize_session=False)
            db.session.commit()

    def ok(self, email: str):
        e, ip = self.key(email)
        LoginAttempt.query.filter_by(email=e, ip=ip).delete(synchronize_session=False)
        db.session.commit()

    def reset_all(self):
        LoginAttempt.query.delete()
        db.session.commit()


throttle = LoginThrottle()


# ---------------------------------------------------------------- validation
def parse_date(value, field="date") -> date:
    try:
        return datetime.strptime(str(value), "%Y-%m-%d").date()
    except (ValueError, TypeError):
        raise ValueError(f"{field} must be in YYYY-MM-DD format")


def parse_time(value, field="time"):
    try:
        return datetime.strptime(str(value), "%H:%M").time()
    except (ValueError, TypeError):
        raise ValueError(f"{field} must be in HH:MM format")


def num(value, field, lo=None, hi=None, default=None):
    if value in (None, ""):
        if default is not None:
            return default
        raise ValueError(f"{field} is required")
    try:
        x = float(value)
    except (TypeError, ValueError):
        raise ValueError(f"{field} must be a number")
    if (lo is not None and x < lo) or (hi is not None and x > hi):
        raise ValueError(f"{field} must be between {lo} and {hi}")
    return x


def next_emp_code() -> str:
    last = Employee.query.order_by(Employee.id.desc()).first()
    n = (last.id if last else 0) + 1
    while Employee.query.filter_by(emp_code=f"EMP{n:04d}").first():
        n += 1
    return f"EMP{n:04d}"
