"""
FlapGuard AI Flask Application Factory.
"""

import json
import os
import secrets
from typing import Optional

from flask import Flask
from flask_login import LoginManager

from ml_engine.fusion import classify_visual_triage
from ml_engine.model_service import load_model_and_metadata, predict_flap_status
from web_app.models import ModelVersionLog, Patient, SensorReading, User, db


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
    env_url = os.environ.get("DATABASE_URL")
    if env_url:
        if env_url.startswith("postgres://"):
            return env_url.replace("postgres://", "postgresql://", 1)
        return env_url
    db_path = os.path.join(base_dir, "flap_guard.db")
    return f"sqlite:///{db_path}"


def seed_initial_data() -> None:
    """Populate realistic starter clinician accounts and 3 monitored patients once."""
    if User.query.first() is not None:
        return

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
    db.session.flush()

    starter_patients = [
        (
            Patient(
                mrn="MRN-2026-101",
                full_name="Ramesh Kumar",
                age=54,
                flap_type="Radial Forearm Free Flap",
                surgery_site="Left Buccal Mucosa",
                hours_post_op=10.0,
                assigned_surgeon_id=surgeon.id,
            ),
            dict(
                ir_flap_temp_c=36.3,
                control_skin_temp_c=36.7,
                spo2_pct=97.4,
                doppler_flow_cm_s=21.8,
                visual_redness_index=0.155,
                visual_cyanosis_index=0.022,
                visual_pallor_index=0.415,
                hours_post_op=10.0,
            ),
        ),
        (
            Patient(
                mrn="MRN-2026-102",
                full_name="Meena Devi",
                age=47,
                flap_type="Fibula Osteocutaneous Flap",
                surgery_site="Right Mandibular Reconstruction",
                hours_post_op=18.5,
                assigned_surgeon_id=surgeon.id,
            ),
            dict(
                ir_flap_temp_c=34.8,
                control_skin_temp_c=36.7,
                spo2_pct=81.5,
                doppler_flow_cm_s=9.4,
                visual_redness_index=0.102,
                visual_cyanosis_index=0.520,
                visual_pallor_index=0.275,
                hours_post_op=18.5,
            ),
        ),
        (
            Patient(
                mrn="MRN-2026-103",
                full_name="Karthik Sundaram",
                age=61,
                flap_type="Anterolateral Thigh (ALT) Flap",
                surgery_site="Anterior Maxilla",
                hours_post_op=6.0,
                assigned_surgeon_id=surgeon.id,
            ),
            dict(
                ir_flap_temp_c=36.2,
                control_skin_temp_c=36.6,
                spo2_pct=96.5,
                doppler_flow_cm_s=20.4,
                visual_redness_index=0.148,
                visual_cyanosis_index=0.028,
                visual_pallor_index=0.430,
                hours_post_op=6.0,
            ),
        ),
    ]

    for patient, params in starter_patients:
        db.session.add(patient)
        db.session.flush()
        pred = predict_flap_status(**params)
        feat = pred["features"]
        triage = classify_visual_triage(
            feat["visual_redness_index"],
            feat["visual_cyanosis_index"],
            feat["visual_pallor_index"],
            feat["visual_perfusion_score"],
        )
        reading = SensorReading(
            patient_id=patient.id,
            recorded_by_id=nurse.id,
            ir_flap_temp_c=feat["ir_flap_temp_c"],
            control_skin_temp_c=feat["control_skin_temp_c"],
            temp_delta_c=feat["temp_delta_c"],
            spo2_pct=feat["spo2_pct"],
            doppler_flow_cm_s=feat["doppler_flow_cm_s"],
            visual_redness_index=feat["visual_redness_index"],
            visual_cyanosis_index=feat["visual_cyanosis_index"],
            visual_pallor_index=feat["visual_pallor_index"],
            visual_perfusion_score=feat["visual_perfusion_score"],
            visual_triage_label=triage,
            perfusion_fusion_index=feat["perfusion_fusion_index"],
            hours_post_op=feat["hours_post_op"],
            predicted_class=pred["predicted_class"],
            risk_tier=pred["risk_tier"],
            risk_score_pct=pred["risk_score_pct"],
            compromise_probability=pred["compromise_probability"],
            alert_required=pred["alert_required"],
            alert_message=pred["alert_message"],
            clinical_notes_json=json.dumps(pred["clinical_notes"]),
            source_mode="Initial Ward Baseline",
            model_version=pred["model_version"],
        )
        db.session.add(reading)

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


def create_app(test_config: Optional[dict] = None) -> Flask:
    base_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    app = Flask(
        __name__,
        template_folder=os.path.join(os.path.dirname(__file__), "templates"),
        static_folder=os.path.join(os.path.dirname(__file__), "static"),
    )

    app.config["SECRET_KEY"] = _get_or_create_secret_key(app.instance_path)
    app.config["SQLALCHEMY_DATABASE_URI"] = _resolve_database_uri(base_dir)
    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
    app.config["MAX_CONTENT_LENGTH"] = 8 * 1024 * 1024
    app.config["SESSION_COOKIE_HTTPONLY"] = True
    app.config["SESSION_COOKIE_SAMESITE"] = "Lax"

    if test_config:
        app.config.update(test_config)

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
            seed_initial_data()

    return app