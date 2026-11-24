"""
Web Routes & Local Android Mobile Sync Routes for FlapGuard AI.
Zero third-party APIs: all authentication, ML inference, image analysis,
and database persistence execute strictly on this self-hosted server.
"""

import base64
import json
import re
from functools import wraps
from typing import Optional, Tuple

from flask import (
    Blueprint,
    current_app,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    url_for,
)
from flask_login import current_user, login_required, login_user, logout_user
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from sqlalchemy import text

from ml_engine.fusion import classify_visual_triage
from ml_engine.model_service import (
    load_model_and_metadata,
    predict_flap_status,
    retrain_from_postop_outcomes,
)
from ml_engine.simulator import SCENARIOS, generate_simulated_reading
from ml_engine.vision_analyzer import analyze_flap_image_bytes
from web_app.models import ModelVersionLog, Patient, PostOpOutcome, SensorReading, User, db


bp = Blueprint("main", __name__)

EMAIL_REGEX = re.compile(r"^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$")
VALID_ROLES = {"Surgeon", "Nurse", "Resident", "Admin"}
VALID_OUTCOMES = {"Normal", "Venous Compromise", "Arterial Compromise"}
RISK_PRIORITY = {"Critical": 0, "High": 1, "Moderate": 2, "Low": 3, "Unassessed": 4}


def _token_serializer() -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(current_app.config["SECRET_KEY"], salt="flapguard-mobile-auth")


def generate_mobile_token(user: User) -> str:
    return _token_serializer().dumps({"user_id": user.id, "email": user.email})


def verify_mobile_token(token: str) -> Optional[User]:
    try:
        payload = _token_serializer().loads(token, max_age=86400 * 7)
        return db.session.get(User, int(payload["user_id"]))
    except (BadSignature, SignatureExpired, KeyError, ValueError, TypeError):
        return None


def mobile_auth_required(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        auth_header = request.headers.get("Authorization", "")
        if not auth_header.startswith("Bearer "):
            return jsonify({"error": "Missing or invalid Bearer token."}), 401
        token = auth_header.split(" ", 1)[1].strip()
        user = verify_mobile_token(token)
        if user is None:
            return jsonify({"error": "Expired or invalid authentication token."}), 401
        request.mobile_user = user
        return fn(*args, **kwargs)

    return wrapper


def validate_sensor_inputs(
    ir_temp: float,
    control_temp: float,
    spo2: float,
    doppler: float,
    hours_post_op: float,
    redness: float = 0.14,
    cyanosis: float = 0.03,
    pallor: float = 0.42,
) -> Tuple[bool, str]:
    checks = [
        (20.0 <= ir_temp <= 43.0, "IR Flap Temperature must be between 20.0 and 43.0 C."),
        (30.0 <= control_temp <= 43.0, "Control Skin Temperature must be between 30.0 and 43.0 C."),
        (0.0 <= spo2 <= 100.0, "SpO2 must be between 0 and 100%."),
        (0.0 <= doppler <= 60.0, "Doppler flow must be between 0.0 and 60.0 cm/s."),
        (0.0 <= hours_post_op <= 720.0, "Hours post-op must be between 0 and 720."),
        (0.0 <= redness <= 1.0, "Visual redness index must be between 0.0 and 1.0."),
        (0.0 <= cyanosis <= 1.0, "Visual cyanosis index must be between 0.0 and 1.0."),
        (0.0 <= pallor <= 1.0, "Visual pallor index must be between 0.0 and 1.0."),
    ]
    for ok, msg in checks:
        if not ok:
            return False, msg
    return True, ""


def _save_prediction_reading(
    patient: Patient,
    recorded_by_id: Optional[int],
    pred: dict,
    source_mode: str,
) -> SensorReading:
    feat = pred["features"]
    triage = classify_visual_triage(
        feat["visual_redness_index"],
        feat["visual_cyanosis_index"],
        feat["visual_pallor_index"],
        feat["visual_perfusion_score"],
    )
    reading = SensorReading(
        patient_id=patient.id,
        recorded_by_id=recorded_by_id,
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
        source_mode=source_mode,
        model_version=pred["model_version"],
    )
    patient.hours_post_op = feat["hours_post_op"]
    db.session.add(reading)
    db.session.commit()
    return reading


def _collect_outcome_records_for_retraining() -> list:
    records = []
    outcomes = PostOpOutcome.query.order_by(PostOpOutcome.created_at.asc()).all()
    for out in outcomes:
        reading = out.reading or (out.patient.latest_reading if out.patient else None)
        if reading is None:
            continue
        records.append(
            {
                "ir_flap_temp_c": reading.ir_flap_temp_c,
                "control_skin_temp_c": reading.control_skin_temp_c,
                "spo2_pct": reading.spo2_pct,
                "doppler_flow_cm_s": reading.doppler_flow_cm_s,
                "visual_redness_index": reading.visual_redness_index,
                "visual_cyanosis_index": reading.visual_cyanosis_index,
                "visual_pallor_index": reading.visual_pallor_index,
                "hours_post_op": reading.hours_post_op,
                "label": out.verified_outcome,
            }
        )
    return records


# ---------------------------------------------------------------------------
# Health Check
# ---------------------------------------------------------------------------
@bp.route("/health")
def health():
    db.session.execute(text("SELECT 1"))
    _, meta = load_model_and_metadata()
    return jsonify(
        {
            "status": "ok",
            "service": "FlapGuard AI",
            "database": "connected",
            "model_version": meta["version"],
            "model_accuracy": meta["accuracy"],
            "patients_count": Patient.query.count(),
        }
    )


# ---------------------------------------------------------------------------
# Web Authentication Routes (Login / Signup / Logout / Profile)
# ---------------------------------------------------------------------------
@bp.route("/register", methods=["GET", "POST"])
def register():
    if current_user.is_authenticated:
        return redirect(url_for("main.dashboard"))

    if request.method == "POST":
        full_name = request.form.get("full_name", "").strip()
        email = request.form.get("email", "").strip().lower()
        role = request.form.get("role", "Surgeon").strip()
        password = request.form.get("password", "")
        confirm_password = request.form.get("confirm_password", "")

        if len(full_name) < 2:
            flash("Please enter your full name (at least 2 characters).", "danger")
        elif not EMAIL_REGEX.match(email):
            flash("Please enter a valid email address.", "danger")
        elif role not in VALID_ROLES:
            flash("Invalid clinician role selected.", "danger")
        elif len(password) < 8:
            flash("Password must be at least 8 characters long.", "danger")
        elif password != confirm_password:
            flash("Passwords do not match.", "danger")
        elif User.query.filter_by(email=email).first():
            flash("An account with this email already exists.", "danger")
        else:
            user = User(full_name=full_name, email=email, role=role)
            user.set_password(password)
            db.session.add(user)
            db.session.commit()
            login_user(user)
            flash(f"Welcome, {user.full_name}! Account created successfully.", "success")
            return redirect(url_for("main.dashboard"))

    return render_template("register.html")


@bp.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("main.dashboard"))

    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        user = User.query.filter_by(email=email).first()

        if user and user.check_password(password):
            login_user(user)
            flash(f"Signed in as {user.full_name} ({user.role}).", "success")
            return redirect(url_for("main.dashboard"))
        flash("Invalid email or password.", "danger")

    return render_template("login.html")


@bp.route("/logout", methods=["POST", "GET"])
@login_required
def logout():
    logout_user()
    flash("You have been signed out.", "info")
    return redirect(url_for("main.login"))


@bp.route("/profile", methods=["GET", "POST"])
@login_required
def profile():
    if request.method == "POST":
        current_pw = request.form.get("current_password", "")
        new_pw = request.form.get("new_password", "")
        confirm_pw = request.form.get("confirm_password", "")

        if not current_user.check_password(current_pw):
            flash("Current password is incorrect.", "danger")
        elif len(new_pw) < 8:
            flash("New password must be at least 8 characters.", "danger")
        elif new_pw != confirm_pw:
            flash("New passwords do not match.", "danger")
        else:
            current_user.set_password(new_pw)
            db.session.commit()
            flash("Password updated successfully.", "success")

    return render_template("profile.html")


# ---------------------------------------------------------------------------
# Risk Stratification Dashboard & Patients
# ---------------------------------------------------------------------------
@bp.route("/")
@login_required
def dashboard():
    patients = Patient.query.all()
    patient_cards = []
    tier_counts = {"Critical": 0, "High": 0, "Moderate": 0, "Low": 0}

    for p in patients:
        latest = p.latest_reading
        tier = latest.risk_tier if latest else "Unassessed"
        if tier in tier_counts:
            tier_counts[tier] += 1
        patient_cards.append({"patient": p, "latest": latest, "tier": tier})

    patient_cards.sort(
        key=lambda item: (
            RISK_PRIORITY.get(item["tier"], 4),
            -(item["latest"].risk_score_pct if item["latest"] else 0.0),
        )
    )

    active_alerts = (
        SensorReading.query.filter_by(alert_required=True, alert_acknowledged=False)
        .order_by(SensorReading.created_at.desc())
        .limit(10)
        .all()
    )
    _, model_meta = load_model_and_metadata()

    return render_template(
        "dashboard.html",
        patient_cards=patient_cards,
        tier_counts=tier_counts,
        active_alerts=active_alerts,
        model_meta=model_meta,
    )


@bp.route("/patients/new", methods=["POST"])
@login_required
def create_patient():
    mrn = request.form.get("mrn", "").strip().upper()
    full_name = request.form.get("full_name", "").strip()
    flap_type = request.form.get("flap_type", "").strip()
    surgery_site = request.form.get("surgery_site", "").strip()

    try:
        age = int(request.form.get("age", "0"))
        hours_post_op = float(request.form.get("hours_post_op", "1.0"))
    except ValueError:
        flash("Age and hours post-op must be valid numbers.", "danger")
        return redirect(url_for("main.dashboard"))

    if not mrn or len(full_name) < 2 or not flap_type or not surgery_site:
        flash("Please fill out all required patient fields.", "danger")
    elif not (1 <= age <= 120):
        flash("Patient age must be between 1 and 120.", "danger")
    elif not (0.0 <= hours_post_op <= 720.0):
        flash("Hours post-op must be between 0 and 720.", "danger")
    elif Patient.query.filter_by(mrn=mrn).first():
        flash(f"Patient with MRN '{mrn}' already exists.", "danger")
    else:
        patient = Patient(
            mrn=mrn,
            full_name=full_name,
            age=age,
            flap_type=flap_type,
            surgery_site=surgery_site,
            hours_post_op=hours_post_op,
            assigned_surgeon_id=current_user.id,
        )
        db.session.add(patient)
        db.session.commit()
        flash(f"Patient {patient.full_name} ({patient.mrn}) added for monitoring.", "success")
        return redirect(url_for("main.patient_detail", patient_id=patient.id))

    return redirect(url_for("main.dashboard"))


@bp.route("/patients/<int:patient_id>")
@login_required
def patient_detail(patient_id: int):
    patient = db.get_or_404(Patient, patient_id)
    readings = patient.readings.limit(30).all()
    chart_readings = list(reversed(readings))
    chart_payload = [
        {
            "label": r.created_at.strftime("%H:%M:%S") if r.created_at else "",
            "temp_delta": r.temp_delta_c,
            "spo2": r.spo2_pct,
            "doppler": r.doppler_flow_cm_s,
            "risk": r.risk_score_pct,
        }
        for r in chart_readings
    ]
    outcomes = patient.outcomes.limit(10).all()

    return render_template(
        "patient_detail.html",
        patient=patient,
        latest=readings[0] if readings else None,
        readings=readings,
        chart_payload_json=json.dumps(chart_payload),
        outcomes=outcomes,
    )


@bp.route("/patients/<int:patient_id>/reading", methods=["POST"])
@login_required
def add_reading(patient_id: int):
    patient = db.get_or_404(Patient, patient_id)
    try:
        ir_temp = float(request.form.get("ir_flap_temp_c", ""))
        control_temp = float(request.form.get("control_skin_temp_c", ""))
        spo2 = float(request.form.get("spo2_pct", ""))
        doppler = float(request.form.get("doppler_flow_cm_s", ""))
        hours_post_op = float(request.form.get("hours_post_op", patient.hours_post_op))
        redness = float(request.form.get("visual_redness_index", "0.14"))
        cyanosis = float(request.form.get("visual_cyanosis_index", "0.03"))
        pallor = float(request.form.get("visual_pallor_index", "0.42"))
        source_mode = request.form.get("source_mode", "Bedside + Camera").strip() or "Bedside + Camera"
    except ValueError:
        flash("All sensor measurements must be valid numeric values.", "danger")
        return redirect(url_for("main.patient_detail", patient_id=patient.id))

    valid, error_msg = validate_sensor_inputs(
        ir_temp, control_temp, spo2, doppler, hours_post_op, redness, cyanosis, pallor
    )
    if not valid:
        flash(error_msg, "danger")
        return redirect(url_for("main.patient_detail", patient_id=patient.id))

    pred = predict_flap_status(
        ir_flap_temp_c=ir_temp,
        control_skin_temp_c=control_temp,
        spo2_pct=spo2,
        doppler_flow_cm_s=doppler,
        visual_redness_index=redness,
        visual_cyanosis_index=cyanosis,
        visual_pallor_index=pallor,
        hours_post_op=hours_post_op,
    )
    reading = _save_prediction_reading(patient, current_user.id, pred, source_mode)

    if reading.alert_required:
        flash(reading.alert_message, "danger")
    else:
        flash(
            f"Reading logged: {reading.predicted_class} ({reading.risk_tier} risk, {reading.risk_score_pct}%).",
            "success",
        )
    return redirect(url_for("main.patient_detail", patient_id=patient.id))


@bp.route("/analyze-image", methods=["POST"])
@login_required
def analyze_image():
    """Local OpenCV flap color analyzer for both webcam capture and uploaded files."""
    image_bytes = None

    if "image" in request.files and request.files["image"].filename:
        image_bytes = request.files["image"].read()
    elif request.is_json:
        data_url = (request.get_json(silent=True) or {}).get("image_base64", "")
        if "," in data_url:
            data_url = data_url.split(",", 1)[1]
        if data_url:
            try:
                image_bytes = base64.b64decode(data_url)
            except Exception:
                return jsonify({"error": "Invalid base64 image data."}), 400

    if not image_bytes:
        return jsonify({"error": "No image provided."}), 400

    try:
        result = analyze_flap_image_bytes(image_bytes)
        return jsonify(result)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400


@bp.route("/patients/<int:patient_id>/simulate", methods=["POST"])
@login_required
def simulate_step(patient_id: int):
    patient = db.get_or_404(Patient, patient_id)
    payload = request.get_json(silent=True) or request.form
    scenario = str(payload.get("scenario", "normal")).strip()
    try:
        step = int(payload.get("step", 0))
    except (ValueError, TypeError):
        step = 0

    if scenario not in SCENARIOS:
        return jsonify({"error": f"Invalid scenario. Choose from {SCENARIOS}."}), 400

    sim = generate_simulated_reading(scenario=scenario, step=step)
    reading = _save_prediction_reading(
        patient=patient,
        recorded_by_id=current_user.id,
        pred=sim["prediction"],
        source_mode=f"Live Simulator ({scenario})",
    )

    if request.is_json or request.headers.get("X-Requested-With") == "XMLHttpRequest":
        return jsonify({"status": "ok", "step": step, "reading": reading.to_dict()})

    flash(reading.alert_message, "danger" if reading.alert_required else "info")
    return redirect(url_for("main.patient_detail", patient_id=patient.id))


# ---------------------------------------------------------------------------
# Alerts Center
# ---------------------------------------------------------------------------
@bp.route("/alerts")
@login_required
def alerts_center():
    alerts = (
        SensorReading.query.filter_by(alert_required=True)
        .order_by(SensorReading.alert_acknowledged.asc(), SensorReading.created_at.desc())
        .limit(50)
        .all()
    )
    return render_template("alerts.html", alerts=alerts)


@bp.route("/alerts/<int:reading_id>/acknowledge", methods=["POST"])
@login_required
def acknowledge_alert(reading_id: int):
    reading = db.get_or_404(SensorReading, reading_id)
    reading.alert_acknowledged = True
    reading.acknowledged_by = current_user.full_name
    db.session.commit()
    flash(f"Alert #{reading.id} acknowledged by {current_user.full_name}.", "success")
    return redirect(request.referrer or url_for("main.alerts_center"))


# ---------------------------------------------------------------------------
# Continuous Learning & Model Retraining
# ---------------------------------------------------------------------------
@bp.route("/learning")
@login_required
def learning_center():
    _, model_meta = load_model_and_metadata()
    patients = Patient.query.order_by(Patient.full_name.asc()).all()
    outcomes = PostOpOutcome.query.order_by(PostOpOutcome.created_at.desc()).limit(30).all()
    versions = ModelVersionLog.query.order_by(ModelVersionLog.id.desc()).limit(15).all()
    return render_template(
        "learning.html",
        model_meta=model_meta,
        patients=patients,
        outcomes=outcomes,
        versions=versions,
    )


@bp.route("/learning/outcome", methods=["POST"])
@login_required
def log_outcome():
    try:
        patient_id = int(request.form.get("patient_id", "0"))
    except ValueError:
        flash("Please choose a valid patient.", "danger")
        return redirect(url_for("main.learning_center"))

    verified_outcome = request.form.get("verified_outcome", "").strip()
    surgeon_notes = request.form.get("surgeon_notes", "").strip()[:300]

    patient = db.session.get(Patient, patient_id)
    if not patient:
        flash("Selected patient not found.", "danger")
        return redirect(url_for("main.learning_center"))
    if verified_outcome not in VALID_OUTCOMES:
        flash("Please choose Normal, Venous Compromise, or Arterial Compromise.", "danger")
        return redirect(url_for("main.learning_center"))

    latest = patient.latest_reading
    outcome = PostOpOutcome(
        patient_id=patient.id,
        reading_id=latest.id if latest else None,
        logged_by_id=current_user.id,
        verified_outcome=verified_outcome,
        surgeon_notes=surgeon_notes,
    )
    db.session.add(outcome)
    db.session.commit()
    flash(
        f"Verified postoperative outcome ({verified_outcome}) saved for {patient.full_name}.",
        "success",
    )
    return redirect(request.referrer or url_for("main.learning_center"))


@bp.route("/learning/retrain", methods=["POST"])
@login_required
def retrain_model():
    records = _collect_outcome_records_for_retraining()
    summary = retrain_from_postop_outcomes(records)
    new_meta = summary["metadata"]

    db.session.add(
        ModelVersionLog(
            version=new_meta["version"],
            accuracy=new_meta["accuracy"],
            macro_f1=new_meta["macro_f1"],
            total_samples=new_meta["total_samples"],
            postop_outcomes_added=new_meta["postop_outcomes_added"],
            trained_at_utc=new_meta["trained_at_utc"],
            notes=f"Retrained by {current_user.full_name} with {len(records)} postoperative outcomes",
        )
    )
    db.session.commit()

    flash(
        f"Continuous learning complete: {summary['old_version']} ({summary['old_accuracy']*100:.2f}%) -> "
        f"{summary['new_version']} ({summary['new_accuracy']*100:.2f}%) using {summary['outcomes_incorporated']} outcomes.",
        "success",
    )
    return redirect(url_for("main.learning_center"))


# ---------------------------------------------------------------------------
# Local Mobile Sync Routes (For Your Android Kotlin App in Phase 4)
# ---------------------------------------------------------------------------
@bp.route("/mobile/register", methods=["POST"])
def mobile_register():
    data = request.get_json(silent=True) or {}
    full_name = str(data.get("full_name", "")).strip()
    email = str(data.get("email", "")).strip().lower()
    role = str(data.get("role", "Surgeon")).strip()
    password = str(data.get("password", ""))

    if len(full_name) < 2 or not EMAIL_REGEX.match(email) or len(password) < 8:
        return jsonify({"error": "Provide full name, valid email, and password (8+ chars)."}), 400
    if role not in VALID_ROLES:
        role = "Surgeon"
    if User.query.filter_by(email=email).first():
        return jsonify({"error": "Email already registered."}), 409

    user = User(full_name=full_name, email=email, role=role)
    user.set_password(password)
    db.session.add(user)
    db.session.commit()
    return jsonify({"token": generate_mobile_token(user), "user": user.to_dict()}), 201


@bp.route("/mobile/login", methods=["POST"])
def mobile_login():
    data = request.get_json(silent=True) or {}
    email = str(data.get("email", "")).strip().lower()
    password = str(data.get("password", ""))
    user = User.query.filter_by(email=email).first()
    if not user or not user.check_password(password):
        return jsonify({"error": "Invalid email or password."}), 401
    return jsonify({"token": generate_mobile_token(user), "user": user.to_dict()})


@bp.route("/mobile/patients", methods=["GET", "POST"])
@mobile_auth_required
def mobile_patients():
    if request.method == "POST":
        data = request.get_json(silent=True) or {}
        mrn = str(data.get("mrn", "")).strip().upper()
        full_name = str(data.get("full_name", "")).strip()
        flap_type = str(data.get("flap_type", "")).strip()
        surgery_site = str(data.get("surgery_site", "")).strip()
        try:
            age = int(data.get("age", 0))
            hours_post_op = float(data.get("hours_post_op", 1.0))
        except (ValueError, TypeError):
            return jsonify({"error": "Age and hours_post_op must be numeric."}), 400

        if not mrn or len(full_name) < 2 or not flap_type or not surgery_site or not (1 <= age <= 120):
            return jsonify({"error": "Invalid or missing patient details."}), 400
        if Patient.query.filter_by(mrn=mrn).first():
            return jsonify({"error": "MRN already exists."}), 409

        patient = Patient(
            mrn=mrn,
            full_name=full_name,
            age=age,
            flap_type=flap_type,
            surgery_site=surgery_site,
            hours_post_op=hours_post_op,
            assigned_surgeon_id=request.mobile_user.id,
        )
        db.session.add(patient)
        db.session.commit()
        return jsonify(patient.to_dict()), 201

    patients = [p.to_dict() for p in Patient.query.all()]
    patients.sort(
        key=lambda item: RISK_PRIORITY.get(
            (item["latest_reading"] or {}).get("risk_tier", "Unassessed"), 4
        )
    )
    return jsonify({"patients": patients})


@bp.route("/mobile/alerts", methods=["GET"])
@mobile_auth_required
def mobile_alerts():
    alerts = (
        SensorReading.query.filter_by(alert_required=True)
        .order_by(SensorReading.alert_acknowledged.asc(), SensorReading.created_at.desc())
        .limit(30)
        .all()
    )
    return jsonify(
        {
            "unacknowledged_count": sum(1 for a in alerts if not a.alert_acknowledged),
            "alerts": [a.to_dict() for a in alerts],
        }
    )


@bp.route("/mobile/alerts/<int:reading_id>/acknowledge", methods=["POST"])
@mobile_auth_required
def mobile_acknowledge_alert(reading_id: int):
    reading = db.session.get(SensorReading, reading_id)
    if not reading:
        return jsonify({"error": "Alert not found."}), 404
    reading.alert_acknowledged = True
    reading.acknowledged_by = request.mobile_user.full_name
    db.session.commit()
    return jsonify({"status": "acknowledged", "reading": reading.to_dict()})


@bp.route("/mobile/analyze-image", methods=["POST"])
@mobile_auth_required
def mobile_analyze_image():
    image_bytes = None
    if "image" in request.files and request.files["image"].filename:
        image_bytes = request.files["image"].read()
    elif request.is_json:
        data_url = (request.get_json(silent=True) or {}).get("image_base64", "")
        if "," in data_url:
            data_url = data_url.split(",", 1)[1]
        if data_url:
            try:
                image_bytes = base64.b64decode(data_url)
            except Exception:
                return jsonify({"error": "Invalid base64 image."}), 400

    if not image_bytes:
        return jsonify({"error": "No image uploaded."}), 400
    try:
        return jsonify(analyze_flap_image_bytes(image_bytes))
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400


@bp.route("/mobile/patients/<int:patient_id>/reading", methods=["POST"])
@mobile_auth_required
def mobile_add_reading(patient_id: int):
    patient = db.session.get(Patient, patient_id)
    if not patient:
        return jsonify({"error": "Patient not found."}), 404
    data = request.get_json(silent=True) or {}
    try:
        ir_temp = float(data["ir_flap_temp_c"])
        control_temp = float(data["control_skin_temp_c"])
        spo2 = float(data["spo2_pct"])
        doppler = float(data["doppler_flow_cm_s"])
        hours_post_op = float(data.get("hours_post_op", patient.hours_post_op))
        redness = float(data.get("visual_redness_index", 0.14))
        cyanosis = float(data.get("visual_cyanosis_index", 0.03))
        pallor = float(data.get("visual_pallor_index", 0.42))
    except (KeyError, ValueError, TypeError):
        return jsonify({"error": "Missing or non-numeric sensor fields."}), 400

    valid, error_msg = validate_sensor_inputs(
        ir_temp, control_temp, spo2, doppler, hours_post_op, redness, cyanosis, pallor
    )
    if not valid:
        return jsonify({"error": error_msg}), 400

    pred = predict_flap_status(
        ir_flap_temp_c=ir_temp,
        control_skin_temp_c=control_temp,
        spo2_pct=spo2,
        doppler_flow_cm_s=doppler,
        visual_redness_index=redness,
        visual_cyanosis_index=cyanosis,
        visual_pallor_index=pallor,
        hours_post_op=hours_post_op,
    )
    reading = _save_prediction_reading(patient, request.mobile_user.id, pred, "Android Mobile App")
    return jsonify({"reading": reading.to_dict()}), 201


@bp.route("/mobile/patients/<int:patient_id>/simulate", methods=["POST"])
@mobile_auth_required
def mobile_simulate(patient_id: int):
    patient = db.session.get(Patient, patient_id)
    if not patient:
        return jsonify({"error": "Patient not found."}), 404
    data = request.get_json(silent=True) or {}
    scenario = str(data.get("scenario", "arterial_deteriorating"))
    step = int(data.get("step", 7))
    if scenario not in SCENARIOS:
        return jsonify({"error": "Invalid scenario."}), 400
    sim = generate_simulated_reading(scenario=scenario, step=step)
    reading = _save_prediction_reading(
        patient, request.mobile_user.id, sim["prediction"], f"Mobile Simulator ({scenario})"
    )
    return jsonify({"reading": reading.to_dict()}), 201


@bp.route("/mobile/outcomes", methods=["POST"])
@mobile_auth_required
def mobile_log_outcome():
    data = request.get_json(silent=True) or {}
    try:
        patient_id = int(data.get("patient_id", 0))
    except (ValueError, TypeError):
        return jsonify({"error": "Invalid patient_id."}), 400
    verified_outcome = str(data.get("verified_outcome", "")).strip()
    surgeon_notes = str(data.get("surgeon_notes", "")).strip()[:300]

    patient = db.session.get(Patient, patient_id)
    if not patient or verified_outcome not in VALID_OUTCOMES:
        return jsonify({"error": "Valid patient_id and verified_outcome are required."}), 400

    latest = patient.latest_reading
    outcome = PostOpOutcome(
        patient_id=patient.id,
        reading_id=latest.id if latest else None,
        logged_by_id=request.mobile_user.id,
        verified_outcome=verified_outcome,
        surgeon_notes=surgeon_notes,
    )
    db.session.add(outcome)
    db.session.commit()
    return jsonify({"outcome": outcome.to_dict()}), 201