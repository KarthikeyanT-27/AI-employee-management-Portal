import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
ROOT_DIR = BASE_DIR.parent
load_dotenv(BASE_DIR / ".env")


def _bool(name: str, default: bool) -> bool:
    return os.getenv(name, str(default)).strip().lower() in {"1", "true", "yes", "on"}


def _database_uri() -> str:
    """DATABASE_URL from the host. Accepts the common `postgres://` / `postgresql://` forms (Neon, Supabase, Render)
    and rewrites them for SQLAlchemy + psycopg2. Empty -> local SQLite file (development only)."""
    url = (os.getenv("DATABASE_URL") or "").strip()
    if not url:
        return "sqlite:///" + str(BASE_DIR / "employee_mgmt.db")
    if url.startswith("postgres://"):
        url = "postgresql://" + url[len("postgres://"):]
    if url.startswith("postgresql://"):
        url = "postgresql+psycopg2://" + url[len("postgresql://"):]
    return url


ENV = os.getenv("APP_ENV", "development").lower()          # development | production
IS_PROD = ENV == "production"


class Config:
    ENV = ENV
    IS_PROD = IS_PROD
    SECRET_KEY = os.getenv("SECRET_KEY", "")
    SQLALCHEMY_DATABASE_URI = _database_uri()
    # pool_pre_ping + pool_recycle: serverless Postgres (Neon) closes idle connections after a few minutes
    SQLALCHEMY_ENGINE_OPTIONS = {"pool_pre_ping": True, "pool_recycle": 280, "pool_size": 5, "max_overflow": 5} \
        if not SQLALCHEMY_DATABASE_URI.startswith("sqlite") else {"pool_pre_ping": True}
    SQLALCHEMY_TRACK_MODIFICATIONS = False

    SESSION_COOKIE_NAME = "emp_session"
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    SESSION_COOKIE_SECURE = _bool("SESSION_COOKIE_SECURE", IS_PROD)   # cookies only over HTTPS in production
    PERMANENT_SESSION_LIFETIME = 60 * 60 * 8  # 8 hours
    SEND_FILE_MAX_AGE_DEFAULT = 300
    TRUST_PROXY = _bool("TRUST_PROXY", IS_PROD)       # Render/Netlify terminate TLS in front of the app

    FRONTEND_DIR = str(ROOT_DIR / "Frontend")
    MODEL_PATH = os.getenv("MODEL_PATH", str(ROOT_DIR / "ML" / "attrition_rf.joblib"))
    MODEL_META_PATH = os.getenv("MODEL_META_PATH", str(ROOT_DIR / "ML" / "model_meta.json"))

    # ---- Generative AI (Groq has a free tier; any OpenAI-compatible endpoint works)
    LLM_PROVIDER = os.getenv(
        "LLM_PROVIDER",
        "groq" if os.getenv("GROQ_API_KEY") else (
            "grok" if os.getenv("GROK_API_KEY") or os.getenv("XAI_API_KEY") else "anthropic"
        ),
    ).lower()
    if LLM_PROVIDER == "groq":
        LLM_API_KEY = os.getenv("GROQ_API_KEY") or os.getenv("GROK_API_KEY") or os.getenv("LLM_API_KEY") or ""
    elif LLM_PROVIDER in {"grok", "xai"}:
        LLM_API_KEY = os.getenv("GROK_API_KEY") or os.getenv("XAI_API_KEY") or os.getenv("LLM_API_KEY") or ""
    else:
        LLM_API_KEY = os.getenv("LLM_API_KEY") or os.getenv("ANTHROPIC_API_KEY") or os.getenv("OPENAI_API_KEY") or ""
    LLM_MODEL = os.getenv(
        "LLM_MODEL",
        "openai/gpt-oss-120b" if LLM_PROVIDER == "groq"
        else ("grok-3-mini" if LLM_PROVIDER in {"grok", "xai"} else "claude-sonnet-5-5"),
    )
    LLM_BASE_URL = os.getenv(
        "LLM_BASE_URL",
        "https://api.groq.com/openai/v1" if LLM_PROVIDER == "groq"
        else ("https://api.x.ai/v1" if LLM_PROVIDER in {"grok", "xai"} else "https://api.openai.com/v1"),
    )

    # ---- Google sign-in for employees (free)
    GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID", "")
    GOOGLE_CLIENT_SECRET = os.getenv("GOOGLE_CLIENT_SECRET", "")
    GOOGLE_REDIRECT_URI = os.getenv("GOOGLE_REDIRECT_URI", "http://127.0.0.1:5000/api/auth/google/callback")

    # ---- first administrator (created automatically on first start)
    ADMIN_EMAIL = os.getenv("ADMIN_EMAIL", "karthiksai9865@gmail.com").strip().lower()
    ADMIN_NAME = os.getenv("ADMIN_NAME", "Administrator").strip() or "Administrator"
    ADMIN_INITIAL_PASSWORD = os.getenv("ADMIN_INITIAL_PASSWORD", "")

    # ---- data bootstrap
    SEED_STARTER_ORG = _bool("SEED_STARTER_ORG", True)    # default departments + roles (editable in the UI)
    SEED_DEMO_DATA = _bool("SEED_DEMO_DATA", False)       # fake employees: development only, never in production

    # ---- payroll rules
    PF_RATE = 0.12
    TAX_THRESHOLD = 50000   # monthly base above which flat tax applies
    TAX_RATE = 0.05
    OVERTIME_MULTIPLIER = 1.5
    STANDARD_HOURS = 8
    # ---- attendance rules
    LATE_AFTER = "09:30"
    UNDO_WINDOW_MINUTES = 30      # employee may undo an accidental check-out within this window
    CORRECTION_MAX_DAYS = 14      # how far back an employee may request a correction
    MAX_WORK_HOURS = 16

    # ---- realtime notifications
    NOTIFICATION_KEEP_DAYS = 60
    SSE_HEARTBEAT_SECONDS = 20


def validate_production(cfg) -> None:
    """Refuse to start in production with unsafe settings."""
    problems = []
    if len(cfg.get("SECRET_KEY") or "") < 32:
        problems.append("SECRET_KEY must be set to a random string of at least 32 characters")
    if cfg.get("SEED_DEMO_DATA"):
        problems.append("SEED_DEMO_DATA must be false in production")
    if cfg["SQLALCHEMY_DATABASE_URI"].startswith("sqlite"):
        problems.append("DATABASE_URL must point to a persistent PostgreSQL database "
                        "(free hosts erase local SQLite files on every restart)")
    if problems:
        raise RuntimeError("Unsafe production configuration:\n - " + "\n - ".join(problems))
