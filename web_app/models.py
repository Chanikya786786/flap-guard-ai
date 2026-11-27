"""
Relational Database Models for FlapGuard AI.
Includes soft-delete patient archiving, server-side token revocation,
and an append-only clinical & security AuditLog.
"""

import json
from datetime import datetime, timezone

from flask_login import UserMixin
from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import check_password_hash, generate_password_hash


db = SQLAlchemy()


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class User(UserMixin, db.Model):
    __tablename__ = "users"

    id = db.Column(db.Integer, primary_key=True)
    full_name = db.Column(db.String(120), nullable=False)
    email = db.Column(db.String(150), unique=True, nullable=False, index=True)
    role = db.Column(db.String(40), nullable=False, default="Surgeon")
    password_hash = db.Column(db.String(256), nullable=False)
    profile_photo = db.Column(db.Text, nullable=True)
    token_version = db.Column(db.Integer, nullable=False, default=1)
    created_at = db.Column(db.DateTime(timezone=True), default=utc_now, nullable=False)

    def set_password(self, raw_password: str) -> None:
        self.password_hash = generate_password_hash(raw_password)

    def check_password(self, raw_password: str) -> bool:
        return check_password_hash(self.password_hash, raw_password)

    def bump_token_version(self) -> None:
        self.token_version = int(self.token_version or 1) + 1

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "full_name": self.full_name,
            "email": self.email,
            "role": self.role,
            "profile_photo": self.profile_photo,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


class Patient(db.Model):
    __tablename__ = "patients"

    id = db.Column(db.Integer, primary_key=True)
    mrn = db.Column(db.String(50), unique=True, nullable=False, index=True)
    full_name = db.Column(db.String(120), nullable=False)
    age = db.Column(db.Integer, nullable=False)
    flap_type = db.Column(db.String(100), nullable=False)
    surgery_site = db.Column(db.String(120), nullable=False)
    hours_post_op = db.Column(db.Float, nullable=False, default=1.0)
    assigned_surgeon_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    is_archived = db.Column(db.Boolean, nullable=False, default=False, index=True)
    archived_at = db.Column(db.DateTime(timezone=True), nullable=True)
    archived_by_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    created_at = db.Column(db.DateTime(timezone=True), default=utc_now, nullable=False)

    assigned_surgeon = db.relationship("User", foreign_keys=[assigned_surgeon_id])
    archived_by = db.relationship("User", foreign_keys=[archived_by_id])
    readings = db.relationship(
        "SensorReading",
        backref="patient",
        lazy="dynamic",
        cascade="all, delete-orphan",
        order_by="SensorReading.created_at.desc()",
    )
    outcomes = db.relationship(
        "PostOpOutcome",
        backref="patient",
        lazy="dynamic",
        cascade="all, delete-orphan",
        order_by="PostOpOutcome.created_at.desc()",
    )

    @property
    def latest_reading(self):
        return self.readings.first()

    def to_dict(self) -> dict:
        latest = self.latest_reading
        return {
            "id": self.id,
            "mrn": self.mrn,
            "full_name": self.full_name,
            "age": self.age,
            "flap_type": self.flap_type,
            "surgery_site": self.surgery_site,
            "hours_post_op": latest.hours_post_op if latest else self.hours_post_op,
            "assigned_surgeon": self.assigned_surgeon.full_name if self.assigned_surgeon else "Unassigned",
            "is_archived": bool(self.is_archived),
            "latest_reading": latest.to_dict() if latest else None,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


class SensorReading(db.Model):
    __tablename__ = "sensor_readings"

    id = db.Column(db.Integer, primary_key=True)
    patient_id = db.Column(db.Integer, db.ForeignKey("patients.id"), nullable=False, index=True)
    recorded_by_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)

    ir_flap_temp_c = db.Column(db.Float, nullable=False)
    control_skin_temp_c = db.Column(db.Float, nullable=False)
    temp_delta_c = db.Column(db.Float, nullable=False)
    spo2_pct = db.Column(db.Float, nullable=False)
    doppler_flow_cm_s = db.Column(db.Float, nullable=False)

    visual_redness_index = db.Column(db.Float, nullable=False, default=0.14)
    visual_cyanosis_index = db.Column(db.Float, nullable=False, default=0.03)
    visual_pallor_index = db.Column(db.Float, nullable=False, default=0.42)
    visual_perfusion_score = db.Column(db.Float, nullable=False, default=85.0)
    visual_triage_label = db.Column(db.String(80), nullable=False, default="Healthy Pink Perfusion")

    perfusion_fusion_index = db.Column(db.Float, nullable=False)
    hours_post_op = db.Column(db.Float, nullable=False, default=12.0)

    predicted_class = db.Column(db.String(50), nullable=False)
    risk_tier = db.Column(db.String(20), nullable=False)
    risk_score_pct = db.Column(db.Float, nullable=False)
    compromise_probability = db.Column(db.Float, nullable=False)

    alert_required = db.Column(db.Boolean, nullable=False, default=False)
    alert_message = db.Column(db.String(300), nullable=False, default="")
    alert_acknowledged = db.Column(db.Boolean, nullable=False, default=False)
    acknowledged_by = db.Column(db.String(120), nullable=True)

    clinical_notes_json = db.Column(db.Text, nullable=False, default="[]")
    source_mode = db.Column(db.String(80), nullable=False, default="Manual + Camera")
    model_version = db.Column(db.String(20), nullable=False, default="v1.0")
    created_at = db.Column(db.DateTime(timezone=True), default=utc_now, nullable=False, index=True)

    @property
    def clinical_notes(self) -> list:
        try:
            return json.loads(self.clinical_notes_json or "[]")
        except ValueError:
            return []

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "patient_id": self.patient_id,
            "patient_name": self.patient.full_name if self.patient else "",
            "patient_mrn": self.patient.mrn if self.patient else "",
            "ir_flap_temp_c": self.ir_flap_temp_c,
            "control_skin_temp_c": self.control_skin_temp_c,
            "temp_delta_c": self.temp_delta_c,
            "spo2_pct": self.spo2_pct,
            "doppler_flow_cm_s": self.doppler_flow_cm_s,
            "visual_redness_index": self.visual_redness_index,
            "visual_cyanosis_index": self.visual_cyanosis_index,
            "visual_pallor_index": self.visual_pallor_index,
            "visual_perfusion_score": self.visual_perfusion_score,
            "visual_triage_label": self.visual_triage_label,
            "perfusion_fusion_index": self.perfusion_fusion_index,
            "hours_post_op": self.hours_post_op,
            "predicted_class": self.predicted_class,
            "risk_tier": self.risk_tier,
            "risk_score_pct": self.risk_score_pct,
            "compromise_probability": self.compromise_probability,
            "alert_required": self.alert_required,
            "alert_message": self.alert_message,
            "alert_acknowledged": self.alert_acknowledged,
            "acknowledged_by": self.acknowledged_by,
            "clinical_notes": self.clinical_notes,
            "source_mode": self.source_mode,
            "model_version": self.model_version,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


class PostOpOutcome(db.Model):
    __tablename__ = "postop_outcomes"

    id = db.Column(db.Integer, primary_key=True)
    patient_id = db.Column(db.Integer, db.ForeignKey("patients.id"), nullable=False, index=True)
    reading_id = db.Column(db.Integer, db.ForeignKey("sensor_readings.id"), nullable=True)
    logged_by_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    verified_outcome = db.Column(db.String(50), nullable=False)
    surgeon_notes = db.Column(db.String(300), nullable=False, default="")
    created_at = db.Column(db.DateTime(timezone=True), default=utc_now, nullable=False)

    reading = db.relationship("SensorReading", foreign_keys=[reading_id])
    logged_by = db.relationship("User", foreign_keys=[logged_by_id])

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "patient_id": self.patient_id,
            "patient_name": self.patient.full_name if self.patient else "",
            "reading_id": self.reading_id,
            "verified_outcome": self.verified_outcome,
            "surgeon_notes": self.surgeon_notes,
            "logged_by": self.logged_by.full_name if self.logged_by else "Clinician",
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


class ModelVersionLog(db.Model):
    __tablename__ = "model_versions"

    id = db.Column(db.Integer, primary_key=True)
    version = db.Column(db.String(20), nullable=False)
    accuracy = db.Column(db.Float, nullable=False)
    macro_f1 = db.Column(db.Float, nullable=False)
    total_samples = db.Column(db.Integer, nullable=False)
    postop_outcomes_added = db.Column(db.Integer, nullable=False, default=0)
    trained_at_utc = db.Column(db.String(60), nullable=False)
    notes = db.Column(db.String(250), nullable=False, default="")

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "version": self.version,
            "accuracy": self.accuracy,
            "macro_f1": self.macro_f1,
            "total_samples": self.total_samples,
            "postop_outcomes_added": self.postop_outcomes_added,
            "trained_at_utc": self.trained_at_utc,
            "notes": self.notes,
        }


class RevokedToken(db.Model):
    __tablename__ = "revoked_tokens"

    id = db.Column(db.Integer, primary_key=True)
    jti = db.Column(db.String(64), unique=True, nullable=False, index=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    revoked_at = db.Column(db.DateTime(timezone=True), default=utc_now, nullable=False)


class AuditLog(db.Model):
    __tablename__ = "audit_logs"

    id = db.Column(db.Integer, primary_key=True)
    actor_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    actor_email = db.Column(db.String(150), nullable=False, default="anonymous")
    actor_role = db.Column(db.String(40), nullable=False, default="Unknown")
    action = db.Column(db.String(60), nullable=False, index=True)
    resource_type = db.Column(db.String(50), nullable=False, default="")
    resource_id = db.Column(db.String(60), nullable=False, default="")
    details = db.Column(db.String(400), nullable=False, default="")
    ip_address = db.Column(db.String(64), nullable=False, default="")
    created_at = db.Column(db.DateTime(timezone=True), default=utc_now, nullable=False, index=True)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "actor_id": self.actor_id,
            "actor_email": self.actor_email,
            "actor_role": self.actor_role,
            "action": self.action,
            "resource_type": self.resource_type,
            "resource_id": self.resource_id,
            "details": self.details,
            "ip_address": self.ip_address,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }
