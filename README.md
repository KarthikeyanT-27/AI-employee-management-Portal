# AI-Enhanced Employee Management Application

A production-ready, full-stack workforce management system with two AI modules and real-time notifications:

* **Machine Learning** - a Random Forest classifier predicts each employee's attrition risk (**Low / Moderate / High**) from six features computed from live records.
* **Generative AI** - an LLM API writes employee performance summaries, attendance analysis, payroll/productivity observations and HR insights from live data.
* **Real-time notifications** - leave, attendance-correction, payroll and attrition events appear instantly for the right people.

Runs **completely free**: Render (web service) + Neon (PostgreSQL) + Groq (LLM) + optional Netlify and Google sign-in. **Deployment guide: [DEPLOYMENT.md](DEPLOYMENT.md).**
The assignment brief is in the `Screenshot ...png` files in this folder.

| Layer | Technology |
|---|---|
| Frontend | HTML, CSS, JavaScript (no framework, no build step) |
| Backend | Python 3.12, Flask, gunicorn + gevent |
| Database | PostgreSQL (Neon free tier) - SQLite for local development |
| Machine Learning | scikit-learn (RandomForestClassifier), joblib |
| Generative AI | LLM API - Groq (free tier) by default; xAI, Anthropic or any OpenAI-compatible endpoint |
| Real time | Server-Sent Events (no extra service) with polling fallback |

## Project structure

```
Karthikeyan_AI_Employee_Management/
├── Frontend/                  HTML, CSS, JS
│   ├── login.html             employee login + HR/Admin login (no self-registration, no demo credentials)
│   ├── employee.html          employee dashboard
│   ├── hr.html                HR/Admin dashboard
│   ├── css/style.css
│   └── js/ (api.js, notify.js, login.js, employee.js, hr.js)
├── Backend/                   APIs + ML + GenAI integration
│   ├── app.py, wsgi.py        Flask app factory / production entry point
│   ├── config.py, models.py, utils.py, seed.py, timeutil.py, manage.py
│   ├── routes/                auth, employees, attendance (+leaves), payroll, analytics, notifications
│   ├── services/              features.py, ml_service.py, genai_service.py, notifications.py
│   ├── tests/                 16 automated tests (pytest)
│   ├── requirements.txt
│   └── .env.example
├── ML/                        training code, dataset, saved model, notebook
├── render.yaml, netlify.toml  free-hosting configuration
├── DEPLOYMENT.md              step-by-step free deployment
├── Project_Documentation.docx
└── README.md
```

## Run locally

```bash
cd Backend
python -m venv .venv
source .venv/bin/activate            # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env                 # Windows: copy .env.example .env
#   edit .env: set ADMIN_INITIAL_PASSWORD (and optionally GROQ_API_KEY)
python app.py                        # http://127.0.0.1:5000
```

On first start the database tables are created, a starter list of departments/roles is added, and the administrator
account **karthiksai9865@gmail.com** (change with `ADMIN_EMAIL`) is created with the password from `ADMIN_INITIAL_PASSWORD`
(if empty, a random one-time password is printed once in the console). The first login forces a new password.
**There are no demo accounts.** Sign in on the **HR / Admin** tab, then create HR and employee accounts from the dashboard.
Each new account gets a temporary password that must be replaced at first login.

For realistic sample data on a laptop only, set `SEED_DEMO_DATA=true` (refused when `APP_ENV=production`).
Run the tests with `cd Backend && pytest tests -q` (set `TEST_DATABASE_URL` to also run them on PostgreSQL).

### Enabling Generative AI
Set `GROQ_API_KEY` (free at console.groq.com). Defaults: model `openai/gpt-oss-120b`, endpoint `https://api.groq.com/openai/v1`;
override with `LLM_PROVIDER`, `LLM_MODEL`, `LLM_BASE_URL`. Without a key the AI sections show a clearly labelled **local fallback**
composed from the same live data (not an LLM response).

### Employee Google sign-in (optional)
HR creates the employee; the employee can then use **Sign in with Google** when the verified Google email matches an active
record. Set `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET` and `GOOGLE_REDIRECT_URI` (must equal the URI added in Google Cloud Console).
The button only appears when these are configured. HR/Admin accounts always use a password.

### Retraining the model
```bash
cd ML
python generate_dataset.py     # optional: rebuild the dataset (or use your own CSV with the same columns)
python train_model.py          # trains, evaluates, writes attrition_rf.joblib + model_meta.json
```
Keep the scikit-learn version equal to `requirements.txt` (a joblib file is tied to the version that created it).

## How the AI modules work (nothing is hard-coded)

**Attrition prediction.** `services/features.py` computes, per employee, from the database:
`attendance_pct` (last 60 days, approved leave excluded), `salary_level` (role, 1-5), `overtime_hours` (monthly average, last 90 days),
`years_experience` (prior + tenure), `job_satisfaction` (1-10) and `leave_frequency` (approved leaves, last 12 months).
These go through the saved pipeline; the label and class probabilities are stored in `attrition_predictions` and shown on the HR dashboard.

**Generative AI.** `services/genai_service.py` sends aggregated, structured facts (plus the ML prediction for HR reports) to the LLM and asks for four sections: performance summary, attendance analysis, payroll & productivity, HR insights. The HR Overview page generates a company-wide brief.

**Real-time notifications.** `services/notifications.py` saves a notification in the same database transaction as the change, and after the commit pushes it to the user's open tabs over Server-Sent Events (`/api/notifications/stream`). Offline users see it in the bell later; if SSE is blocked the browser polls every 15 s.

## Features by module

* **Accounts and login** - no public registration. HR creates employee accounts; only an administrator creates HR/admin accounts. Separate employee and HR/Admin login tabs, hashed passwords, password policy (8+ characters, letter + number), forced change of temporary passwords at first login, lock-out after 5 failed attempts (15 min, stored in the database), server-side sessions and role checks on every API.
* **Employee profile management** - employees edit contact details and satisfaction score; HR edits everything.
* **Attendance & leave** - check-in/out (late after 09:30, overtime beyond 8 h), leave requests with HR approval.
* **Wrong or forgotten check-out** - the employee can *undo* an accidental check-out for 30 minutes; after that, or if the check-out was forgotten, they file a *correction request* (date, time, reason) that HR approves or rejects. Forgotten check-outs are flagged to the employee and listed for HR; those days still count as present, and overtime is added when the correction is approved. HR can also set a check-out directly. Correction requests and decisions are retained in an HR audit history.
* **Payroll** - monthly generation: loss-of-pay, 1.5x overtime, bonus, PF 12%, 5% tax above ₹50,000; payslips for employees.
* **Departments & roles** - CRUD; roles carry the salary level used by the ML model.
* **Employee dashboard** - today's attendance, 30-day stats, latest pay, AI summary.
* **Real-time notifications** - bell with unread badge, toast pop-ups and optional desktop alerts, delivered live over Server-Sent Events (automatic polling fallback). Leave and correction requests alert HR; decisions, attendance edits and payslips alert the employee; attrition refreshes alert HR. Open dashboards refresh themselves when check-ins or decisions happen.
* **HR reports & records** - overview statistics, CSV exports (employees, attendance, payroll, attrition).

## Main API endpoints (all under `/api`)

| Area | Endpoints |
|---|---|
| Auth | `POST /auth/login`, `/auth/logout`, `/auth/change-password`; `GET /auth/me`, `/auth/google`, `/auth/google/callback` (no public registration endpoint) |
| Employees | `GET/POST /employees` (HR: employees only; admin: any type), `GET/PUT/DELETE /employees/<id>`, `PUT /profile`, `POST/GET /satisfaction` |
| Org | `/departments`, `/roles` (CRUD) |
| Attendance | `POST /attendance/check-in`, `/attendance/check-out`, `/attendance/undo-checkout`, `/attendance/mark`; `GET /attendance`, `/attendance/me`, `/attendance/missed`, `/attendance/audit` |
| Corrections | `POST /attendance/corrections`, `GET /attendance/corrections/me`; HR: `GET /attendance/corrections`, `PUT /attendance/corrections/<id>/decision` |
| Leave | `POST /leaves`, `GET /leaves`, `/leaves/me`, `PUT /leaves/<id>/decision` |
| Payroll | `POST /payroll/generate`, `GET /payroll`, `/payroll/me` |
| ML | `POST /ml/predict/<id>`, `/ml/predict-all`; `GET /ml/predictions`, `/ml/model-info` |
| GenAI | `POST /genai/employee/<id>`, `/genai/hr-insights`; `GET /genai/me`, `/genai/status` |
| Notifications | `GET /notifications`, `/notifications/stream` (SSE); `POST /notifications/<id>/read`, `/notifications/read-all` |
| Reports | `GET /reports/summary`, `/reports/export/<employees|attendance|payroll|attrition>` |

## Notes and limitations

* **Dataset.** No real HR data is available, so `employee_attrition.csv` is synthetic: it is generated from documented relationships (low satisfaction, low pay, heavy overtime, poor attendance and frequent leave raise risk; experience lowers it) plus noise. The reported accuracy (~74% test, ~78% 5-fold CV) describes how well the model learns those relationships, not real-world attrition forecasting. Replace the CSV with a real dataset to get meaningful predictions.
## Performance

The dashboards were profiled with 400 employees (28,800 attendance rows). The slow paths were one-query-per-employee loops; they were replaced with grouped SQL, eager loading and one batched model call. Results are identical before and after.

| Action | Before | After |
|---|---|---|
| Run attrition predictions (all employees) | 7.5 s (2,436 SQL statements) | 0.07-0.15 s |
| HR overview statistics | 0.58 s (812 statements) | 0.04 s (9 statements) |
| Generate payroll (all employees) | 0.88 s (2,402 statements) | 0.20 s |
| Attendance list / leave list / predictions | 96 / 30 / 112 ms | 21 / 15 / 13 ms |

Other measures: database indexes, SQLite WAL mode, debounced search box, and a 10-minute cache for identical LLM requests (re-opening a report is instant; changed data always produces a new request).

## Notes and limitations

* Attrition predictions are visible to HR only; employees see only their own performance/attendance summary.
* Free-text fields (reasons, addresses) are never sent to the LLM; all UI rendering escapes server data; CSV exports neutralise spreadsheet formulas.
* Production setup (HTTPS, strong secret, Secure cookies, persistent lock-out, no demo data) is built in and enforced at start-up in `APP_ENV=production`; see **DEPLOYMENT.md**. The live-notification broker is in-process, so run **one** server process (the free plan does); to scale to several workers replace it with PostgreSQL `LISTEN/NOTIFY` or Redis pub/sub.
