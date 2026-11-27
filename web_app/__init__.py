"""
FlapGuard AI Flask Application Factory.
Hardened for Neon Serverless PostgreSQL and local SQLite with safe schema migrations,
session CSRF protection, HTTPS transport headers, and JSON error handling for mobile clients.
"""

import os
import secrets
from typing import Optional

from flask import Flask, jsonify, request
from flask_login import LoginManager
from sqlalchemy import inspect, text

from ml_engine.model_service import load_model_and_metadata
from web_app.models import ModelVersionLog, User, db
from web_app.security import enforce_due_account_deletion, get_csrf_token, is_csrf_enabled, log_audit, purge_all_expired_accounts, validate_csrf_request
from web_app.clinical_workflow import compute_check_schedule, format_bedside_exam_summary
from web_app.model_store import restore_active_model_from_db
from web_app.ward_service import load_sample_ward_cases


login_manager = LoginManager()
login_manager.login_view = "main.login"
login_manager.login_message_category = "warning"


@login_manager.user_loader
def load_user(user_id: str) -> Optional[User]:
    try:
        user = db.session.get(User, int(user_id))
        if user is None or enforce_due_account_deletion(user) or user.scheduled_deletion_at is not None:
            return None
        return user
    except (TypeError, ValueError):
        return None


def _get_or_create_secret_key(instance_path: str) -> str:
    env_key = os.environ.get("SECRET_KEY")
    if env_key:
        return env_key

    os.makedirs(instance_path, exist_ok=True)
    key_file = os.path.join(instance_path, ".secret_key")
    if os.path.exists(key_file):
        with open(key_file, "r", encoding="utf-8") as fh:
            stored = fh.read().strip()
            if stored:
                return stored

    generated = secrets.token_hex(32)
    with open(key_file, "w", encoding="utf-8") as fh:
        fh.write(generated)
    return generated


def _resolve_database_uri(base_dir: str) -> str:
    env_url = os.environ.get("DATABASE_URL", "").strip()
    if env_url:
        if env_url.startswith("postgres://"):
            return env_url.replace("postgres://", "postgresql+psycopg2://", 1)
        if env_url.startswith("postgresql://"):
            return env_url.replace("postgresql://", "postgresql+psycopg2://", 1)
        return env_url
    db_path = os.path.join(base_dir, "flap_guard.db")
    return f"sqlite:///{db_path}"


def _run_safe_schema_migrations() -> None:
    """Safely upgrade existing Neon PostgreSQL / SQLite tables without data loss."""
    try:
        inspector = inspect(db.engine)
        tables = set(inspector.get_table_names())
        dialect = db.engine.dialect.name

        if "users" in tables:
            user_cols = {c["name"] for c in inspector.get_columns("users")}
            if "profile_photo" not in user_cols:
                db.session.execute(text("ALTER TABLE users ADD COLUMN profile_photo TEXT"))
                db.session.commit()
            if "token_version" not in user_cols:
                db.session.execute(
                    text("ALTER TABLE users ADD COLUMN token_version INTEGER NOT NULL DEFAULT 1")
                )
                db.session.commit()
            if "deletion_requested_at" not in user_cols:
                db.session.execute(text("ALTER TABLE users ADD COLUMN deletion_requested_at TIMESTAMP"))
                db.session.commit()
            if "scheduled_deletion_at" not in user_cols:
                db.session.execute(text("ALTER TABLE users ADD COLUMN scheduled_deletion_at TIMESTAMP"))
                db.session.commit()
            if "is_deleted" not in user_cols:
                if dialect == "postgresql":
                    db.session.execute(
                        text("ALTER TABLE users ADD COLUMN is_deleted BOOLEAN NOT NULL DEFAULT FALSE")
                    )
                else:
                    db.session.execute(
                        text("ALTER TABLE users ADD COLUMN is_deleted BOOLEAN NOT NULL DEFAULT 0")
                    )
                db.session.commit()

        if "patients" in tables:
            patient_cols = {c["name"] for c in inspector.get_columns("patients")}
            if "is_archived" not in patient_cols:
                if dialect == "postgresql":
                    db.session.execute(
                        text("ALTER TABLE patients ADD COLUMN is_archived BOOLEAN NOT NULL DEFAULT FALSE")
                    )
                else:
                    db.session.execute(
                        text("ALTER TABLE patients ADD COLUMN is_archived BOOLEAN NOT NULL DEFAULT 0")
                    )
                db.session.commit()
            if "archived_at" not in patient_cols:
                db.session.execute(text("ALTER TABLE patients ADD COLUMN archived_at TIMESTAMP"))
                db.session.commit()
            if "archived_by_id" not in patient_cols:
                db.session.execute(text("ALTER TABLE patients ADD COLUMN archived_by_id INTEGER"))
                db.session.commit()

        if "sensor_readings" in tables:
            sr_cols = {c["name"] for c in inspector.get_columns("sensor_readings")}
            if "trend_status" not in sr_cols:
                db.session.execute(
                    text("ALTER TABLE sensor_readings ADD COLUMN trend_status VARCHAR(40) NOT NULL DEFAULT 'Baseline Established'")
                )
                db.session.commit()
            if "ood_flag" not in sr_cols:
                if dialect == "postgresql":
                    db.session.execute(
                        text("ALTER TABLE sensor_readings ADD COLUMN ood_flag BOOLEAN NOT NULL DEFAULT FALSE")
                    )
                else:
                    db.session.execute(
                        text("ALTER TABLE sensor_readings ADD COLUMN ood_flag BOOLEAN NOT NULL DEFAULT 0")
                    )
                db.session.commit()
            if "confidence_label" not in sr_cols:
                db.session.execute(
                    text("ALTER TABLE sensor_readings ADD COLUMN confidence_label VARCHAR(40) NOT NULL DEFAULT 'High'")
                )
                db.session.commit()
            if "trend_json" not in sr_cols:
                db.session.execute(
                    text("ALTER TABLE sensor_readings ADD COLUMN trend_json TEXT NOT NULL DEFAULT '{}'")
                )
                db.session.commit()
            for exam_col in ("capillary_refill", "flap_turgor", "pinprick_bleeding", "doppler_quality"):
                if exam_col not in sr_cols:
                    db.session.execute(
                        text(f"ALTER TABLE sensor_readings ADD COLUMN {exam_col} VARCHAR(40)")
                    )
                    db.session.commit()

        if "sensor_readings" in tables and dialect == "postgresql":
            cols = {c["name"]: c for c in inspector.get_columns("sensor_readings")}
            sm_col = cols.get("source_mode")
            col_len = getattr(sm_col["type"], "length", None) if sm_col else None
            if col_len is not None and col_len < 80:
                db.session.execute(
                    text("ALTER TABLE sensor_readings ALTER COLUMN source_mode TYPE VARCHAR(80)")
                )
                db.session.commit()
    except Exception:
        db.session.rollback()


def seed_initial_data(include_sample_patients: bool = False) -> None:
    """
    Initialize starter clinician accounts (unless SEED_DEFAULT_USERS=false)
    and baseline model log once. Patients remain empty by default.
    """
    seed_users = os.environ.get("SEED_DEFAULT_USERS", "true").strip().lower() != "false"
    if seed_users and User.query.first() is None:
        surgeon = User(
            full_name="Dr. Alden Schnyder",
            email="surgeon@flapguard.local",
            role="Surgeon",
        )
        surgeon.set_password("Surgeon@123")

        nurse = User(
            full_name="Nurse Priya Nair",
            email="nurse@flapguard.local",
            role="Nurse",
        )
        nurse.set_password("Nurse@123")

        db.session.add_all([surgeon, nurse])
        db.session.commit()

    if ModelVersionLog.query.first() is None:
        _, meta = load_model_and_metadata()
        db.session.add(
            ModelVersionLog(
                version=meta["version"],
                accuracy=meta["accuracy"],
                macro_f1=meta["macro_f1"],
                total_samples=meta["total_samples"],
                postop_outcomes_added=meta["postop_outcomes_added"],
                trained_at_utc=meta["trained_at_utc"],
                notes="Baseline multisensor fusion Random Forest model",
            )
        )
        db.session.commit()

    if include_sample_patients:
        load_sample_ward_cases()


def create_app(test_config: Optional[dict] = None) -> Flask:
    base_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    app = Flask(
        __name__,
        template_folder=os.path.join(os.path.dirname(__file__), "templates"),
        static_folder=os.path.join(os.path.dirname(__file__), "static"),
    )

    db_uri = _resolve_database_uri(base_dir)
    is_https_prod = bool(os.environ.get("RENDER") or os.environ.get("HTTPS_ONLY", "").lower() == "true")

    app.config["SECRET_KEY"] = _get_or_create_secret_key(app.instance_path)
    app.config["SQLALCHEMY_DATABASE_URI"] = db_uri
    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
    app.config["MAX_CONTENT_LENGTH"] = 8 * 1024 * 1024
    app.config["SESSION_COOKIE_HTTPONLY"] = True
    app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
    app.config["SESSION_COOKIE_SECURE"] = is_https_prod

    if test_config:
        app.config.update(test_config)

    active_uri = app.config["SQLALCHEMY_DATABASE_URI"]
    if active_uri.startswith("postgresql"):
        app.config.setdefault(
            "SQLALCHEMY_ENGINE_OPTIONS",
            {
                "pool_pre_ping": True,
                "pool_recycle": 240,
                "pool_size": 3,
                "max_overflow": 2,
                "pool_timeout": 15,
            },
        )
    else:
        app.config.setdefault("SQLALCHEMY_ENGINE_OPTIONS", {"pool_pre_ping": True})

    db.init_app(app)
    login_manager.init_app(app)
    app.jinja_env.globals["csrf_token"] = get_csrf_token
    app.jinja_env.globals["get_check_schedule"] = compute_check_schedule
    app.jinja_env.globals["format_bedside_exam"] = format_bedside_exam_summary

    from web_app.routes import bp as main_bp

    app.register_blueprint(main_bp)

    @app.before_request
    def enforce_web_csrf():
        if request.method not in ("POST", "PUT", "PATCH", "DELETE"):
            return None
        if request.path.startswith("/mobile/"):
            return None
        if not is_csrf_enabled():
            return None
        if not validate_csrf_request():
            log_audit(
                "CSRF_REJECTED",
                resource_type="web_route",
                resource_id=request.path,
                details="Missing or invalid CSRF token on state-changing request",
            )
            if request.is_json or request.headers.get("X-Requested-With") == "XMLHttpRequest":
                return jsonify({"error": "Invalid or missing CSRF token."}), 403
            return "Forbidden: Invalid or missing CSRF token.", 403
        return None

    @app.errorhandler(500)
    def handle_internal_error(err):
        db.session.rollback()
        if request.path.startswith("/mobile/"):
            return jsonify({"error": "Server encountered an internal error. Please retry."}), 500
        return "Internal Server Error", 500

    @app.after_request
    def apply_security_headers(response):
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "SAMEORIGIN"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; "
            "img-src 'self' data: blob:; "
            "style-src 'self' 'unsafe-inline'; "
            "script-src 'self' 'unsafe-inline';"
        )
        if request.is_secure or request.headers.get("X-Forwarded-Proto") == "https":
            response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
        return response

    with app.app_context():
        db.create_all()
        _run_safe_schema_migrations()
        restore_active_model_from_db()
        purge_all_expired_accounts()
        if not app.config.get("SKIP_SEED"):
            seed_patients = bool(
                app.config.get("SEED_SAMPLE_PATIENTS")
                or os.environ.get("SEED_SAMPLE_PATIENTS", "").lower() == "true"
            )
            seed_initial_data(include_sample_patients=seed_patients)

    return app
