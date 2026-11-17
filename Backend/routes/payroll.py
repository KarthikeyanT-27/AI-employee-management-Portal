import calendar
from collections import defaultdict
from datetime import date, timedelta

from flask import Blueprint, current_app, jsonify, request
from sqlalchemy.orm import joinedload

from models import Attendance, Employee, LeaveRequest, Payroll, db
from services.notifications import notify_users
from timeutil import today_local
from services.features import weekdays_between
from utils import err, hr_required, login_required, num

bp = Blueprint("payroll", __name__, url_prefix="/api/payroll")


def _month_bounds(month: str):
    try:
        y, m = (int(x) for x in month.split("-"))
        first = date(y, m, 1)
    except (ValueError, AttributeError):
        raise ValueError("month must be in YYYY-MM format")
    return first, date(y, m, calendar.monthrange(y, m)[1])


def _preload(emp_ids, month: str):
    """3 queries for ALL employees: attendance rows, unpaid-leave days, existing payslips."""
    first, last = _month_bounds(month)
    att = defaultdict(dict)
    for emp_id, day, status, ot in (db.session.query(Attendance.employee_id, Attendance.date, Attendance.status,
                                                     Attendance.overtime_hours)
                                    .filter(Attendance.employee_id.in_(emp_ids), Attendance.date >= first,
                                            Attendance.date <= last)):
        att[emp_id][day] = (status, ot or 0)
    unpaid = defaultdict(set)
    for emp_id, s, e in (db.session.query(LeaveRequest.employee_id, LeaveRequest.start_date, LeaveRequest.end_date)
                         .filter(LeaveRequest.employee_id.in_(emp_ids), LeaveRequest.status == "Approved",
                                 LeaveRequest.leave_type == "Unpaid", LeaveRequest.start_date <= last,
                                 LeaveRequest.end_date >= first)):
        d = max(s, first)
        while d <= min(e, last):
            unpaid[emp_id].add(d)
            d += timedelta(days=1)
    existing = {p.employee_id: p for p in Payroll.query.filter(Payroll.employee_id.in_(emp_ids), Payroll.month == month)}
    return att, unpaid, existing


def compute_payroll(emp: Employee, month: str, bonus: float | None, att: dict, unpaid: set, existing: Payroll | None) -> Payroll:
    cfg = current_app.config
    first, last = _month_bounds(month)
    start = max(first, emp.join_date)
    working_days = weekdays_between(first, last)
    window_end = min(last, today_local() - timedelta(days=1))  # only past days can be "absent"

    lop_days, d = 0, start
    while d <= window_end:
        if d.weekday() < 5:
            r = att.get(d)
            ok = r is not None and r[0] in ("Present", "Late", "Leave")
            if not ok or d in unpaid:
                lop_days += 1
        d += timedelta(days=1)
    if emp.join_date > first:  # days before the employee joined are unpaid
        lop_days += weekdays_between(first, min(emp.join_date - timedelta(days=1), last))
    lop_days = min(lop_days, working_days)

    base = emp.salary
    per_day = base / working_days if working_days else 0
    lop = round(per_day * lop_days, 2)
    ot_hours = round(sum(v[1] for v in att.values()), 2)
    ot_pay = round(ot_hours * (base / (working_days * cfg["STANDARD_HOURS"]) if working_days else 0)
                   * cfg["OVERTIME_MULTIPLIER"], 2)
    bonus = existing.bonus if (bonus is None and existing) else (bonus or 0.0)
    taxable = base - lop
    pf = round(taxable * cfg["PF_RATE"], 2)
    tax = round(taxable * cfg["TAX_RATE"], 2) if base > cfg["TAX_THRESHOLD"] else 0.0
    net = round(taxable + ot_pay + bonus - pf - tax, 2)

    p = existing or Payroll(employee_id=emp.id, month=month)
    p.base_salary, p.working_days, p.paid_days = base, working_days, working_days - lop_days
    p.lop_deduction, p.overtime_hours, p.overtime_pay = lop, ot_hours, ot_pay
    p.bonus, p.pf, p.tax, p.net_salary = bonus, pf, tax, net
    db.session.add(p)
    return p


def generate_for(employees, month: str, bonus: float | None = None):
    ids = [e.id for e in employees]
    att, unpaid, existing = _preload(ids, month)
    return [compute_payroll(e, month, bonus, att.get(e.id, {}), unpaid.get(e.id, set()), existing.get(e.id))
            for e in employees]


@bp.post("/generate")
@hr_required
def generate(user):
    d = request.get_json(silent=True) or {}
    month = d.get("month") or today_local().strftime("%Y-%m")
    try:
        _month_bounds(month)
        q = Employee.query.filter(Employee.status == "active", Employee.salary > 0)
        if d.get("employee_id"):
            q = q.filter_by(id=int(d["employee_id"]))
        employees = q.all()
        if not employees:
            return err("No matching active employees", 404)
        bonus = num(d["bonus"], "bonus", 0, 10_000_000) if d.get("bonus") not in (None, "") else None
        if bonus is not None and not d.get("employee_id"):
            raise ValueError("A bonus can only be set when generating for a single employee")
        slips = generate_for(employees, month, bonus)
        db.session.flush()
        for p in slips:     # each employee is told their own payslip is ready (live + stored)
            notify_users([p.employee_id], "payroll", "Payslip ready",
                         f"Your payslip for {month} is available. Net pay: ₹{p.net_salary:,.0f}.", "payslips")
    except ValueError as exc:
        return err(str(exc))
    db.session.commit()
    return jsonify({"month": month, "generated": len(slips), "payroll": [p.to_dict() for p in slips]}), 201


@bp.get("")
@hr_required
def list_payroll(user):
    q = Payroll.query.options(joinedload(Payroll.employee))
    if request.args.get("month"):
        q = q.filter_by(month=request.args["month"])
    if request.args.get("employee_id"):
        q = q.filter_by(employee_id=int(request.args["employee_id"]))
    rows = q.order_by(Payroll.month.desc(), Payroll.employee_id).limit(1000).all()
    return jsonify({"payroll": [p.to_dict() for p in rows],
                    "total_net": round(sum(p.net_salary for p in rows), 2)})


@bp.get("/me")
@login_required
def my_payroll(user):
    rows = Payroll.query.filter_by(employee_id=user.id).order_by(Payroll.month.desc()).all()
    return jsonify({"payroll": [p.to_dict() for p in rows]})
