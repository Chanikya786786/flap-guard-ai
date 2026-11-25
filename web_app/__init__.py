"""
FlapGuard AI Flask Application Factory.
Hardened for Neon Serverless PostgreSQL and local SQLite with empty-start ward support.
"""

import os
import secrets
from typing import Optional

from flask import Flask
from flask_login import LoginManager

from ml_engine.model_service import load_model_and_metadata
from web_app.models import ModelVersionLog, User, db
from web_app.ward_service import load_sample_ward_cases


login_manager = LoginManager()
login_manager.login_view = "main.login"
login_manager.login_message_category = "warning"


@login_manager.user_loader
def load_user(user_id: str) -> Optional[User]:
    try:
        return db.session.get(User, int(user_id))
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


def seed_initial_data(include_sample_patients: bool = False) -> None:
    """
    Initialize starter clinician accounts and baseline model log once.
    Patients are NOT auto-seeded by default; use Profile -> Load Sample Cases instead.
    """
    if User.query.first() is None:
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

    app.config["SECRET_KEY"] = _get_or_create_secret_key(app.instance_path)
    app.config["SQLALCHEMY_DATABASE_URI"] = db_uri
    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
    app.config["MAX_CONTENT_LENGTH"] = 8 * 1024 * 1024
    app.config["SESSION_COOKIE_HTTPONLY"] = True
    app.config["SESSION_COOKIE_SAMESITE"] = "Lax"

    if test_config:
        app.config.update(test_config)

    # Apply resilient pooling for remote PostgreSQL (Neon / Render) without breaking SQLite tests
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

    from web_app.routes import bp as main_bp

    app.register_blueprint(main_bp)

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
        return response

    with app.app_context():
        db.create_all()
        if not app.config.get("SKIP_SEED"):
            seed_patients = bool(
                app.config.get("SEED_SAMPLE_PATIENTS")
                or os.environ.get("SEED_SAMPLE_PATIENTS", "").lower() == "true"
            )
            seed_initial_data(include_sample_patients=seed_patients)

    return app