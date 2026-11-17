"""Administration helpers. Run from your own computer against the production database, e.g.

    set DATABASE_URL=postgresql://...neon...      (Windows: set / PowerShell: $env:DATABASE_URL="...")
    python manage.py list-admins
    python manage.py create-admin --email someone@example.com --name "Jane Doe"
    python manage.py reset-password --email karthiksai9865@gmail.com

Needed because the free hosting tier offers no server shell. Passwords are never printed except the one-time
temporary password generated here; the user must change it at the next login.
"""
import argparse
import os
import secrets
import sys

os.environ.setdefault("APP_ENV", "development")     # skip the "no SQLite in production" guard for CLI use


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list-admins")
    c = sub.add_parser("create-admin")
    c.add_argument("--email", required=True)
    c.add_argument("--name", default="Administrator")
    r = sub.add_parser("reset-password")
    r.add_argument("--email", required=True)
    r.add_argument("--password", help="optional; a random temporary password is generated if omitted")
    args = ap.parse_args()

    from app import create_app
    from models import Employee, db
    from seed import create_admin
    from utils import check_password_strength

    app = create_app({"ADMIN_EMAIL": ""})            # do not auto-create the default admin as a side effect
    with app.app_context():
        if args.cmd == "list-admins":
            for e in Employee.query.filter_by(account_type="admin").order_by(Employee.id):
                print(f"{e.email}\t{e.name}\t{e.status}")
        elif args.cmd == "create-admin":
            email = args.email.strip().lower()
            if Employee.query.filter_by(email=email).first():
                sys.exit(f"{email} already exists. Use reset-password, or promote it from the Employees page.")
            admin, pw, _ = create_admin(app, email, args.name)
            print(f"Created administrator {admin.email}\nTemporary password: {pw}\n(must be changed at first login)")
        elif args.cmd == "reset-password":
            email = args.email.strip().lower()
            emp = Employee.query.filter_by(email=email).first()
            if not emp:
                sys.exit(f"No account with email {email}")
            pw = args.password or secrets.token_urlsafe(12) + "aA1"
            problem = check_password_strength(pw, email)
            if problem:
                sys.exit(problem)
            emp.set_password(pw)
            emp.must_change_password = True
            emp.status = "active"
            from utils import throttle
            throttle.reset_all()                     # also clears any lock-out
            db.session.commit()
            print(f"Password reset for {email}.\nTemporary password: {pw}\n(must be changed at next login)")


if __name__ == "__main__":
    main()
