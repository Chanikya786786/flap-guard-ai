"""
Web Routes & Mobile Sync Routes for FlapGuard AI.
Enforces RBAC, CSRF protection, server-side token revocation, login rate limiting,
soft-delete patient archiving, and an append-only clinical AuditLog.
"""

import base64
import json
import re
from functools import wraps
from typing import Optional, Tuple

import cv2
import numpy as np
from flask import (
    Blueprint,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    url_for,
)
from flask_login import current_user, login_required, login_user, logout_user
from sqlalchemy import func, text
from sqlalchemy.orm import joinedload

from ml_engine.fusion import classify_visual_triage
from ml_engine.model_service import (
    load_model_and_metadata,
    predict_flap_status,
    retrain_from_postop_outcomes,
)
from ml_engine.simulator import SCENARIOS, generate_simulated_reading
from ml_engine.vision_analyzer import analyze_flap_image_bytes
from web_app.models import (
    AuditLog,
    ModelVersionLog,
    Patient,
    PostOpOutcome,
    SensorReading,
    User,
    db,
    utc_now,
)
from web_app.security import (
    ACCOUNT_DELETION_GRACE_DAYS,
    enforce_due_account_deletion,
    normalize_utc,
    remaining_deletion_window,
    schedule_user_deletion,
    ENROLLMENT_ROLES,
    PRIVILEGED_ROLES,
    clear_login_failures,
    generate_mobile_token,
    get_client_ip,
    get_required_invite_code,
    is_login_rate_limited,
    log_audit,
    mobile_roles_required,
    record_login_failure,
    revoke_mobile_token,
    validate_invite_code,
    verify_mobile_token,
    verify_mobile_token_with_meta,
    web_roles_required,
)
from web_app.model_store import persist_active_model_to_db
from web_app.ward_service import (
    clear_all_ward_patients,
    fetch_patients_with_latest_readings,
    load_sample_ward_cases,
)


bp = Blueprint("main", __name__)

EMAIL_REGEX = re.compile(r"^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$")
VALID_ROLES = {"Surgeon", "Nurse", "Resident", "Admin"}
VALID_OUTCOMES = {"Normal", "Venous Compromise", "Arterial Compromise"}
RISK_PRIORITY = {"Critical": 0, "High": 1, "Moderate": 2, "Low": 3, "Unassessed": 4}


def mobile_auth_required(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        auth_header = request.headers.get("Authorization", "")
        if not auth_header.startswith("Bearer "):
            return jsonify({"error": "Missing or invalid Bearer token."}), 401
        token = auth_header.split(" ", 1)[1].strip()
        user, jti = verify_mobile_token_with_meta(token)
        if user is None:
            return jsonify({"error": "Expired, revoked, or invalid authentication token."}), 401
        request.mobile_user = user
        request.mobile_token_jti = jti
        return fn(*args, **kwargs)

    return wrapper


def validate_sensor_inputs(
    ir_temp: Optional[float],
    control_temp: Optional[float],
    spo2: Optional[float],
    doppler: Optional[float],
    hours_post_op: float,
    redness: float = 0.14,
    cyanosis: float = 0.03,
    pallor: float = 0.42,
) -> Tuple[bool, str]:
    checks = []
    if ir_temp is not None:
        checks.append((20.0 <= ir_temp <= 43.0, "IR Flap Temperature must be between 20.0 and 43.0 C."))
    if control_temp is not None:
        checks.append((30.0 <= control_temp <= 43.0, "Control Skin Temperature must be between 30.0 and 43.0 C."))
    if spo2 is not None:
        checks.append((0.0 <= spo2 <= 100.0, "SpO2 must be between 0 and 100%."))
    if doppler is not None:
        checks.append((0.0 <= doppler <= 60.0, "Doppler flow must be between 0.0 and 60.0 cm/s."))
    checks.extend(
        [
            (0.0 <= hours_post_op <= 720.0, "Hours post-op must be between 0 and 720."),
            (0.0 <= redness <= 1.0, "Visual redness index must be between 0.0 and 1.0."),
            (0.0 <= cyanosis <= 1.0, "Visual cyanosis index must be between 0.0 and 1.0."),
            (0.0 <= pallor <= 1.0, "Visual pallor index must be between 0.0 and 1.0."),
        ]
    )
    for ok, msg in checks:
        if not ok:
            return False, msg
    return True, ""


def _recent_patient_history(patient: Patient, limit: int = 10) -> list:
    recent_desc = (
        SensorReading.query.filter_by(patient_id=patient.id)
        .order_by(SensorReading.created_at.desc(), SensorReading.id.desc())
        .limit(limit)
        .all()
    )
    return list(reversed(recent_desc))


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
        trend_status=str(pred.get("trend_status", "Baseline Established"))[:40],
        ood_flag=bool(pred.get("ood_flag", False)),
        confidence_label=str(pred.get("confidence_label", "High"))[:40],
        trend_json=json.dumps(pred.get("trend_summary", {})),
        source_mode=str(source_mode or "Bedside + Camera")[:80],
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


def _encode_profile_avatar(image_bytes: bytes) -> str:
    """Validate, center-crop to 256x256 JPEG, and return a compact base64 data URI."""
    if not image_bytes:
        raise ValueError("Empty image uploaded.")
    if len(image_bytes) > 6 * 1024 * 1024:
        raise ValueError("Profile photo must be under 6 MB.")
    arr = np.frombuffer(image_bytes, dtype=np.uint8)
    img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
    if img is None or img.size == 0:
        raise ValueError("Unsupported or corrupted image file.")
    h, w = img.shape[:2]
    side = min(h, w)
    y0 = (h - side) // 2
    x0 = (w - side) // 2
    cropped = img[y0 : y0 + side, x0 : x0 + side]
    resized = cv2.resize(cropped, (256, 256), interpolation=cv2.INTER_AREA)
    ok, buf = cv2.imencode(".jpg", resized, [int(cv2.IMWRITE_JPEG_QUALITY), 84])
    if not ok:
        raise ValueError("Could not encode profile photo.")
    b64 = base64.b64encode(buf.tobytes()).decode("ascii")
    return f"data:image/jpeg;base64,{b64}"


# ---------------------------------------------------------------------------
# Health Check
# ---------------------------------------------------------------------------
@bp.route("/health")
def health():
    try:
        db.session.execute(text("SELECT 1"))
        patients_count = (
            db.session.query(func.count(Patient.id))
            .filter(Patient.is_archived.is_(False))
            .scalar()
            or 0
        )
        _, meta = load_model_and_metadata()
        return jsonify(
            {
                "status": "ok",
                "service": "FlapGuard AI",
                "database": "connected",
                "model_version": meta["version"],
                "model_accuracy": meta["accuracy"],
                "model_brier_score": meta.get("brier_score"),
                "calibration": meta.get("calibration_method", "sigmoid"),
                "patients_count": int(patients_count),
            }
        )
    except Exception as exc:
        db.session.rollback()
        return jsonify({"status": "error", "database": "error", "detail": str(exc)}), 503


# ---------------------------------------------------------------------------
# Web Authentication Routes (Login / Signup / Logout / Profile)
# ---------------------------------------------------------------------------
@bp.route("/register", methods=["GET", "POST"])
def register():
    if current_user.is_authenticated:
        return redirect(url_for("main.dashboard"))

    invite_required = bool(get_required_invite_code())
    if request.method == "POST":
        full_name = request.form.get("full_name", "").strip()
        email = request.form.get("email", "").strip().lower()
        role = request.form.get("role", "Surgeon").strip()
        password = request.form.get("password", "")
        confirm_password = request.form.get("confirm_password", "")
        invite_code = request.form.get("invite_code", "")

        if not validate_invite_code(invite_code):
            flash("Invalid or missing hospital registration invite code.", "danger")
            log_audit("REGISTER_DENIED", "user", email, "Invalid invite code")
            return render_template("register.html", invite_required=invite_required), 403
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
            log_audit("REGISTER", "user", str(user.id), f"Role={user.role}", actor=user)
            flash(f"Welcome, {user.full_name}! Account created successfully.", "success")
            return redirect(url_for("main.dashboard"))

    return render_template("register.html", invite_required=invite_required)


@bp.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("main.dashboard"))

    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        ip_addr = get_client_ip()

        if is_login_rate_limited(ip_addr, email):
            log_audit("LOGIN_RATE_LIMITED", "user", email, f"IP={ip_addr}")
            flash("Too many failed login attempts. Please wait a few minutes.", "danger")
            return render_template("login.html"), 429

        user = User.query.filter_by(email=email).first()
        if user and not enforce_due_account_deletion(user) and user.check_password(password):
            clear_login_failures(ip_addr, email)
            if user.scheduled_deletion_at is not None:
                if request.form.get("confirm_restore") == "yes":
                    user.deletion_requested_at = None
                    user.scheduled_deletion_at = None
                    db.session.commit()
                    login_user(user)
                    log_audit("ACCOUNT_RESTORED", "user", str(user.id), "Restored via Web before 30-day deadline", actor=user)
                    flash("Account deletion cancelled. Your clinician account has been restored.", "success")
                    return redirect(url_for("main.dashboard"))
                days_rem, hrs_rem = remaining_deletion_window(user)
                dl = normalize_utc(user.scheduled_deletion_at)
                return render_template(
                    "restore_account.html",
                    email=email,
                    password=password,
                    deadline_utc=dl.strftime("%d %b %Y, %H:%M UTC"),
                    days_remaining=days_rem,
                    hours_remaining=hrs_rem,
                )
            login_user(user)
            log_audit("LOGIN_SUCCESS", "user", str(user.id), "Web session", actor=user)
            flash(f"Signed in as {user.full_name} ({user.role}).", "success")
            return redirect(url_for("main.dashboard"))

        record_login_failure(ip_addr, email)
        log_audit("LOGIN_FAILED", "user", email, "Web login invalid credentials")
        flash("Invalid email or password.", "danger")

    return render_template("login.html")


@bp.route("/logout", methods=["POST", "GET"])
@login_required
def logout():
    actor = current_user._get_current_object()
    log_audit("LOGOUT", "user", str(actor.id), "Web logout", actor=actor)
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
            current_user.bump_token_version()
            db.session.commit()
            log_audit(
                "PASSWORD_CHANGED",
                "user",
                str(current_user.id),
                "Password updated via Web; prior mobile tokens revoked",
            )
            flash("Password updated successfully. Active mobile sessions were reset.", "success")

    active_patients = Patient.query.filter(Patient.is_archived.is_(False)).count()
    return render_template(
        "profile.html",
        patients_count=active_patients,
        readings_count=SensorReading.query.count(),
    )


@bp.route("/profile/photo", methods=["POST"])
@login_required
def profile_update_photo():
    if request.form.get("action") == "remove":
        current_user.profile_photo = None
        db.session.commit()
        log_audit("PROFILE_PHOTO_REMOVED", "user", str(current_user.id), "Web")
        flash("Profile photo removed.", "success")
        return redirect(url_for("main.profile"))

    photo_file = request.files.get("photo")
    if not photo_file or not photo_file.filename:
        flash("Choose an image file to upload.", "warning")
        return redirect(url_for("main.profile"))
    try:
        current_user.profile_photo = _encode_profile_avatar(photo_file.read())
        db.session.commit()
        log_audit("PROFILE_PHOTO_UPDATED", "user", str(current_user.id), "Web")
        flash("Profile photo updated.", "success")
    except ValueError as exc:
        flash(str(exc), "danger")
    return redirect(url_for("main.profile"))


@bp.route("/profile/load-samples", methods=["POST"])
@login_required
@web_roles_required(PRIVILEGED_ROLES)
def profile_load_samples():
    created = load_sample_ward_cases(
        assigned_surgeon_id=current_user.id,
        recorded_by_id=current_user.id,
    )
    log_audit("SAMPLES_LOADED", "ward", "samples", f"Created/restored={created}")
    if created > 0:
        flash(f"Loaded {created} sample OMFS clinical cases into the ward.", "success")
    else:
        flash("Sample clinical cases are already loaded in the ward.", "info")
    return redirect(url_for("main.dashboard"))


@bp.route("/profile/sign-out-all", methods=["POST"])
@login_required
def profile_sign_out_all():
    current_user.bump_token_version()
    db.session.commit()
    log_audit("SESSIONS_REVOKED_ALL", "user", str(current_user.id), "Web sign-out all mobile devices")
    flash("Signed out of all connected mobile devices. Re-sign in on your phone to reconnect.", "success")
    return redirect(url_for("main.profile"))


@bp.route("/profile/delete-account", methods=["POST"])
@login_required
def profile_delete_account():
    password = request.form.get("confirm_delete_password", "")
    if not current_user.check_password(password):
        flash("Password is incorrect. Account deletion was not scheduled.", "danger")
        return redirect(url_for("main.profile"))

    actor = current_user._get_current_object()
    deadline = schedule_user_deletion(actor)
    log_audit(
        "ACCOUNT_DELETION_SCHEDULED",
        "user",
        str(actor.id),
        f"Web request; permanent purge scheduled at {deadline.isoformat()}",
        actor=actor,
    )
    logout_user()
    flash(
        f"Account scheduled for permanent deletion on {deadline.strftime('%d %b %Y at %H:%M UTC')} "
        f"({ACCOUNT_DELETION_GRACE_DAYS}-day grace period). Sign back in anytime before then to cancel and restore.",
        "warning",
    )
    return redirect(url_for("main.login"))


@bp.route("/account-deletion", methods=["GET"])
def public_account_deletion_policy():
    return render_template("account_deletion_policy.html", grace_days=ACCOUNT_DELETION_GRACE_DAYS)


@bp.route("/profile/clear-ward", methods=["POST"])
@login_required
@web_roles_required(PRIVILEGED_ROLES)
def profile_clear_ward():
    removed = clear_all_ward_patients(archived_by_id=current_user.id)
    log_audit("WARD_ARCHIVED", "ward", "all", f"Archived {removed} active patient(s)")
    flash(f"Archived {removed} patient record(s). Historical data preserved in Audit Log.", "success")
    return redirect(url_for("main.dashboard"))


# ---------------------------------------------------------------------------
# Audit Trail Viewer (Surgeon / Admin Only)
# ---------------------------------------------------------------------------
@bp.route("/audit")
@login_required
@web_roles_required(PRIVILEGED_ROLES)
def audit_center():
    logs = AuditLog.query.order_by(AuditLog.created_at.desc()).limit(100).all()
    archived_count = Patient.query.filter(Patient.is_archived.is_(True)).count()
    return render_template("audit.html", logs=logs, archived_count=archived_count)


# ---------------------------------------------------------------------------
# Risk Stratification Dashboard & Patients
# ---------------------------------------------------------------------------
@bp.route("/")
@login_required
def dashboard():
    patients = (
        Patient.query.filter(Patient.is_archived.is_(False))
        .order_by(Patient.created_at.desc())
        .all()
    )
    latest_by_pid = {}
    if patients:
        pids = [p.id for p in patients]
        latest_subq = (
            db.session.query(
                SensorReading.patient_id,
                func.max(SensorReading.id).label("max_id"),
            )
            .filter(SensorReading.patient_id.in_(pids))
            .group_by(SensorReading.patient_id)
            .subquery()
        )
        for r in SensorReading.query.join(latest_subq, SensorReading.id == latest_subq.c.max_id).all():
            latest_by_pid[r.patient_id] = r

    patient_cards = []
    tier_counts = {"Critical": 0, "High": 0, "Moderate": 0, "Low": 0}

    for p in patients:
        latest = latest_by_pid.get(p.id)
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
        SensorReading.query.join(Patient, SensorReading.patient_id == Patient.id)
        .options(joinedload(SensorReading.patient))
        .filter(
            Patient.is_archived.is_(False),
            SensorReading.alert_required.is_(True),
            SensorReading.alert_acknowledged.is_(False),
        )
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
@web_roles_required(ENROLLMENT_ROLES)
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
        flash(f"Patient with MRN '{mrn}' already exists in active or archived records.", "danger")
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
        log_audit("PATIENT_ENROLLED", "patient", str(patient.id), f"MRN={patient.mrn}, Flap={patient.flap_type}")
        flash(f"Patient {patient.full_name} ({patient.mrn}) added for monitoring.", "success")
        return redirect(url_for("main.patient_detail", patient_id=patient.id))

    return redirect(url_for("main.dashboard"))


@bp.route("/patients/<int:patient_id>/delete", methods=["POST"])
@login_required
@web_roles_required(PRIVILEGED_ROLES)
def delete_patient(patient_id: int):
    patient = db.session.get(Patient, patient_id)
    if not patient or patient.is_archived:
        flash("Patient record not found.", "warning")
    else:
        name = patient.full_name
        patient.is_archived = True
        patient.archived_at = utc_now()
        patient.archived_by_id = current_user.id
        db.session.commit()
        log_audit("PATIENT_ARCHIVED", "patient", str(patient.id), f"MRN={patient.mrn}, Name={name}")
        flash(f"Archived patient {name} from active ward monitoring (history preserved).", "success")
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
        hours_post_op = float(request.form.get("hours_post_op") or patient.hours_post_op)
        redness = float(request.form.get("visual_redness_index") or 0.14)
        cyanosis = float(request.form.get("visual_cyanosis_index") or 0.03)
        pallor = float(request.form.get("visual_pallor_index") or 0.42)
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
        history=_recent_patient_history(patient),
    )
    reading = _save_prediction_reading(patient, current_user.id, pred, source_mode)
    log_audit(
        "READING_LOGGED",
        "sensor_reading",
        str(reading.id),
        f"Patient={patient.mrn}, Class={reading.predicted_class}, Tier={reading.risk_tier}",
    )

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

    control_bytes = None
    if "control_image" in request.files and request.files["control_image"].filename:
        control_bytes = request.files["control_image"].read()
    elif request.is_json:
        ctrl_b64 = (request.get_json(silent=True) or {}).get("control_image_base64", "")
        if "," in ctrl_b64:
            ctrl_b64 = ctrl_b64.split(",", 1)[1]
        if ctrl_b64:
            try:
                control_bytes = base64.b64decode(ctrl_b64)
            except Exception:
                return jsonify({"error": "Invalid base64 control image data."}), 400

    try:
        result = analyze_flap_image_bytes(image_bytes, control_image_bytes=control_bytes)
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
        SensorReading.query.join(Patient, SensorReading.patient_id == Patient.id)
        .options(joinedload(SensorReading.patient))
        .filter(Patient.is_archived.is_(False), SensorReading.alert_required.is_(True))
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
    log_audit("ALERT_ACKNOWLEDGED", "sensor_reading", str(reading.id), f"Web by {current_user.full_name}")
    flash(f"Alert #{reading.id} acknowledged by {current_user.full_name}.", "success")
    return redirect(request.referrer or url_for("main.alerts_center"))


# ---------------------------------------------------------------------------
# Continuous Learning & Model Retraining
# ---------------------------------------------------------------------------
@bp.route("/learning")
@login_required
def learning_center():
    _, model_meta = load_model_and_metadata()
    patients = (
        Patient.query.filter(Patient.is_archived.is_(False))
        .order_by(Patient.full_name.asc())
        .all()
    )
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
@web_roles_required(ENROLLMENT_ROLES)
def log_outcome():
    try:
        patient_id = int(request.form.get("patient_id", "0"))
    except ValueError:
        flash("Please choose a valid patient.", "danger")
        return redirect(url_for("main.learning_center"))

    verified_outcome = request.form.get("verified_outcome", "").strip()
    surgeon_notes = request.form.get("surgeon_notes", "").strip()[:300]

    patient = db.session.get(Patient, patient_id)
    if not patient or patient.is_archived:
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
    log_audit("OUTCOME_LOGGED", "postop_outcome", str(outcome.id), f"Patient={patient.mrn}, Outcome={verified_outcome}")
    flash(
        f"Verified postoperative outcome ({verified_outcome}) saved for {patient.full_name}.",
        "success",
    )
    return redirect(request.referrer or url_for("main.learning_center"))


@bp.route("/learning/retrain", methods=["POST"])
@login_required
@web_roles_required(PRIVILEGED_ROLES)
def retrain_model():
    records = _collect_outcome_records_for_retraining()
    summary = retrain_from_postop_outcomes(records)
    if not summary.get("promoted", True):
        reason = summary.get("rejection_reason", "Holdout safety gate failed.")
        log_audit("MODEL_RETRAIN_REJECTED", "model", str(summary["old_version"]), reason)
        flash(f"Model promotion blocked: {reason}", "danger")
        return redirect(url_for("main.learning_center"))

    new_meta = summary["metadata"]
    persist_active_model_to_db(summary["model"], new_meta)
    db.session.add(
        ModelVersionLog(
            version=new_meta["version"],
            accuracy=new_meta["accuracy"],
            macro_f1=new_meta["macro_f1"],
            total_samples=new_meta["total_samples"],
            postop_outcomes_added=new_meta["postop_outcomes_added"],
            trained_at_utc=new_meta["trained_at_utc"],
            notes=(
                f"Calibrated retrain by {current_user.full_name} "
                f"(Outcomes={len(records)}, Brier={new_meta.get('brier_score', '-')}, Persisted=DB)"
            ),
        )
    )
    db.session.commit()
    log_audit(
        "MODEL_RETRAINED",
        "model",
        str(new_meta["version"]),
        f"Outcomes={len(records)}, MacroF1={new_meta['macro_f1']}, Persisted=PostgreSQL/DB",
    )
    flash(
        f"Continuous learning complete: AI model upgraded to {new_meta['version']} "
        f"(Accuracy: {new_meta['accuracy'] * 100:.2f}%, Holdout F1: {new_meta['macro_f1'] * 100:.1f}%, "
        f"Outcomes included: {len(records)}, Saved to DB).",
        "success",
    )
    return redirect(url_for("main.learning_center"))


# ---------------------------------------------------------------------------
# Mobile JSON Endpoints (Android Surgeon & Clinician App Sync)
# ---------------------------------------------------------------------------
@bp.route("/mobile/register", methods=["POST"])
def mobile_register():
    data = request.get_json(silent=True) or {}
    full_name = str(data.get("full_name", "")).strip()
    email = str(data.get("email", "")).strip().lower()
    role = str(data.get("role", "Surgeon")).strip()
    password = str(data.get("password", ""))
    invite_code = str(data.get("invite_code", ""))

    if not validate_invite_code(invite_code):
        log_audit("REGISTER_DENIED", "user", email, "Mobile invalid invite code")
        return jsonify({"error": "Invalid or missing hospital registration invite code."}), 403
    if len(full_name) < 2 or not EMAIL_REGEX.match(email) or role not in VALID_ROLES or len(password) < 8:
        return jsonify({"error": "Invalid registration fields. Password must be at least 8 characters."}), 400
    if User.query.filter_by(email=email).first():
        return jsonify({"error": "Account with this email already exists."}), 409

    user = User(full_name=full_name, email=email, role=role)
    user.set_password(password)
    db.session.add(user)
    db.session.commit()
    log_audit("REGISTER", "user", str(user.id), f"Mobile Role={user.role}", actor=user)
    return jsonify({"token": generate_mobile_token(user), "user": user.to_dict()}), 201


@bp.route("/mobile/login", methods=["POST"])
def mobile_login():
    data = request.get_json(silent=True) or {}
    email = str(data.get("email", "")).strip().lower()
    password = str(data.get("password", ""))
    ip_addr = get_client_ip()

    if is_login_rate_limited(ip_addr, email):
        log_audit("LOGIN_RATE_LIMITED", "user", email, f"Mobile IP={ip_addr}")
        return jsonify({"error": "Too many failed login attempts. Try again later."}), 429

    restore_flag = bool(data.get("restore", False))
    user = User.query.filter_by(email=email).first()
    if not user or enforce_due_account_deletion(user) or not user.check_password(password):
        record_login_failure(ip_addr, email)
        log_audit("LOGIN_FAILED", "user", email, "Mobile invalid credentials or purged account")
        return jsonify({"error": "Invalid email or password."}), 401

    clear_login_failures(ip_addr, email)
    if user.scheduled_deletion_at is not None:
        if restore_flag:
            user.deletion_requested_at = None
            user.scheduled_deletion_at = None
            db.session.commit()
            log_audit("ACCOUNT_RESTORED", "user", str(user.id), "Restored via Mobile before 30-day deadline", actor=user)
            return jsonify({"token": generate_mobile_token(user), "user": user.to_dict(), "restored": True})

        days_rem, hrs_rem = remaining_deletion_window(user)
        dl = normalize_utc(user.scheduled_deletion_at)
        return (
            jsonify(
                {
                    "error": (
                        f"Account scheduled for permanent deletion on "
                        f"{dl.strftime('%d %b %Y, %H:%M UTC')} ({days_rem}d {hrs_rem}h left)."
                    ),
                    "code": "ACCOUNT_DELETION_PENDING",
                    "scheduled_deletion_at": dl.isoformat(),
                    "days_remaining": days_rem,
                    "hours_remaining": hrs_rem,
                }
            ),
            403,
        )

    log_audit("LOGIN_SUCCESS", "user", str(user.id), "Mobile token issued", actor=user)
    return jsonify({"token": generate_mobile_token(user), "user": user.to_dict()})


@bp.route("/mobile/logout", methods=["POST"])
@mobile_auth_required
def mobile_logout():
    revoke_mobile_token(getattr(request, "mobile_token_jti", None), request.mobile_user.id)
    log_audit("LOGOUT", "user", str(request.mobile_user.id), "Mobile token revoked", actor=request.mobile_user)
    return jsonify({"status": "logged_out"})


@bp.route("/mobile/patients", methods=["GET", "POST"])
@mobile_auth_required
def mobile_patients():
    if request.method == "POST":
        if request.mobile_user.role not in ENROLLMENT_ROLES:
            log_audit("RBAC_DENIED", "mobile_route", "/mobile/patients", f"Role={request.mobile_user.role}")
            return jsonify({"error": "Insufficient role permissions to enroll patients."}), 403

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
            return jsonify({"error": "MRN already exists in active or archived records."}), 409

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
        log_audit("PATIENT_ENROLLED", "patient", str(patient.id), f"Mobile MRN={patient.mrn}")
        return jsonify(patient.to_dict()), 201

    patients = fetch_patients_with_latest_readings()
    patients.sort(
        key=lambda item: (
            RISK_PRIORITY.get(
                (item["latest_reading"] or {}).get("risk_tier", "Unassessed"), 4
            ),
            -float((item["latest_reading"] or {}).get("risk_score_pct", 0.0)),
        )
    )
    return jsonify({"patients": patients})


@bp.route("/mobile/alerts", methods=["GET"])
@mobile_auth_required
def mobile_alerts():
    alerts = (
        SensorReading.query.join(Patient, SensorReading.patient_id == Patient.id)
        .options(joinedload(SensorReading.patient))
        .filter(Patient.is_archived.is_(False), SensorReading.alert_required.is_(True))
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
    log_audit("ALERT_ACKNOWLEDGED", "sensor_reading", str(reading.id), f"Mobile by {request.mobile_user.full_name}")
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
    control_bytes = None
    if "control_image" in request.files and request.files["control_image"].filename:
        control_bytes = request.files["control_image"].read()
    elif request.is_json:
        ctrl_b64 = (request.get_json(silent=True) or {}).get("control_image_base64", "")
        if "," in ctrl_b64:
            ctrl_b64 = ctrl_b64.split(",", 1)[1]
        if ctrl_b64:
            try:
                control_bytes = base64.b64decode(ctrl_b64)
            except Exception:
                return jsonify({"error": "Invalid base64 control image."}), 400
    try:
        return jsonify(analyze_flap_image_bytes(image_bytes, control_image_bytes=control_bytes))
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400


@bp.route("/mobile/patients/<int:patient_id>/reading", methods=["POST"])
@mobile_auth_required
def mobile_add_reading(patient_id: int):
    patient = db.session.get(Patient, patient_id)
    if not patient or patient.is_archived:
        return jsonify({"error": "Patient not found."}), 404
    data = request.get_json(silent=True) or {}

    def _opt_float(key: str) -> Optional[float]:
        raw = data.get(key)
        if raw is None or str(raw).strip() == "":
            return None
        return float(raw)

    try:
        ir_temp = _opt_float("ir_flap_temp_c")
        control_temp = _opt_float("control_skin_temp_c")
        spo2 = _opt_float("spo2_pct")
        doppler = _opt_float("doppler_flow_cm_s")
        hours_post_op = float(data.get("hours_post_op", patient.hours_post_op))
        redness = float(data.get("visual_redness_index", 0.14))
        cyanosis = float(data.get("visual_cyanosis_index", 0.03))
        pallor = float(data.get("visual_pallor_index", 0.42))
    except (ValueError, TypeError):
        return jsonify({"error": "Sensor fields must be valid numbers."}), 400

    if sum(v is not None for v in (ir_temp, control_temp, spo2, doppler)) < 2:
        return jsonify({"error": "Provide at least 2 bedside sensor readings to run an assessment."}), 400

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
        history=_recent_patient_history(patient),
    )
    reading = _save_prediction_reading(patient, request.mobile_user.id, pred, "Android Mobile App")
    log_audit("READING_LOGGED", "sensor_reading", str(reading.id), f"Mobile Patient={patient.mrn}")
    return jsonify({"reading": reading.to_dict()}), 201


@bp.route("/mobile/patients/<int:patient_id>/simulate", methods=["POST"])
@mobile_auth_required
def mobile_simulate(patient_id: int):
    patient = db.session.get(Patient, patient_id)
    if not patient or patient.is_archived:
        return jsonify({"error": "Patient not found."}), 404
    data = request.get_json(silent=True) or {}
    scenario = str(data.get("scenario", "arterial_deteriorating"))
    step = int(data.get("step", 7))
    if scenario not in SCENARIOS:
        return jsonify({"error": "Invalid scenario."}), 400
    sim = generate_simulated_reading(scenario=scenario, step=step)
    sim_label = {
        "normal": "Normal",
        "venous_deteriorating": "Venous",
        "arterial_deteriorating": "Arterial",
    }.get(scenario, scenario)
    reading = _save_prediction_reading(
        patient, request.mobile_user.id, sim["prediction"], f"Mobile Simulator ({sim_label})"
    )
    return jsonify({"reading": reading.to_dict()}), 201


@bp.route("/mobile/outcomes", methods=["POST"])
@mobile_auth_required
@mobile_roles_required(ENROLLMENT_ROLES)
def mobile_log_outcome():
    data = request.get_json(silent=True) or {}
    try:
        patient_id = int(data.get("patient_id", 0))
    except (ValueError, TypeError):
        return jsonify({"error": "Invalid patient_id."}), 400
    verified_outcome = str(data.get("verified_outcome", "")).strip()
    surgeon_notes = str(data.get("surgeon_notes", "")).strip()[:300]

    patient = db.session.get(Patient, patient_id)
    if not patient or patient.is_archived or verified_outcome not in VALID_OUTCOMES:
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
    log_audit("OUTCOME_LOGGED", "postop_outcome", str(outcome.id), f"Mobile Patient={patient.mrn}")
    return jsonify({"outcome": outcome.to_dict()}), 201


@bp.route("/mobile/patients/<int:patient_id>", methods=["DELETE"])
@bp.route("/mobile/patients/<int:patient_id>/delete", methods=["POST"])
@mobile_auth_required
@mobile_roles_required(PRIVILEGED_ROLES)
def mobile_delete_patient(patient_id: int):
    patient = db.session.get(Patient, patient_id)
    if not patient or patient.is_archived:
        return jsonify({"error": "Patient not found."}), 404
    name = patient.full_name
    patient.is_archived = True
    patient.archived_at = utc_now()
    patient.archived_by_id = request.mobile_user.id
    db.session.commit()
    log_audit("PATIENT_ARCHIVED", "patient", str(patient.id), f"Mobile MRN={patient.mrn}")
    return jsonify({"status": "deleted", "archived": True, "patient_id": patient_id, "full_name": name})


@bp.route("/mobile/patients/<int:patient_id>/readings", methods=["GET"])
@mobile_auth_required
def mobile_patient_readings(patient_id: int):
    patient = db.session.get(Patient, patient_id)
    if not patient or patient.is_archived:
        return jsonify({"error": "Patient not found."}), 404
    readings = (
        SensorReading.query.filter_by(patient_id=patient.id)
        .order_by(SensorReading.created_at.desc())
        .limit(20)
        .all()
    )
    return jsonify({"patient": patient.to_dict(), "readings": [r.to_dict() for r in readings]})


@bp.route("/mobile/profile", methods=["GET"])
@mobile_auth_required
def mobile_profile():
    _, meta = load_model_and_metadata()
    patients_count = (
        db.session.query(func.count(Patient.id))
        .filter(Patient.is_archived.is_(False))
        .scalar()
        or 0
    )
    unack_alerts = (
        db.session.query(func.count(SensorReading.id))
        .join(Patient, SensorReading.patient_id == Patient.id)
        .filter(
            Patient.is_archived.is_(False),
            SensorReading.alert_required.is_(True),
            SensorReading.alert_acknowledged.is_(False),
        )
        .scalar()
        or 0
    )
    return jsonify(
        {
            "user": request.mobile_user.to_dict(),
            "patients_count": int(patients_count),
            "unacknowledged_alerts": int(unack_alerts),
            "model_version": meta["version"],
            "model_accuracy": meta["accuracy"],
        }
    )


@bp.route("/mobile/profile/password", methods=["POST"])
@mobile_auth_required
def mobile_change_password():
    data = request.get_json(silent=True) or {}
    current_pw = str(data.get("current_password", ""))
    new_pw = str(data.get("new_password", ""))
    if not request.mobile_user.check_password(current_pw):
        return jsonify({"error": "Current password is incorrect."}), 400
    if len(new_pw) < 8:
        return jsonify({"error": "New password must be at least 8 characters."}), 400
    request.mobile_user.set_password(new_pw)
    request.mobile_user.bump_token_version()
    db.session.commit()
    new_token = generate_mobile_token(request.mobile_user)
    log_audit("PASSWORD_CHANGED", "user", str(request.mobile_user.id), "Mobile password updated; prior tokens revoked")
    return jsonify({"status": "password_updated", "token": new_token})


@bp.route("/mobile/ward/load-samples", methods=["POST"])
@mobile_auth_required
@mobile_roles_required(PRIVILEGED_ROLES)
def mobile_load_samples():
    created = load_sample_ward_cases(
        assigned_surgeon_id=request.mobile_user.id,
        recorded_by_id=request.mobile_user.id,
    )
    log_audit("SAMPLES_LOADED", "ward", "samples", f"Mobile created/restored={created}")
    active_count = Patient.query.filter(Patient.is_archived.is_(False)).count()
    return jsonify(
        {
            "status": "ok",
            "created_count": created,
            "patients_count": active_count,
        }
    )


@bp.route("/mobile/ward/clear", methods=["POST"])
@mobile_auth_required
@mobile_roles_required(PRIVILEGED_ROLES)
def mobile_clear_ward():
    removed = clear_all_ward_patients(archived_by_id=request.mobile_user.id)
    log_audit("WARD_ARCHIVED", "ward", "all", f"Mobile archived={removed}")
    return jsonify({"status": "cleared", "archived": True, "removed_count": removed})


@bp.route("/mobile/audit", methods=["GET"])
@mobile_auth_required
@mobile_roles_required(PRIVILEGED_ROLES)
def mobile_audit_logs():
    logs = AuditLog.query.order_by(AuditLog.created_at.desc()).limit(60).all()
    return jsonify({"logs": [entry.to_dict() for entry in logs]})


@bp.route("/mobile/profile/photo", methods=["POST"])
@mobile_auth_required
def mobile_update_profile_photo():
    if request.is_json:
        data = request.get_json(silent=True) or {}
        if data.get("remove") is True:
            request.mobile_user.profile_photo = None
            db.session.commit()
            log_audit("PROFILE_PHOTO_REMOVED", "user", str(request.mobile_user.id), "Mobile")
            return jsonify({"status": "removed", "user": request.mobile_user.to_dict()})
        raw_b64 = str(data.get("image_base64", ""))
        if "," in raw_b64:
            raw_b64 = raw_b64.split(",", 1)[1]
        if not raw_b64:
            return jsonify({"error": "No profile image provided."}), 400
        try:
            image_bytes = base64.b64decode(raw_b64)
        except Exception:
            return jsonify({"error": "Invalid base64 image."}), 400
    elif "photo" in request.files and request.files["photo"].filename:
        image_bytes = request.files["photo"].read()
    else:
        return jsonify({"error": "No profile image provided."}), 400

    try:
        request.mobile_user.profile_photo = _encode_profile_avatar(image_bytes)
        db.session.commit()
        log_audit("PROFILE_PHOTO_UPDATED", "user", str(request.mobile_user.id), "Mobile")
        return jsonify({"status": "updated", "user": request.mobile_user.to_dict()})
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400


@bp.route("/mobile/sessions/revoke-all", methods=["POST"])
@mobile_auth_required
def mobile_revoke_all_sessions():
    data = request.get_json(silent=True) or {}
    include_current = bool(data.get("include_current", False))
    request.mobile_user.bump_token_version()
    db.session.commit()
    log_audit(
        "SESSIONS_REVOKED_ALL",
        "user",
        str(request.mobile_user.id),
        f"Mobile revoke all sessions (include_current={include_current})",
        actor=request.mobile_user,
    )
    if include_current:
        return jsonify({"status": "all_sessions_revoked"})
    return jsonify({"status": "other_sessions_revoked", "token": generate_mobile_token(request.mobile_user)})


@bp.route("/mobile/account/delete", methods=["POST"])
@mobile_auth_required
def mobile_delete_account():
    data = request.get_json(silent=True) or {}
    password = str(data.get("password", ""))
    if not request.mobile_user.check_password(password):
        return jsonify({"error": "Incorrect password. Account deletion was not scheduled."}), 400

    deadline = schedule_user_deletion(request.mobile_user)
    revoke_mobile_token(getattr(request, "mobile_token_jti", None), request.mobile_user.id)
    log_audit(
        "ACCOUNT_DELETION_SCHEDULED",
        "user",
        str(request.mobile_user.id),
        f"Mobile request; permanent purge at {deadline.isoformat()}",
        actor=request.mobile_user,
    )
    return jsonify(
        {
            "status": "deletion_scheduled",
            "grace_days": ACCOUNT_DELETION_GRACE_DAYS,
            "scheduled_deletion_at": deadline.isoformat(),
            "message": (
                f"Account scheduled for permanent deletion on {deadline.strftime('%d %b %Y, %H:%M UTC')}. "
                "Sign back in anytime before then to cancel and restore."
            ),
        }
    )
