"""First-start bootstrap.

PRODUCTION  : creates ONLY (1) the first administrator and (2) a starter list of departments/roles that the
              administrator can edit. There are no demo users and no default passwords anywhere in the code.
DEVELOPMENT : set SEED_DEMO_DATA=true to also generate 36 fake employees with ~100 days of attendance, leave and
              payroll, which makes the ML/GenAI screens look realistic on a laptop. Refused in production.

The administrator is the account ADMIN_EMAIL (default: karthiksai9865@gmail.com). Its first password is taken from
ADMIN_INITIAL_PASSWORD; if that is empty a random one-time password is generated and printed ONCE in the server log.
Either way the account is flagged "must change password", so the first login forces a personal password.
"""
import random
import secrets
from datetime import date, time, timedelta

from models import Attendance, Department, Employee, LeaveRequest, Role, db
from timeutil import today_local
from utils import check_password_strength

# Only used when SEED_DEMO_DATA=true (development). Never active in production.
# Development demo accounts get ONE random password per run (printed once in the console) - nothing is hard-coded.
DEMO_HR_EMAIL = "demo.hr@example.test"
DEMO_PASSWORD = secrets.token_urlsafe(9) + "aA1"
EMP_PASSWORD = DEMO_PASSWORD

ROLES = {
    "Engineering": [("Junior Developer", 1, 30000), ("Software Engineer", 2, 50000),
                    ("Senior Engineer", 4, 95000), ("Engineering Manager", 5, 150000)],
    "Human Resources": [("HR Executive", 2, 35000), ("HR Manager", 4, 80000)],
    "Finance": [("Accountant", 2, 38000), ("Finance Manager", 4, 90000)],
    "Sales": [("Sales Executive", 1, 28000), ("Sales Manager", 4, 85000)],
    "Operations": [("Operations Associate", 1, 26000), ("Operations Lead", 3, 60000)],
    "Support": [("Support Agent", 1, 24000), ("Support Lead", 3, 55000)],
}
FIRST = ["Arun", "Divya", "Karthik", "Meena", "Suresh", "Priya", "Vignesh", "Lakshmi", "Rahul", "Anitha",
         "Naveen", "Kavya", "Manoj", "Deepa", "Harish", "Swathi", "Prakash", "Nithya", "Sathish", "Revathi",
         "Gokul", "Pooja", "Dinesh", "Shalini", "Bharath", "Janani", "Ashwin", "Keerthi", "Mohan", "Sneha",
         "Vishnu", "Ramya", "Tarun", "Bhavana", "Ajay", "Yamini"]
LAST = ["Kumar", "Raj", "Iyer", "Nair", "Sharma", "Reddy", "Pillai", "Menon", "Das", "Patel"]


def _weekdays(start, end):
    d = start
    while d <= end:
        if d.weekday() < 5:
            yield d
        d += timedelta(days=1)


def create_admin(app, email: str, name: str, password: str | None = None):
    """Create an administrator. Returns (employee, password_used, was_generated)."""
    generated = False
    if password and check_password_strength(password, email):
        app.logger.error("The supplied admin password is too weak (%s); generating a random one instead.",
                         check_password_strength(password, email))
        password = None
    if not password:
        password, generated = secrets.token_urlsafe(12) + "aA1", True
    n = 1
    while Employee.query.filter_by(emp_code=f"ADM{n:04d}").first():
        n += 1
    admin = Employee(name=name, email=email, account_type="admin", emp_code=f"ADM{n:04d}",
                     join_date=today_local(), salary=0, job_satisfaction=8, must_change_password=True)
    admin.set_password(password)
    db.session.add(admin)
    db.session.commit()
    return admin, password, generated


def ensure_admin(app):
    cfg = app.config
    email = cfg["ADMIN_EMAIL"]
    if not email:
        return
    existing = Employee.query.filter_by(email=email).first()
    if existing:
        if existing.account_type != "admin":
            app.logger.warning("%s exists but is not an administrator; leaving it unchanged. "
                               "Use `python manage.py create-admin` or the dashboard.", email)
        return
    admin, password, generated = create_admin(app, email, cfg["ADMIN_NAME"], cfg["ADMIN_INITIAL_PASSWORD"])
    if generated:
        app.logger.warning("=" * 70 + "\nFIRST-RUN ADMIN CREATED: %s\nOne-time password (shown once, must be "
                           "changed at first login): %s\n" + "=" * 70, email, password)
    else:
        app.logger.warning("First-run administrator created: %s (password from ADMIN_INITIAL_PASSWORD; "
                           "it must be changed at first login).", email)


def ensure_seed(app):
    ensure_admin(app)
    if app.config["SEED_STARTER_ORG"] and Department.query.count() == 0:
        for dept_name, roles in ROLES.items():
            dept = Department(name=dept_name)
            db.session.add(dept)
            db.session.flush()
            for title, level, salary in roles:
                db.session.add(Role(title=title, department_id=dept.id, salary_level=level, base_salary=salary))
        db.session.commit()
    if app.config["SEED_DEMO_DATA"]:
        if app.config.get("IS_PROD"):
            raise RuntimeError("SEED_DEMO_DATA must not be enabled in production")
        if not Employee.query.filter_by(account_type="hr").first():
            hr = Employee(name="Hema HR", email=DEMO_HR_EMAIL, account_type="hr", emp_code="EMP0001H",
                          join_date=today_local() - timedelta(days=700), salary=0, job_satisfaction=8)
            hr.set_password(DEMO_PASSWORD)
            app.logger.warning("DEMO accounts (development only): %s and every demo employee use password %s", DEMO_HR_EMAIL, DEMO_PASSWORD)
            db.session.add(hr)
            db.session.commit()
        if Employee.query.filter_by(account_type="employee").count() == 0:
            seed_demo(app)


def seed_demo(app):
    from routes.payroll import generate_for
    rng = random.Random(2026)
    today = today_local()
    yesterday = today - timedelta(days=1)
    roles = Role.query.all()
    used = set()
    employees = []
    for i in range(36):
        while True:
            name = f"{FIRST[i]} {rng.choice(LAST)}"
            email = f"{name.lower().replace(' ', '.')}@company.com"
            if email not in used:
                used.add(email)
                break
        role = rng.choice(roles)
        d = min(1.0, max(0.0, rng.betavariate(1.3, 1.7)))        # disengagement 0..1
        emp = Employee(
            name=name, email=email, account_type="employee", emp_code=f"EMP{i + 1:04d}",
            phone=f"+91 9{rng.randint(100000000, 999999999)}", address="Chennai, Tamil Nadu",
            department_id=role.department_id, role_id=role.id,
            join_date=today - timedelta(days=rng.randint(120, 2300)),
            prior_experience_years=round(rng.uniform(0, 6), 1),
            salary=round(role.base_salary * rng.uniform(0.95, 1.1), -2),
            job_satisfaction=round(min(10, max(1, 8.6 - 5.2 * d + rng.gauss(0, 0.9))), 1),
            performance_rating=round(min(5, max(1, 4.5 - 2.2 * d + rng.gauss(0, 0.5))), 1))
        emp.set_password(EMP_PASSWORD)
        db.session.add(emp)
        employees.append((emp, d))
    db.session.flush()

    window_start = today - timedelta(days=100)
    for emp, d in employees:
        # approved leaves spread over the last year (more when disengaged)
        leave_days = set()
        for _ in range(int(round(rng.gauss(2 + 9 * d, 1.2)))):
            start = today - timedelta(days=rng.randint(3, 330))
            length = rng.choice([1, 1, 2, 3])
            end = start + timedelta(days=length - 1)
            if start < emp.join_date or end >= today:
                continue
            if any(l.start_date <= end and l.end_date >= start for l in LeaveRequest.query.filter_by(employee_id=emp.id)):
                continue
            db.session.add(LeaveRequest(employee_id=emp.id, start_date=start, end_date=end, status="Approved",
                                        leave_type=rng.choice(["Casual", "Sick", "Earned"]), reason="Personal"))
            leave_days.update(_weekdays(start, end))
        if rng.random() < 0.15:  # a pending request in the future
            s = today + timedelta(days=rng.randint(5, 25))
            db.session.add(LeaveRequest(employee_id=emp.id, start_date=s, end_date=s, leave_type="Casual",
                                        reason="Family function", status="Pending"))
        db.session.flush()

        for day in _weekdays(max(window_start, emp.join_date), yesterday):
            if day in leave_days:
                db.session.add(Attendance(employee_id=emp.id, date=day, status="Leave"))
                continue
            if rng.random() < 0.01 + 0.13 * d:
                db.session.add(Attendance(employee_id=emp.id, date=day, status="Absent"))
                continue
            late = rng.random() < 0.05 + 0.22 * d
            cin = time(9, rng.randint(35, 59)) if late else time(rng.choice([8, 9]), rng.randint(0, 25))
            ot = round(rng.uniform(0.3, 0.6 + 2.4 * d), 2) if rng.random() < 0.25 + 0.5 * d else 0.0
            total_minutes = int((8 + ot) * 60) + (cin.hour * 60 + cin.minute) % 7
            end_min = min(cin.hour * 60 + cin.minute + total_minutes, 23 * 60 + 30)
            cout = time(end_min // 60, end_min % 60)
            real_ot = round(max((end_min - (cin.hour * 60 + cin.minute)) / 60 - 8, 0), 2)
            db.session.add(Attendance(employee_id=emp.id, date=day, check_in=cin, check_out=cout,
                                      status="Late" if late else "Present", overtime_hours=real_ot))
    db.session.commit()

    # last two months of payroll so reports and payslips are populated
    first_this = today.replace(day=1)
    prev1 = (first_this - timedelta(days=1)).replace(day=1)
    prev2 = (prev1 - timedelta(days=1)).replace(day=1)
    for m in (prev2, prev1, first_this):
        generate_for([emp for emp, _ in employees], m.strftime("%Y-%m"))
    db.session.commit()
    app.logger.warning("DEMO data seeded (development only): 36 employees.")


if __name__ == "__main__":
    from app import create_app
    create_app()
    print("Seed complete.")
