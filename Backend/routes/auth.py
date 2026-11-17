"""Authentication. There is NO public registration: accounts are created by HR (employees)
or by an administrator (HR / admin accounts) from the dashboard."""
import secrets
from urllib.parse import urlencode

import requests
from flask import Blueprint, current_app, jsonify, redirect, request, session

from models import db
from utils import (check_password_strength, current_user, err, login_required, throttle)

bp = Blueprint("auth", __name__, url_prefix="/api/auth")

GENERIC_FAIL = "Invalid email or password"


@bp.post("/login")
def login():
    from models import Employee
    d = request.get_json(silent=True) or {}
    email, pw = (d.get("email") or "").strip().lower(), d.get("password") or ""
    portal = d.get("portal", "employee")  # employee | hr  (hr portal = HR + admin staff)
    wait = throttle.locked_for(email)
    if wait:
        return err(f"Too many failed attempts. Try again in {max(1, wait // 60)} minute(s).", 429)
    emp = Employee.query.filter_by(email=email).first()
    if not emp or not emp.check_password(pw):
        throttle.fail(email)
        return err(GENERIC_FAIL, 401)
    if emp.status != "active":
        return err("This account is inactive. Contact HR.", 403)
    if portal == "hr" and not emp.is_staff:
        return err("This account does not have HR/Admin access", 403)
    if portal != "hr" and emp.is_staff:
        return err("Please use the HR / Admin tab to sign in", 403)
    throttle.ok(email)
    session.clear()
    session["uid"] = emp.id
    session.permanent = True
    return jsonify({"message": "Logged in", "user": emp.to_dict()})


@bp.post("/logout")
def logout():
    session.clear()
    return jsonify({"message": "Logged out"})


def _login_error(message: str):
    """The Google flow is a full-page redirect, so failures go back to the login page with a readable message."""
    return redirect("/login.html?" + urlencode({"error": message}))


@bp.get("/google")
def google_start():
    if not (current_app.config["GOOGLE_CLIENT_ID"] and current_app.config["GOOGLE_CLIENT_SECRET"]):
        return _login_error("Google sign-in is not enabled on this server. Use your email and password.")
    state = secrets.token_urlsafe(32)
    session["google_oauth_state"] = state
    params = urlencode({
        "client_id": current_app.config["GOOGLE_CLIENT_ID"],
        "redirect_uri": current_app.config["GOOGLE_REDIRECT_URI"],
        "response_type": "code",
        "scope": "openid email profile",
        "state": state,
        "access_type": "online",
        "prompt": "select_account",
    })
    return redirect(f"https://accounts.google.com/o/oauth2/v2/auth?{params}")


@bp.get("/google/callback")
def google_callback():
    from models import Employee
    if request.args.get("state") != session.pop("google_oauth_state", None):
        return _login_error("Google sign-in expired. Please try again.")
    code = request.args.get("code")
    if not code:
        return _login_error("Google sign-in was cancelled.")
    try:
        token = requests.post("https://oauth2.googleapis.com/token", data={
            "code": code, "client_id": current_app.config["GOOGLE_CLIENT_ID"],
            "client_secret": current_app.config["GOOGLE_CLIENT_SECRET"],
            "redirect_uri": current_app.config["GOOGLE_REDIRECT_URI"],
            "grant_type": "authorization_code",
        }, timeout=15)
        token.raise_for_status()
        access_token = token.json()["access_token"]
        info = requests.get("https://openidconnect.googleapis.com/v1/userinfo",
                            headers={"Authorization": f"Bearer {access_token}"}, timeout=15)
        info.raise_for_status()
        profile = info.json()
    except (requests.RequestException, KeyError) as exc:
        current_app.logger.error("Google sign-in failed: %s", exc)
        return _login_error("Google sign-in failed. Please try again.")
    if not profile.get("email_verified") or not profile.get("email"):
        return _login_error("A verified Google email is required.")
    emp = Employee.query.filter_by(email=profile["email"].strip().lower(), account_type="employee").first()
    if not emp or emp.status != "active":
        return _login_error("No active employee account matches this Google email. Contact HR.")
    session.clear()
    session["uid"] = emp.id
    session.permanent = True
    return redirect("/employee.html")


@bp.get("/me")
@login_required
def me(user):
    return jsonify({"user": user.to_dict()})


@bp.post("/change-password")
@login_required
def change_password(user):
    """Used for the mandatory first-login change and for normal password changes."""
    d = request.get_json(silent=True) or {}
    current, new = d.get("current_password") or "", d.get("new_password") or ""
    if not user.check_password(current):
        return err("Current password is incorrect", 403)
    if current == new:
        return err("New password must be different from the current one")
    problem = check_password_strength(new, user.email)
    if problem:
        return err(problem)
    user.set_password(new)
    user.must_change_password = False
    db.session.commit()
    return jsonify({"message": "Password updated", "user": user.to_dict()})
