from datetime import date, datetime, time, timedelta

from flask import Blueprint, current_app, jsonify, request
from sqlalchemy.orm import joinedload

from models import AuditLog, Attendance, AttendanceCorrection, Employee, LeaveRequest, db
from services.notifications import notify_staff, notify_user
from timeutil import now_local, today_local, utcnow
from services.features import attendance_stats
from utils import err, hr_required, login_required, parse_date, parse_time

bp = Blueprint("attendance", __name__, url_prefix="/api")

LATE_AFTER = time(9, 30)


def _hours(cin: time, cout: time) -> float:
    d = today_local()
    return (datetime.combine(d, cout) - datetime.combine(d, cin)).total_seconds() / 3600


def _overtime(cin: time, cout: time) -> float:
    return round(max(_hours(cin, cout) - current_app.config["STANDARD_HOURS"], 0), 2)


def _undo_state(row):
    """Can the employee still undo today's check-out? -> (bool, minutes_left)"""
    if not row or not row.check_out or row.date != today_local():
        return False, 0
    window = current_app.config["UNDO_WINDOW_MINUTES"]
    out_dt = datetime.combine(row.date, row.check_out)
    left = window - (now_local() - out_dt).total_seconds() / 60
    return left > 0, max(int(left + 0.99), 0)


# ----------------------------------------------------------------- attendance
@bp.post("/attendance/check-in")
@login_required
def check_in(user):
    today, now = today_local(), now_local().time().replace(second=0, microsecond=0)
    row = Attendance.query.filter_by(employee_id=user.id, date=today).first()
    if row and row.check_in:
        return err("Already checked in today", 409)
    on_leave = LeaveRequest.query.filter(LeaveRequest.employee_id == user.id, LeaveRequest.status == "Approved",
                                         LeaveRequest.start_date <= today, LeaveRequest.end_date >= today).first()
    if on_leave:
        return err("You are on approved leave today", 409)
    status = "Late" if now > LATE_AFTER else "Present"
    if row:
        row.check_in, row.status = now, status
    else:
        row = Attendance(employee_id=user.id, date=today, check_in=now, status=status)
        db.session.add(row)
    notify_staff("attendance", "Check-in", f"{user.name} checked in ({status}).", "attendance", persist=False)
    db.session.commit()
    return jsonify({"attendance": row.to_dict()}), 201


@bp.post("/attendance/check-out")
@login_required
def check_out(user):
    row = Attendance.query.filter_by(employee_id=user.id, date=today_local()).first()
    if not row or not row.check_in:
        return err("You have not checked in today", 409)
    if row.check_out:
        return err("Already checked out today. If this was a mistake you can undo it for a short time.", 409)
    now = now_local().time().replace(second=0, microsecond=0)
    if now < row.check_in:
        return err("Check-out cannot be before check-in")
    row.check_out = now
    row.overtime_hours = _overtime(row.check_in, now)
    notify_staff("attendance", "Check-out", f"{user.name} checked out.", "attendance", persist=False)
    db.session.commit()
    can, left = _undo_state(row)
    return jsonify({"attendance": row.to_dict(), "can_undo": can, "undo_minutes_left": left})


@bp.post("/attendance/undo-checkout")
@login_required
def undo_checkout(user):
    """Accidental check-out: reopen today's day, but only within a short window (default 30 min).
    After that, the employee must file a correction request that HR approves."""
    row = Attendance.query.filter_by(employee_id=user.id, date=today_local()).first()
    if not row or not row.check_out:
        return err("There is no check-out to undo today", 409)
    can, _ = _undo_state(row)
    if not can:
        mins = current_app.config["UNDO_WINDOW_MINUTES"]
        return err(f"The {mins}-minute undo window has passed. Please submit an attendance correction request.", 409,
                   code="UNDO_EXPIRED")
    row.check_out, row.overtime_hours, row.note = None, 0, "Check-out undone by employee"
    notify_staff("attendance", "Check-out undone", f"{user.name} undid a check-out.", "attendance", persist=False)
    db.session.commit()
    return jsonify({"attendance": row.to_dict(), "message": "Check-out undone. You are checked in again."})


@bp.get("/attendance/me")
@login_required
def my_attendance(user):
    days = min(int(request.args.get("days", 60)), 365)
    since = today_local() - timedelta(days=days)
    rows = (Attendance.query.filter(Attendance.employee_id == user.id, Attendance.date >= since)
            .order_by(Attendance.date.desc()).all())
    today_row = next((r for r in rows if r.date == today_local()), None)
    pending_dates = {c.date for c in AttendanceCorrection.query.filter_by(employee_id=user.id, status="Pending")}
    missing = [r.to_dict() for r in rows if r.check_in and not r.check_out and r.date < today_local()
               and r.date not in pending_dates and (today_local() - r.date).days <= current_app.config["CORRECTION_MAX_DAYS"]]
    can, left = _undo_state(today_row)
    return jsonify({"attendance": [r.to_dict() for r in rows], "today": today_row.to_dict() if today_row else None,
                    "stats": attendance_stats(user, days=30), "missing_checkouts": missing,
                    "can_undo": can, "undo_minutes_left": left,
                    "undo_window_minutes": current_app.config["UNDO_WINDOW_MINUTES"]})


@bp.get("/attendance")
@hr_required
def all_attendance(user):
    q = Attendance.query.options(joinedload(Attendance.employee))
    if request.args.get("employee_id"):
        q = q.filter_by(employee_id=int(request.args["employee_id"]))
    if request.args.get("date"):
        try:
            q = q.filter_by(date=parse_date(request.args["date"]))
        except ValueError as exc:
            return err(str(exc))
    else:
        q = q.filter(Attendance.date >= today_local() - timedelta(days=int(request.args.get("days", 14))))
    return jsonify({"attendance": [r.to_dict() for r in q.order_by(Attendance.date.desc()).limit(1000).all()]})


@bp.get("/attendance/missed")
@hr_required
def missed_checkouts(user):
    """Days (last 14) where an employee checked in but never checked out."""
    since = today_local() - timedelta(days=14)
    rows = (Attendance.query.options(joinedload(Attendance.employee))
            .filter(Attendance.date >= since, Attendance.date < today_local(),
                    Attendance.check_in.isnot(None), Attendance.check_out.is_(None))
            .order_by(Attendance.date.desc()).all())
    return jsonify({"missed": [r.to_dict() for r in rows]})


@bp.post("/attendance/mark")
@hr_required
def mark_attendance(user):
    """HR can create/correct an attendance record directly."""
    d = request.get_json(silent=True) or {}
    try:
        emp = db.session.get(Employee, d.get("employee_id"))
        if not emp:
            raise ValueError("Employee not found")
        day = parse_date(d.get("date"), "date")
        if day > today_local():
            raise ValueError("Cannot mark attendance for a future date")
        status = d.get("status")
        if status not in ("Present", "Late", "Absent", "Leave"):
            raise ValueError("status must be Present, Late, Absent or Leave")
        cin = parse_time(d["check_in"], "check_in") if d.get("check_in") else None
        cout = parse_time(d["check_out"], "check_out") if d.get("check_out") else None
        if cin and cout and cout <= cin:
            raise ValueError("Check-out must be after check-in")
    except (ValueError, KeyError) as exc:
        return err(str(exc))
    row = Attendance.query.filter_by(employee_id=emp.id, date=day).first() or Attendance(employee_id=emp.id, date=day)
    row.status, row.check_in, row.check_out = status, cin, cout
    row.overtime_hours = _overtime(cin, cout) if cin and cout else 0
    row.note = f"Set by {user.account_type.upper()} ({user.name})"[:60]
    db.session.add(row)
    if emp.id != user.id:
        notify_user(emp.id, "attendance", "Attendance updated",
                    f"HR updated your attendance for {day.isoformat()}: {status}.", "attendance")
    db.session.commit()
    return jsonify({"attendance": row.to_dict()})


# ------------------------------------------------------- correction requests
@bp.post("/attendance/corrections")
@login_required
def request_correction(user):
    """Employee asks HR to fix a day: forgot to check out, or checked out by mistake after the undo window."""
    d = request.get_json(silent=True) or {}
    cfg = current_app.config
    try:
        day = parse_date(d.get("date"), "date")
        if day > today_local():
            raise ValueError("You cannot request a correction for a future date")
        if (today_local() - day).days > cfg["CORRECTION_MAX_DAYS"]:
            raise ValueError(f"Corrections can only be requested for the last {cfg['CORRECTION_MAX_DAYS']} days. Contact HR.")
        cout = parse_time(d.get("check_out"), "check_out")
        cin_new = parse_time(d["check_in"], "check_in") if d.get("check_in") else None
        reason = (d.get("reason") or "").strip()
        if len(reason) < 5:
            raise ValueError("Please give a short reason (at least 5 characters)")
        row = Attendance.query.filter_by(employee_id=user.id, date=day).first()
        if row and row.status == "Leave":
            raise ValueError("That day is approved leave")
        cin = cin_new or (row.check_in if row else None)
        if cin is None:
            raise ValueError("Enter your check-in time too, because there is no check-in recorded for that day")
        if cout <= cin:
            raise ValueError("Check-out must be after check-in")
        if _hours(cin, cout) > cfg["MAX_WORK_HOURS"]:
            raise ValueError(f"Worked time cannot exceed {cfg['MAX_WORK_HOURS']} hours in a day")
        if day == today_local() and cout > now_local().time():
            raise ValueError("Check-out time cannot be in the future")
    except ValueError as exc:
        return err(str(exc))
    if AttendanceCorrection.query.filter_by(employee_id=user.id, date=day, status="Pending").first():
        return err("You already have a pending correction for this date", 409)
    corr = AttendanceCorrection(employee_id=user.id, date=day, check_in=cin_new, check_out=cout, reason=reason[:255])
    db.session.add(corr)
    db.session.commit()
    db.session.add(AuditLog(
        actor_id=user.id,
        action="attendance_correction_requested",
        entity_type="attendance_correction",
        entity_id=corr.id,
        details={"date": day.isoformat(), "reason": corr.reason},
    ))
    notify_staff("correction", "New attendance correction",
                 f"{user.name} requested a correction for {day.isoformat()}.", "corrections", exclude=user.id)
    db.session.commit()
    return jsonify({"correction": corr.to_dict(), "message": "Request sent to HR for approval"}), 201


@bp.get("/attendance/corrections/me")
@login_required
def my_corrections(user):
    rows = AttendanceCorrection.query.filter_by(employee_id=user.id).order_by(AttendanceCorrection.id.desc()).limit(50).all()
    return jsonify({"corrections": [c.to_dict() for c in rows]})


@bp.get("/attendance/corrections")
@hr_required
def list_corrections(user):
    q = AttendanceCorrection.query.options(joinedload(AttendanceCorrection.employee))
    if request.args.get("status"):
        q = q.filter_by(status=request.args["status"])
    rows = q.order_by(AttendanceCorrection.id.desc()).limit(300).all()
    return jsonify({"corrections": [c.to_dict() for c in rows]})


@bp.get("/attendance/audit")
@hr_required
def correction_audit(user):
    rows = (AuditLog.query.filter_by(entity_type="attendance_correction")
            .order_by(AuditLog.created_at.desc()).limit(500).all())
    return jsonify({"audit": [row.to_dict() for row in rows]})


@bp.put("/attendance/corrections/<int:corr_id>/decision")
@hr_required
def decide_correction(user, corr_id):
    corr = db.session.get(AttendanceCorrection, corr_id)
    decision = (request.get_json(silent=True) or {}).get("decision")
    if not corr:
        return err("Correction request not found", 404)
    if decision not in ("Approved", "Rejected"):
        return err("decision must be Approved or Rejected")
    if corr.status != "Pending":
        return err(f"Request already {corr.status.lower()}", 409)
    corr.status, corr.decided_by, corr.decided_at = decision, user.id, utcnow()
    db.session.add(AuditLog(
        actor_id=user.id,
        action=f"attendance_correction_{decision.lower()}",
        entity_type="attendance_correction",
        entity_id=corr.id,
        details={"employee_id": corr.employee_id, "date": corr.date.isoformat()},
    ))
    if decision == "Approved":
        row = Attendance.query.filter_by(employee_id=corr.employee_id, date=corr.date).first() \
              or Attendance(employee_id=corr.employee_id, date=corr.date)
        cin = corr.check_in or row.check_in
        if cin is None or corr.check_out <= cin:
            return err("Request is no longer valid for this record. Reject it or fix the record directly.", 409)
        row.check_in, row.check_out = cin, corr.check_out
        row.status = "Late" if cin > LATE_AFTER else "Present"
        row.overtime_hours = _overtime(cin, corr.check_out)
        row.note = "Corrected (approved request)"
        db.session.add(row)
    notify_user(corr.employee_id, "correction", f"Correction {decision.lower()}",
                f"Your attendance correction for {corr.date.isoformat()} was {decision.lower()}.", "attendance")
    db.session.commit()
    return jsonify({"correction": corr.to_dict()})


# --------------------------------------------------------------------- leaves
@bp.post("/leaves")
@login_required
def apply_leave(user):
    d = request.get_json(silent=True) or {}
    try:
        start, end = parse_date(d.get("start_date"), "start_date"), parse_date(d.get("end_date"), "end_date")
        if end < start:
            raise ValueError("end_date cannot be before start_date")
        if start < today_local():
            raise ValueError("Leave cannot start in the past")
        if (end - start).days > 30:
            raise ValueError("A single request cannot exceed 31 days")
        ltype = d.get("leave_type", "Casual")
        if ltype not in ("Casual", "Sick", "Earned", "Unpaid"):
            raise ValueError("leave_type must be Casual, Sick, Earned or Unpaid")
    except ValueError as exc:
        return err(str(exc))
    overlap = LeaveRequest.query.filter(LeaveRequest.employee_id == user.id, LeaveRequest.status != "Rejected",
                                        LeaveRequest.start_date <= end, LeaveRequest.end_date >= start).first()
    if overlap:
        return err("You already have a leave request overlapping these dates", 409)
    lr = LeaveRequest(employee_id=user.id, start_date=start, end_date=end, leave_type=ltype,
                      reason=(d.get("reason") or "")[:255])
    db.session.add(lr)
    notify_staff("leave", "New leave request",
                 f"{user.name} requested {ltype} leave: {start.isoformat()} to {end.isoformat()}.", "leaves",
                 exclude=user.id)
    db.session.commit()
    return jsonify({"leave": lr.to_dict()}), 201


@bp.get("/leaves/me")
@login_required
def my_leaves(user):
    rows = LeaveRequest.query.filter_by(employee_id=user.id).order_by(LeaveRequest.created_at.desc()).all()
    return jsonify({"leaves": [r.to_dict() for r in rows]})


@bp.get("/leaves")
@hr_required
def all_leaves(user):
    q = LeaveRequest.query.options(joinedload(LeaveRequest.employee))
    if request.args.get("status"):
        q = q.filter_by(status=request.args["status"])
    return jsonify({"leaves": [r.to_dict() for r in q.order_by(LeaveRequest.created_at.desc()).limit(500).all()]})


@bp.put("/leaves/<int:leave_id>/decision")
@hr_required
def decide_leave(user, leave_id):
    lr = db.session.get(LeaveRequest, leave_id)
    decision = (request.get_json(silent=True) or {}).get("decision")
    if not lr:
        return err("Leave request not found", 404)
    if decision not in ("Approved", "Rejected"):
        return err("decision must be Approved or Rejected")
    if lr.status != "Pending":
        return err(f"Request already {lr.status.lower()}", 409)
    lr.status = decision
    if decision == "Approved":  # mark weekdays as Leave in the attendance register
        existing = {r.date: r for r in Attendance.query.filter(
            Attendance.employee_id == lr.employee_id, Attendance.date >= lr.start_date, Attendance.date <= lr.end_date)}
        day = lr.start_date
        while day <= lr.end_date:
            if day.weekday() < 5:
                row = existing.get(day) or Attendance(employee_id=lr.employee_id, date=day)
                row.status, row.overtime_hours = "Leave", 0
                db.session.add(row)
            day += timedelta(days=1)
    notify_user(lr.employee_id, "leave", f"Leave {decision.lower()}",
                f"Your {lr.leave_type} leave ({lr.start_date.isoformat()} to {lr.end_date.isoformat()}) "
                f"was {decision.lower()}.", "leave")
    db.session.commit()
    return jsonify({"leave": lr.to_dict()})
