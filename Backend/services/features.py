"""Turns live database records into the six features the ML model expects,
and into compact statistics for the Generative AI prompts.

Performance: the *bulk_* functions compute the numbers for many employees with a handful of
grouped SQL queries (no per-employee queries). The single-employee helpers simply call them."""
from collections import defaultdict
from datetime import date, timedelta

from sqlalchemy import func

from models import Attendance, Employee, LeaveRequest, Payroll, db
from timeutil import today_local

_CHUNK = 500


def weekdays_between(start: date, end: date) -> int:
    """Number of Mon-Fri days in [start, end] inclusive (closed form, O(1))."""
    if end < start:
        return 0
    days = (end - start).days + 1
    full_weeks, extra = divmod(days, 7)
    count = full_weeks * 5
    wd = start.weekday()
    for i in range(extra):
        if (wd + i) % 7 < 5:
            count += 1
    return count


def _chunks(ids):
    ids = list(ids)
    for i in range(0, len(ids), _CHUNK):
        yield ids[i:i + _CHUNK]


# ------------------------------------------------------------------ bulk queries
def bulk_attendance_stats(emps, days: int = 60, today: date | None = None) -> dict:
    """{employee_id: stats} for the last `days` days up to yesterday.
    Approved-leave days are excluded from the denominator; weekdays with no record count as absent."""
    today = today or today_local()
    end = today - timedelta(days=1)
    lo = today - timedelta(days=days)
    counts = defaultdict(lambda: defaultdict(int))
    for chunk in _chunks(e.id for e in emps):
        rows = (db.session.query(Attendance.employee_id, Attendance.status, func.count(Attendance.id))
                .filter(Attendance.employee_id.in_(chunk), Attendance.date >= lo, Attendance.date <= end)
                .group_by(Attendance.employee_id, Attendance.status).all())
        for emp_id, status, n in rows:
            counts[emp_id][status] = n
    out = {}
    for e in emps:
        start = max(lo, e.join_date)
        if end < start:
            out[e.id] = {"window_days": 0, "expected": 0, "present": 0, "late": 0, "leave": 0, "absent": 0,
                         "attendance_pct": 100.0}
            continue
        c = counts.get(e.id, {})
        present = c.get("Present", 0) + c.get("Late", 0)
        leave = c.get("Leave", 0)
        expected = max(weekdays_between(start, end) - leave, 0)
        pct = 100.0 if expected == 0 else min(100.0, 100.0 * present / expected)
        out[e.id] = {"window_days": (end - start).days + 1, "expected": expected, "present": present,
                     "late": c.get("Late", 0), "leave": leave, "absent": max(expected - present, 0),
                     "attendance_pct": round(pct, 1)}
    return out


def bulk_overtime_monthly(emps, today: date | None = None) -> dict:
    today = today or today_local()
    lo = today - timedelta(days=90)
    totals = {}
    for chunk in _chunks(e.id for e in emps):
        for emp_id, tot in (db.session.query(Attendance.employee_id, func.coalesce(func.sum(Attendance.overtime_hours), 0))
                            .filter(Attendance.employee_id.in_(chunk), Attendance.date >= lo)
                            .group_by(Attendance.employee_id).all()):
            totals[emp_id] = float(tot or 0)
    out = {}
    for e in emps:
        start = max(lo, e.join_date)
        months = max(((today - start).days + 1) / 30.0, 1.0)
        out[e.id] = round(totals.get(e.id, 0.0) / months, 1)
    return out


def bulk_leave_frequency(emps, today: date | None = None) -> dict:
    today = today or today_local()
    since = today - timedelta(days=365)
    out = {e.id: 0 for e in emps}
    for chunk in _chunks(e.id for e in emps):
        for emp_id, n in (db.session.query(LeaveRequest.employee_id, func.count(LeaveRequest.id))
                          .filter(LeaveRequest.employee_id.in_(chunk), LeaveRequest.status == "Approved",
                                  LeaveRequest.start_date >= since)
                          .group_by(LeaveRequest.employee_id).all()):
            out[emp_id] = n
    return out


def years_experience(emp: Employee, today: date | None = None) -> float:
    today = today or today_local()
    return round((emp.prior_experience_years or 0) + max((today - emp.join_date).days, 0) / 365.25, 1)


def bulk_ml_features(emps) -> dict:
    """{employee_id: the six model inputs} computed from real records (never hard-coded).
    `emps` should have their `role` relationship loaded (joinedload) to avoid extra queries."""
    emps = list(emps)
    att = bulk_attendance_stats(emps, 60)
    ot = bulk_overtime_monthly(emps)
    lv = bulk_leave_frequency(emps)
    return {e.id: {
        "attendance_pct": att[e.id]["attendance_pct"],
        "salary_level": int(e.role.salary_level) if e.role else 1,
        "overtime_hours": ot[e.id],
        "years_experience": years_experience(e),
        "job_satisfaction": float(e.job_satisfaction),
        "leave_frequency": lv[e.id],
    } for e in emps}


# ------------------------------------------------------- single-employee helpers
def attendance_stats(emp: Employee, days: int = 60, today: date | None = None) -> dict:
    return bulk_attendance_stats([emp], days, today)[emp.id]


def overtime_monthly_avg(emp: Employee, today: date | None = None) -> float:
    return bulk_overtime_monthly([emp], today)[emp.id]


def leave_frequency(emp: Employee, today: date | None = None) -> int:
    return bulk_leave_frequency([emp], today)[emp.id]


def ml_features(emp: Employee) -> dict:
    return bulk_ml_features([emp])[emp.id]


def genai_context(emp: Employee) -> dict:
    """Aggregated, non-free-text facts about one employee for LLM prompts."""
    att = attendance_stats(emp, days=90)
    last_pay = (Payroll.query.filter_by(employee_id=emp.id).order_by(Payroll.month.desc()).limit(3).all())
    leaves = LeaveRequest.query.filter(LeaveRequest.employee_id == emp.id).all()
    return {
        "name": emp.name, "department": emp.department.name if emp.department else "n/a",
        "role": emp.role.title if emp.role else "n/a",
        "tenure_years": round(max((today_local() - emp.join_date).days, 0) / 365.25, 1),
        "total_experience_years": years_experience(emp),
        "performance_rating_out_of_5": emp.performance_rating,
        "job_satisfaction_out_of_10": emp.job_satisfaction,
        "attendance_last_90_days": att,
        "avg_monthly_overtime_hours": overtime_monthly_avg(emp),
        "leave_requests": {"approved": sum(1 for l in leaves if l.status == "Approved"),
                           "pending": sum(1 for l in leaves if l.status == "Pending"),
                           "rejected": sum(1 for l in leaves if l.status == "Rejected"),
                           "approved_days": sum(l.days for l in leaves if l.status == "Approved")},
        "monthly_base_salary_inr": emp.salary,
        "recent_payslips": [{"month": p.month, "net_salary_inr": p.net_salary, "overtime_pay_inr": p.overtime_pay,
                             "bonus_inr": p.bonus, "lop_deduction_inr": p.lop_deduction} for p in last_pay],
    }
