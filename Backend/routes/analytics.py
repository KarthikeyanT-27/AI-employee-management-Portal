import csv
import io
import json
from collections import Counter, defaultdict
from datetime import date

from flask import Blueprint, Response, current_app, jsonify, request
from sqlalchemy import func
from sqlalchemy.orm import joinedload

from models import Attendance, AttendanceCorrection, AttritionPrediction, Department, Employee, LeaveRequest, Payroll, db
from timeutil import today_local
from services.features import (bulk_attendance_stats, bulk_ml_features, bulk_overtime_monthly, genai_context,
                               ml_features)
from services.genai_service import LLMError
from services.notifications import notify_staff
from utils import err, hr_required, login_required

bp = Blueprint("analytics", __name__, url_prefix="/api")


def _active():
    return (Employee.query.options(joinedload(Employee.role), joinedload(Employee.department))
            .filter_by(status="active", account_type="employee").all())


def latest_predictions() -> dict:
    """Latest prediction per employee using ONE query (max id per employee), not a scan of all history."""
    newest = db.session.query(func.max(AttritionPrediction.id)).group_by(AttritionPrediction.employee_id).subquery()
    rows = (AttritionPrediction.query
            .options(joinedload(AttritionPrediction.employee).joinedload(Employee.department),
                     joinedload(AttritionPrediction.employee).joinedload(Employee.role))
            .filter(AttritionPrediction.id.in_(db.session.query(newest.c[0]))).all())
    return {p.employee_id: p for p in rows}


def _predict_and_store_many(employees):
    """Compute features for all employees with grouped queries, score them with ONE model call, store results."""
    ml = current_app.extensions["attrition_model"]
    feats = bulk_ml_features(employees)
    results = ml.predict_many([feats[e.id] for e in employees])
    rows = [AttritionPrediction(employee_id=e.id, risk_label=r["risk_label"],
                                probabilities=json.dumps(r["probabilities"]), features=json.dumps(feats[e.id]))
            for e, r in zip(employees, results)]
    db.session.add_all(rows)
    return rows


# --------------------------------------------------------------------- reports
def build_summary() -> dict:
    emps = _active()
    by_dept = Counter(e.department.name if e.department else "Unassigned" for e in emps)
    att = bulk_attendance_stats(emps, 60)
    ots = bulk_overtime_monthly(emps)
    atts = [att[e.id]["attendance_pct"] for e in emps]
    preds = latest_predictions()
    dist = Counter(preds[e.id].risk_label for e in emps if e.id in preds)
    month = today_local().strftime("%Y-%m")
    pay_n, pay_net, pay_ot = db.session.query(func.count(Payroll.id), func.coalesce(func.sum(Payroll.net_salary), 0),
                                              func.coalesce(func.sum(Payroll.overtime_pay), 0)).filter(Payroll.month == month).one()
    n = len(emps)
    return {
        "active_employees": n,
        "hr_users": Employee.query.filter(Employee.account_type.in_(["hr", "admin"]), Employee.status == "active").count(),
        "by_department": dict(by_dept),
        "avg_attendance_pct": round(sum(atts) / n, 1) if n else 0,
        "avg_overtime_hours": round(sum(ots.values()) / n, 1) if n else 0,
        "avg_job_satisfaction": round(sum(e.job_satisfaction for e in emps) / n, 1) if n else 0,
        "avg_performance_rating": round(sum(e.performance_rating for e in emps) / n, 2) if n else 0,
        "pending_leaves": LeaveRequest.query.filter_by(status="Pending").count(),
        "pending_corrections": AttendanceCorrection.query.filter_by(status="Pending").count(),
        "attrition_risk_distribution": {k: dist.get(k, 0) for k in ("Low", "Moderate", "High")},
        "predictions_available": len(preds),
        "payroll_month": month, "payroll_records": pay_n,
        "payroll_total_net_inr": round(float(pay_net), 2),
        "payroll_total_overtime_inr": round(float(pay_ot), 2),
    }


@bp.get("/reports/summary")
@hr_required
def summary(user):
    return jsonify(build_summary())


def _safe(v):
    """Neutralise spreadsheet formula injection in exported cells."""
    s = "" if v is None else str(v)
    return "'" + s if s[:1] in ("=", "+", "-", "@") else s


@bp.get("/reports/export/<kind>")
@hr_required
def export(user, kind):
    buf = io.StringIO()
    w = csv.writer(buf)
    if kind == "employees":
        w.writerow(["emp_code", "name", "email", "department", "role", "join_date", "salary", "status"])
        for e in Employee.query.options(joinedload(Employee.department), joinedload(Employee.role)).order_by(Employee.name):
            w.writerow([_safe(x) for x in (e.emp_code, e.name, e.email, e.department.name if e.department else "",
                                           e.role.title if e.role else "", e.join_date, e.salary, e.status)])
    elif kind == "attendance":
        w.writerow(["date", "employee", "status", "check_in", "check_out", "overtime_hours"])
        for r in Attendance.query.options(joinedload(Attendance.employee)).order_by(Attendance.date.desc()).limit(5000):
            d = r.to_dict()
            w.writerow([_safe(d[k]) for k in ("date", "employee", "status", "check_in", "check_out", "overtime_hours")])
    elif kind == "payroll":
        month = request.args.get("month")
        w.writerow(["month", "employee", "base_salary", "paid_days", "lop_deduction", "overtime_pay",
                    "bonus", "pf", "tax", "net_salary"])
        q = Payroll.query.options(joinedload(Payroll.employee))
        q = q.filter_by(month=month) if month else q
        for p in q.order_by(Payroll.month.desc()):
            d = p.to_dict()
            w.writerow([_safe(d[k]) for k in ("month", "employee", "base_salary", "paid_days", "lop_deduction",
                                              "overtime_pay", "bonus", "pf", "tax", "net_salary")])
    elif kind == "attrition":
        w.writerow(["employee", "department", "risk", "p_low", "p_moderate", "p_high"])
        for p in latest_predictions().values():
            d = p.to_dict()
            pr = d["probabilities"]
            w.writerow([_safe(d["employee"]), _safe(d["department"]), d["risk_label"],
                        pr.get("Low"), pr.get("Moderate"), pr.get("High")])
    else:
        return err("Unknown report", 404)
    return Response(buf.getvalue(), mimetype="text/csv",
                    headers={"Content-Disposition": f"attachment; filename={kind}_report.csv"})


# ------------------------------------------------------------------------- ML
@bp.get("/ml/model-info")
@hr_required
def model_info(user):
    return jsonify(current_app.extensions["attrition_model"].info())


@bp.post("/ml/predict/<int:emp_id>")
@hr_required
def predict_one(user, emp_id):
    emp = db.session.get(Employee, emp_id)
    if not emp:
        return err("Employee not found", 404)
    ml = current_app.extensions["attrition_model"]
    if not ml.ready:
        return err(ml.error, 503)
    row = _predict_and_store_many([emp])[0]
    db.session.commit()
    return jsonify({"prediction": row.to_dict()})


@bp.post("/ml/predict-all")
@hr_required
def predict_all(user):
    ml = current_app.extensions["attrition_model"]
    if not ml.ready:
        return err(ml.error, 503)
    rows = _predict_and_store_many(_active())
    counts = Counter(r.risk_label for r in rows)
    notify_staff("attrition", "Attrition predictions updated",
                 f"{len(rows)} employees scored: {counts.get('High', 0)} high, {counts.get('Moderate', 0)} moderate, "
                 f"{counts.get('Low', 0)} low risk.", "attrition", exclude=user.id)
    db.session.commit()
    return jsonify({"count": len(rows)})


@bp.get("/ml/predictions")
@hr_required
def predictions(user):
    items = [p.to_dict() for p in latest_predictions().values() if p.employee and p.employee.status == "active"]
    order = {"High": 0, "Moderate": 1, "Low": 2}
    items.sort(key=lambda x: (order[x["risk_label"]], -x["probabilities"].get("High", 0)))
    return jsonify({"predictions": items})


# ---------------------------------------------------------------------- GenAI
def _genai_failure(exc):
    return err(f"Generative AI request failed: {exc}", 502)


@bp.post("/genai/employee/<int:emp_id>")
@hr_required
def genai_employee(user, emp_id):
    emp = db.session.get(Employee, emp_id)
    if not emp:
        return err("Employee not found", 404)
    sections = (request.get_json(silent=True) or {}).get("sections") or []
    pred = latest_predictions().get(emp.id)
    risk = {"risk_label": pred.risk_label, "probabilities": json.loads(pred.probabilities)} if pred else None
    try:
        out = current_app.extensions["genai"].employee_report(genai_context(emp), risk, sections)
    except LLMError as exc:
        return _genai_failure(exc)
    return jsonify({"employee": emp.to_dict(include_private=False), "risk": risk, **out})


@bp.get("/genai/me")
@login_required
def genai_me(user):
    """Employees can read their own generated performance summary and attendance analysis (no attrition data)."""
    try:
        out = current_app.extensions["genai"].employee_report(
            genai_context(user), None, ["performance_summary", "attendance_analysis"])
    except LLMError as exc:
        return _genai_failure(exc)
    return jsonify(out)


@bp.post("/genai/hr-insights")
@hr_required
def genai_org(user):
    try:
        out = current_app.extensions["genai"].org_insights(build_summary())
    except LLMError as exc:
        return _genai_failure(exc)
    return jsonify(out)


@bp.get("/genai/status")
@hr_required
def genai_status(user):
    g = current_app.extensions["genai"]
    return jsonify({"configured": g.configured, "provider": g.provider, "model": g.model if g.configured else None})
