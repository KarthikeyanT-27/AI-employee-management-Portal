"""SQLAlchemy models. PostgreSQL in production (Neon free tier); SQLite or MySQL also work."""
import json
from datetime import date, datetime

from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import check_password_hash, generate_password_hash

from timeutil import iso_utc, today_local, utcnow

db = SQLAlchemy()


class Department(db.Model):
    __tablename__ = "departments"
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(80), unique=True, nullable=False)

    def to_dict(self):
        return {"id": self.id, "name": self.name,
                "employee_count": Employee.query.filter_by(department_id=self.id, status="active").count()}


class Role(db.Model):
    __tablename__ = "roles"
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(80), nullable=False)
    department_id = db.Column(db.Integer, db.ForeignKey("departments.id"), nullable=False)
    salary_level = db.Column(db.Integer, nullable=False, default=1)  # 1 (lowest) .. 5 (highest) - ML feature
    base_salary = db.Column(db.Float, nullable=False, default=0)     # monthly, INR
    department = db.relationship("Department", backref="roles")

    def to_dict(self):
        return {"id": self.id, "title": self.title, "department_id": self.department_id,
                "department": self.department.name if self.department else None,
                "salary_level": self.salary_level, "base_salary": self.base_salary}


class Employee(db.Model):
    __tablename__ = "employees"
    id = db.Column(db.Integer, primary_key=True)
    emp_code = db.Column(db.String(20), unique=True)
    name = db.Column(db.String(120), nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False, index=True)
    password_hash = db.Column(db.String(255), nullable=False)
    account_type = db.Column(db.String(10), nullable=False, default="employee")  # employee | hr | admin
    must_change_password = db.Column(db.Boolean, nullable=False, default=False)  # set when HR/admin issues a temp password
    phone = db.Column(db.String(30))
    address = db.Column(db.String(255))
    department_id = db.Column(db.Integer, db.ForeignKey("departments.id"))
    role_id = db.Column(db.Integer, db.ForeignKey("roles.id"))
    join_date = db.Column(db.Date, nullable=False, default=today_local)
    prior_experience_years = db.Column(db.Float, nullable=False, default=0)
    salary = db.Column(db.Float, nullable=False, default=0)            # monthly base, INR
    job_satisfaction = db.Column(db.Float, nullable=False, default=7)  # 1..10 (self-reported / HR survey)
    hr_satisfaction = db.Column(db.Float)  # private HR assessment, 1..10
    performance_rating = db.Column(db.Float, nullable=False, default=3)  # 1..5
    status = db.Column(db.String(12), nullable=False, default="active")  # active | inactive
    created_at = db.Column(db.DateTime, default=utcnow)

    department = db.relationship("Department", backref="employees")
    role = db.relationship("Role", backref="employees")

    @property
    def is_staff(self):
        return self.account_type in ("hr", "admin")

    def set_password(self, raw):
        self.password_hash = generate_password_hash(raw)

    def check_password(self, raw):
        return check_password_hash(self.password_hash, raw)

    def to_dict(self, include_private=True, include_hr=False):
        d = {
            "id": self.id, "emp_code": self.emp_code, "name": self.name, "email": self.email,
            "account_type": self.account_type, "must_change_password": bool(self.must_change_password),
            "phone": self.phone, "address": self.address,
            "department_id": self.department_id, "department": self.department.name if self.department else None,
            "role_id": self.role_id, "role": self.role.title if self.role else None,
            "join_date": self.join_date.isoformat() if self.join_date else None,
            "prior_experience_years": self.prior_experience_years,
            "job_satisfaction": self.job_satisfaction,
            "performance_rating": self.performance_rating,
            "status": self.status,
        }
        if include_private:
            d["salary"] = self.salary
        if include_hr:
            d["hr_satisfaction"] = self.hr_satisfaction
        return d


class SatisfactionSurvey(db.Model):
    __tablename__ = "satisfaction_surveys"
    id = db.Column(db.Integer, primary_key=True)
    employee_id = db.Column(db.Integer, db.ForeignKey("employees.id"), nullable=False, index=True)
    score = db.Column(db.Float, nullable=False)
    comment = db.Column(db.String(500))
    submitted_at = db.Column(db.DateTime, default=utcnow, nullable=False, index=True)
    employee = db.relationship("Employee", backref="satisfaction_surveys")

    def to_dict(self):
        return {"id": self.id, "score": self.score, "comment": self.comment,
                "submitted_at": iso_utc(self.submitted_at)}


class Attendance(db.Model):
    __tablename__ = "attendance"
    __table_args__ = (db.UniqueConstraint("employee_id", "date", name="uq_att_emp_date"),
                      db.Index("ix_att_emp_date", "employee_id", "date"))
    id = db.Column(db.Integer, primary_key=True)
    employee_id = db.Column(db.Integer, db.ForeignKey("employees.id"), nullable=False, index=True)
    date = db.Column(db.Date, nullable=False, index=True)
    check_in = db.Column(db.Time)
    check_out = db.Column(db.Time)
    status = db.Column(db.String(10), nullable=False, default="Present")  # Present | Late | Absent | Leave
    overtime_hours = db.Column(db.Float, nullable=False, default=0)
    note = db.Column(db.String(60))  # e.g. "Corrected by HR", "Corrected (approved request)", "Check-out undone"
    employee = db.relationship("Employee", backref="attendance_rows")

    def to_dict(self):
        return {"id": self.id, "employee_id": self.employee_id,
                "employee": self.employee.name if self.employee else None,
                "date": self.date.isoformat(),
                "check_in": self.check_in.strftime("%H:%M") if self.check_in else None,
                "check_out": self.check_out.strftime("%H:%M") if self.check_out else None,
                "status": self.status, "overtime_hours": self.overtime_hours, "note": self.note,
                "missing_checkout": bool(self.check_in and not self.check_out and self.date < today_local())}


class AttendanceCorrection(db.Model):
    """Employee asks HR to fix a day (forgot / accidental check-out, missed check-in)."""
    __tablename__ = "attendance_corrections"
    id = db.Column(db.Integer, primary_key=True)
    employee_id = db.Column(db.Integer, db.ForeignKey("employees.id"), nullable=False, index=True)
    date = db.Column(db.Date, nullable=False)
    check_in = db.Column(db.Time)        # optional: only if the check-in must change too
    check_out = db.Column(db.Time, nullable=False)
    reason = db.Column(db.String(255), nullable=False)
    status = db.Column(db.String(10), nullable=False, default="Pending", index=True)  # Pending | Approved | Rejected
    decided_by = db.Column(db.Integer, db.ForeignKey("employees.id"))
    decided_at = db.Column(db.DateTime)
    created_at = db.Column(db.DateTime, default=utcnow)
    employee = db.relationship("Employee", foreign_keys=[employee_id], backref="corrections")

    def to_dict(self):
        return {"id": self.id, "employee_id": self.employee_id,
                "employee": self.employee.name if self.employee else None,
                "date": self.date.isoformat(),
                "check_in": self.check_in.strftime("%H:%M") if self.check_in else None,
                "check_out": self.check_out.strftime("%H:%M"), "reason": self.reason, "status": self.status,
                "created_at": iso_utc(self.created_at)}


class AuditLog(db.Model):
    __tablename__ = "audit_logs"
    id = db.Column(db.Integer, primary_key=True)
    actor_id = db.Column(db.Integer, db.ForeignKey("employees.id"), nullable=False, index=True)
    action = db.Column(db.String(80), nullable=False)
    entity_type = db.Column(db.String(50), nullable=False)
    entity_id = db.Column(db.Integer, nullable=False, index=True)
    details = db.Column(db.JSON, nullable=False, default=dict)
    created_at = db.Column(db.DateTime, default=utcnow, nullable=False, index=True)
    actor = db.relationship("Employee", backref="audit_events")

    def to_dict(self):
        return {
            "id": self.id,
            "actor_id": self.actor_id,
            "actor": self.actor.name if self.actor else None,
            "action": self.action,
            "entity_type": self.entity_type,
            "entity_id": self.entity_id,
            "details": self.details or {},
            "created_at": iso_utc(self.created_at),
        }


class LeaveRequest(db.Model):
    __tablename__ = "leave_requests"
    __table_args__ = (db.Index("ix_leave_emp_status_start", "employee_id", "status", "start_date"),)
    id = db.Column(db.Integer, primary_key=True)
    employee_id = db.Column(db.Integer, db.ForeignKey("employees.id"), nullable=False, index=True)
    start_date = db.Column(db.Date, nullable=False)
    end_date = db.Column(db.Date, nullable=False)
    leave_type = db.Column(db.String(20), nullable=False, default="Casual")
    reason = db.Column(db.String(255))
    status = db.Column(db.String(10), nullable=False, default="Pending")  # Pending | Approved | Rejected
    created_at = db.Column(db.DateTime, default=utcnow)
    employee = db.relationship("Employee", backref="leave_requests")

    @property
    def days(self):
        return (self.end_date - self.start_date).days + 1

    def to_dict(self):
        return {"id": self.id, "employee_id": self.employee_id,
                "employee": self.employee.name if self.employee else None,
                "start_date": self.start_date.isoformat(), "end_date": self.end_date.isoformat(),
                "days": self.days, "leave_type": self.leave_type, "reason": self.reason,
                "status": self.status, "created_at": iso_utc(self.created_at)}


class Payroll(db.Model):
    __tablename__ = "payroll"
    __table_args__ = (db.UniqueConstraint("employee_id", "month", name="uq_pay_emp_month"),
                      db.Index("ix_pay_month", "month"))
    id = db.Column(db.Integer, primary_key=True)
    employee_id = db.Column(db.Integer, db.ForeignKey("employees.id"), nullable=False, index=True)
    month = db.Column(db.String(7), nullable=False)  # YYYY-MM
    base_salary = db.Column(db.Float, nullable=False)
    working_days = db.Column(db.Integer, nullable=False)
    paid_days = db.Column(db.Integer, nullable=False)
    lop_deduction = db.Column(db.Float, nullable=False, default=0)
    overtime_hours = db.Column(db.Float, nullable=False, default=0)
    overtime_pay = db.Column(db.Float, nullable=False, default=0)
    bonus = db.Column(db.Float, nullable=False, default=0)
    pf = db.Column(db.Float, nullable=False, default=0)
    tax = db.Column(db.Float, nullable=False, default=0)
    net_salary = db.Column(db.Float, nullable=False)
    created_at = db.Column(db.DateTime, default=utcnow)
    employee = db.relationship("Employee", backref="payslips")

    def to_dict(self):
        return {"id": self.id, "employee_id": self.employee_id,
                "employee": self.employee.name if self.employee else None,
                "month": self.month, "base_salary": self.base_salary, "working_days": self.working_days,
                "paid_days": self.paid_days, "lop_deduction": self.lop_deduction,
                "overtime_hours": self.overtime_hours, "overtime_pay": self.overtime_pay,
                "bonus": self.bonus, "pf": self.pf, "tax": self.tax, "net_salary": self.net_salary}


class AttritionPrediction(db.Model):
    __tablename__ = "attrition_predictions"
    __table_args__ = (db.Index("ix_pred_emp_id", "employee_id", "id"),)
    id = db.Column(db.Integer, primary_key=True)
    employee_id = db.Column(db.Integer, db.ForeignKey("employees.id"), nullable=False, index=True)
    risk_label = db.Column(db.String(10), nullable=False)
    probabilities = db.Column(db.Text, nullable=False)  # JSON
    features = db.Column(db.Text, nullable=False)       # JSON
    created_at = db.Column(db.DateTime, default=utcnow)
    employee = db.relationship("Employee", backref="predictions")

    def to_dict(self):
        return {"id": self.id, "employee_id": self.employee_id,
                "employee": self.employee.name if self.employee else None,
                "department": self.employee.department.name if self.employee and self.employee.department else None,
                "role": self.employee.role.title if self.employee and self.employee.role else None,
                "risk_label": self.risk_label, "probabilities": json.loads(self.probabilities),
                "features": json.loads(self.features),
                "created_at": iso_utc(self.created_at)}


class Notification(db.Model):
    """In-app notification. Stored so nothing is lost while a user is offline, and pushed live over SSE."""
    __tablename__ = "notifications"
    __table_args__ = (db.Index("ix_notif_user_read", "user_id", "is_read", "id"),)
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("employees.id"), nullable=False, index=True)
    kind = db.Column(db.String(30), nullable=False)          # leave | correction | payroll | attendance | attrition | system
    title = db.Column(db.String(120), nullable=False)
    message = db.Column(db.String(300), nullable=False)
    link = db.Column(db.String(60))                          # dashboard view to open, e.g. "leaves"
    is_read = db.Column(db.Boolean, nullable=False, default=False)
    created_at = db.Column(db.DateTime, default=utcnow, nullable=False, index=True)

    def to_dict(self):
        return {"id": self.id, "kind": self.kind, "title": self.title, "message": self.message,
                "link": self.link, "is_read": bool(self.is_read), "created_at": iso_utc(self.created_at)}


class LoginAttempt(db.Model):
    """Persistent brute-force protection (survives restarts / sleeping free hosts)."""
    __tablename__ = "login_attempts"
    __table_args__ = (db.UniqueConstraint("email", "ip", name="uq_login_email_ip"),)
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(120), nullable=False)
    ip = db.Column(db.String(64), nullable=False)
    fail_count = db.Column(db.Integer, nullable=False, default=0)
    window_start = db.Column(db.DateTime, nullable=False, default=utcnow)
    locked_until = db.Column(db.DateTime)
