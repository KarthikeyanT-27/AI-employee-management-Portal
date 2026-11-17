import secrets
from datetime import date

from flask import Blueprint, jsonify, request
from sqlalchemy import func
from sqlalchemy.orm import joinedload

from models import Department, Employee, Role, SatisfactionSurvey, db
from timeutil import today_local
from utils import EMAIL_RE, check_password_strength, err, hr_required, login_required, next_emp_code, num, parse_date

bp = Blueprint("employees", __name__, url_prefix="/api")


# ------------------------------------------------------------------ employees
def _can_manage(actor: Employee, target: Employee) -> bool:
    """Admins manage everyone; HR manages employee-type accounts only."""
    return actor.account_type == "admin" or target.account_type == "employee"


def _active_admins() -> int:
    return Employee.query.filter_by(account_type="admin", status="active").count()


@bp.get("/employees")
@hr_required
def list_employees(user):
    q = Employee.query.options(joinedload(Employee.department), joinedload(Employee.role))
    if request.args.get("department_id"):
        q = q.filter_by(department_id=int(request.args["department_id"]))
    if request.args.get("status"):
        q = q.filter_by(status=request.args["status"])
    if request.args.get("type") in ("employee", "hr", "admin"):
        q = q.filter_by(account_type=request.args["type"])
    term = (request.args.get("q") or "").strip()
    if term:
        like = f"%{term}%"
        q = q.filter(Employee.name.ilike(like) | Employee.email.ilike(like) | Employee.emp_code.ilike(like))
    rows = q.order_by(Employee.name).all()
    out = []
    for e in rows:
        d = e.to_dict(include_hr=True)
        d["can_manage"] = _can_manage(user, e)
        out.append(d)
    return jsonify({"employees": out, "actor_type": user.account_type})


@bp.post("/employees")
@hr_required
def create_employee(user):
    """Only HR/admin can create accounts. HR -> employees only; admin -> employee, hr or admin."""
    d = request.get_json(silent=True) or {}
    acct = d.get("account_type", "employee")
    if acct not in ("employee", "hr", "admin"):
        return err("account_type must be employee, hr or admin")
    if acct != "employee" and user.account_type != "admin":
        return err("Only an administrator can create HR or admin accounts", 403)
    try:
        name, email = (d.get("name") or "").strip(), (d.get("email") or "").strip().lower()
        if len(name) < 2:
            raise ValueError("Name must be at least 2 characters")
        if not EMAIL_RE.match(email):
            raise ValueError("Enter a valid email address")
        raw_password = d.get("password") or ""
        if acct != "employee" and not raw_password:
            raise ValueError("A temporary password is required for HR and admin accounts")
        if raw_password:
            problem = check_password_strength(raw_password, email)
            if problem:
                raise ValueError(problem.replace("Password", "Temporary password", 1))
        if Employee.query.filter_by(email=email).first():
            return err("An account with this email already exists", 409)
        role = db.session.get(Role, d.get("role_id")) if d.get("role_id") else None
        emp = Employee(
            name=name, email=email, emp_code=next_emp_code(), account_type=acct,
            must_change_password=bool(raw_password),
            phone=(d.get("phone") or "")[:30], address=(d.get("address") or "")[:255],
            department_id=d.get("department_id") or (role.department_id if role else None),
            role_id=role.id if role else None,
            join_date=parse_date(d["join_date"], "join_date") if d.get("join_date") else today_local(),
            prior_experience_years=num(d.get("prior_experience_years"), "prior_experience_years", 0, 50, 0),
            salary=num(d.get("salary"), "salary", 0, 10_000_000, role.base_salary if role else 0),
            job_satisfaction=num(d.get("job_satisfaction"), "job_satisfaction", 1, 10, 7),
            performance_rating=num(d.get("performance_rating"), "performance_rating", 1, 5, 3))
    except ValueError as exc:
        return err(str(exc))
    emp.set_password(raw_password or secrets.token_urlsafe(32))
    db.session.add(emp)
    db.session.commit()
    return jsonify({"employee": emp.to_dict(),
                    "message": ("Account created. The employee can use matching Google sign-in."
                                if acct == "employee" and not raw_password
                                else "Account created. The user must change the temporary password at first login.")}), 201


@bp.get("/employees/<int:emp_id>")
@login_required
def get_employee(user, emp_id):
    if not user.is_staff and user.id != emp_id:
        return err("Forbidden", 403)
    emp = db.session.get(Employee, emp_id)
    return jsonify({"employee": emp.to_dict(include_hr=user.is_staff)}) if emp else err("Employee not found", 404)


@bp.put("/employees/<int:emp_id>")
@hr_required
def update_employee(user, emp_id):
    emp = db.session.get(Employee, emp_id)
    if not emp:
        return err("Employee not found", 404)
    if not _can_manage(user, emp):
        return err("Only an administrator can modify HR or admin accounts", 403)
    d = request.get_json(silent=True) or {}
    try:
        if "name" in d:
            if len(d["name"].strip()) < 2:
                raise ValueError("Name must be at least 2 characters")
            emp.name = d["name"].strip()
        if "email" in d:
            email = d["email"].strip().lower()
            if not EMAIL_RE.match(email):
                raise ValueError("Enter a valid email address")
            if Employee.query.filter(Employee.email == email, Employee.id != emp.id).first():
                return err("Email already in use", 409)
            emp.email = email
        for f in ("phone", "address"):
            if f in d:
                setattr(emp, f, (d[f] or "")[:255])
        if "department_id" in d:
            emp.department_id = d["department_id"] or None
        if "role_id" in d:
            emp.role_id = d["role_id"] or None
        if "join_date" in d:
            emp.join_date = parse_date(d["join_date"], "join_date")
        if "prior_experience_years" in d:
            emp.prior_experience_years = num(d["prior_experience_years"], "prior_experience_years", 0, 50)
        if "salary" in d:
            emp.salary = num(d["salary"], "salary", 0, 10_000_000)
        if "job_satisfaction" in d:
            if user.account_type not in ("hr", "admin"):
                return err("Employee satisfaction must be submitted through a survey", 403)
            emp.job_satisfaction = num(d["job_satisfaction"], "job_satisfaction", 1, 10)
        if "hr_satisfaction" in d:
            emp.hr_satisfaction = (None if d["hr_satisfaction"] in (None, "")
                                   else num(d["hr_satisfaction"], "hr_satisfaction", 1, 10))
        if "performance_rating" in d:
            emp.performance_rating = num(d["performance_rating"], "performance_rating", 1, 5)
        if d.get("status") in ("active", "inactive") and d["status"] != emp.status:
            if emp.id == user.id:
                raise ValueError("You cannot deactivate your own account")
            if d["status"] == "inactive" and emp.account_type == "admin" and _active_admins() <= 1:
                raise ValueError("At least one active administrator must remain")
            emp.status = d["status"]
        if d.get("account_type") in ("employee", "hr", "admin") and d["account_type"] != emp.account_type:
            if user.account_type != "admin":
                return err("Only an administrator can change account types", 403)
            if emp.id == user.id:
                raise ValueError("You cannot change your own account type")
            if emp.account_type == "admin" and _active_admins() <= 1:
                raise ValueError("At least one active administrator must remain")
            emp.account_type = d["account_type"]
        if d.get("password"):
            if emp.id == user.id:
                raise ValueError("Use “Change password” for your own account")
            problem = check_password_strength(d["password"], emp.email)
            if problem:
                raise ValueError(problem.replace("Password", "Temporary password", 1))
            emp.set_password(d["password"])
            emp.must_change_password = True   # reset by someone else -> must be changed at next login
    except ValueError as exc:
        return err(str(exc))
    db.session.commit()
    return jsonify({"employee": emp.to_dict()})


@bp.delete("/employees/<int:emp_id>")
@hr_required
def deactivate_employee(user, emp_id):
    """Soft delete: keeps attendance/payroll history intact."""
    emp = db.session.get(Employee, emp_id)
    if not emp:
        return err("Employee not found", 404)
    if emp.id == user.id:
        return err("You cannot deactivate your own account")
    if not _can_manage(user, emp):
        return err("Only an administrator can deactivate HR or admin accounts", 403)
    if emp.account_type == "admin" and _active_admins() <= 1:
        return err("At least one active administrator must remain")
    emp.status = "inactive"
    db.session.commit()
    return jsonify({"message": "Employee deactivated"})


@bp.put("/profile")
@login_required
def update_own_profile(user):
    """Everyone may edit only their own contact details.
    (Passwords are changed through /auth/change-password.)"""
    d = request.get_json(silent=True) or {}
    try:
        if "name" in d:
            if len(d["name"].strip()) < 2:
                raise ValueError("Name must be at least 2 characters")
            user.name = d["name"].strip()
        for f in ("phone", "address"):
            if f in d:
                setattr(user, f, (d[f] or "")[:255])
    except ValueError as exc:
        return err(str(exc))
    db.session.commit()
    return jsonify({"user": user.to_dict()})


@bp.post("/satisfaction")
@login_required
def submit_satisfaction(user):
    if user.account_type != "employee":
        return err("Only employees can submit satisfaction surveys", 403)
    d = request.get_json(silent=True) or {}
    try:
        score = num(d.get("score"), "score", 1, 10)
    except ValueError as exc:
        return err(str(exc))
    survey = SatisfactionSurvey(employee_id=user.id, score=score,
                                comment=(d.get("comment") or "")[:500])
    user.job_satisfaction = score
    db.session.add(survey)
    db.session.commit()
    return jsonify({"survey": survey.to_dict(), "user": user.to_dict()})


@bp.get("/satisfaction")
@login_required
def satisfaction_history(user):
    rows = (SatisfactionSurvey.query.filter_by(employee_id=user.id)
            .order_by(SatisfactionSurvey.submitted_at.desc()).limit(12).all())
    return jsonify({"surveys": [row.to_dict() for row in rows],
                    "hr_satisfaction": user.hr_satisfaction})


# ---------------------------------------------------------------- departments
@bp.get("/departments")
@login_required
def list_departments(user):
    counts = dict(db.session.query(Employee.department_id, func.count(Employee.id))
                  .filter(Employee.status == "active").group_by(Employee.department_id).all())
    return jsonify({"departments": [{"id": d.id, "name": d.name, "employee_count": counts.get(d.id, 0)}
                                    for d in Department.query.order_by(Department.name)]})


@bp.post("/departments")
@hr_required
def create_department(user):
    name = ((request.get_json(silent=True) or {}).get("name") or "").strip()
    if len(name) < 2:
        return err("Department name is required")
    if Department.query.filter(Department.name.ilike(name)).first():
        return err("Department already exists", 409)
    dep = Department(name=name)
    db.session.add(dep)
    db.session.commit()
    return jsonify({"department": dep.to_dict()}), 201


@bp.put("/departments/<int:dep_id>")
@hr_required
def rename_department(user, dep_id):
    dep = db.session.get(Department, dep_id)
    name = ((request.get_json(silent=True) or {}).get("name") or "").strip()
    if not dep:
        return err("Department not found", 404)
    if len(name) < 2:
        return err("Department name is required")
    dep.name = name
    db.session.commit()
    return jsonify({"department": dep.to_dict()})


@bp.delete("/departments/<int:dep_id>")
@hr_required
def delete_department(user, dep_id):
    dep = db.session.get(Department, dep_id)
    if not dep:
        return err("Department not found", 404)
    if Employee.query.filter_by(department_id=dep.id).count() or Role.query.filter_by(department_id=dep.id).count():
        return err("Move or delete the department's employees and roles first", 409)
    db.session.delete(dep)
    db.session.commit()
    return jsonify({"message": "Department deleted"})


# ---------------------------------------------------------------------- roles
@bp.get("/roles")
@login_required
def list_roles(user):
    return jsonify({"roles": [r.to_dict() for r in Role.query.options(joinedload(Role.department)).order_by(Role.title)]})


@bp.post("/roles")
@hr_required
def create_role(user):
    d = request.get_json(silent=True) or {}
    try:
        title = (d.get("title") or "").strip()
        if len(title) < 2:
            raise ValueError("Role title is required")
        if not db.session.get(Department, d.get("department_id")):
            raise ValueError("Valid department is required")
        role = Role(title=title, department_id=d["department_id"],
                    salary_level=int(num(d.get("salary_level"), "salary_level", 1, 5)),
                    base_salary=num(d.get("base_salary"), "base_salary", 0, 10_000_000))
    except ValueError as exc:
        return err(str(exc))
    db.session.add(role)
    db.session.commit()
    return jsonify({"role": role.to_dict()}), 201


@bp.put("/roles/<int:role_id>")
@hr_required
def update_role(user, role_id):
    role = db.session.get(Role, role_id)
    if not role:
        return err("Role not found", 404)
    d = request.get_json(silent=True) or {}
    try:
        if "title" in d and len(d["title"].strip()) >= 2:
            role.title = d["title"].strip()
        if "department_id" in d and db.session.get(Department, d["department_id"]):
            role.department_id = d["department_id"]
        if "salary_level" in d:
            role.salary_level = int(num(d["salary_level"], "salary_level", 1, 5))
        if "base_salary" in d:
            role.base_salary = num(d["base_salary"], "base_salary", 0, 10_000_000)
    except ValueError as exc:
        return err(str(exc))
    db.session.commit()
    return jsonify({"role": role.to_dict()})


@bp.delete("/roles/<int:role_id>")
@hr_required
def delete_role(user, role_id):
    role = db.session.get(Role, role_id)
    if not role:
        return err("Role not found", 404)
    if Employee.query.filter_by(role_id=role.id).count():
        return err("Reassign employees with this role first", 409)
    db.session.delete(role)
    db.session.commit()
    return jsonify({"message": "Role deleted"})
