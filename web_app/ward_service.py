"""
Fast Ward Query, Sample Case Seeding, and Soft-Archive Cleanup Service for FlapGuard AI.
Preserves historical patient records and readings for clinical audit traceability.
"""

import json
from typing import Dict, List, Optional

from sqlalchemy import func

from ml_engine.fusion import classify_visual_triage
from ml_engine.model_service import predict_flap_status
from web_app.models import Patient, SensorReading, User, db, utc_now


SAMPLE_WARD_CASES = [
    (
        dict(
            mrn="MRN-2026-101",
            full_name="Ramesh Kumar",
            age=54,
            flap_type="Radial Forearm Free Flap",
            surgery_site="Left Buccal Mucosa",
            hours_post_op=10.0,
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
        dict(
            mrn="MRN-2026-102",
            full_name="Meena Devi",
            age=47,
            flap_type="Fibula Osteocutaneous Flap",
            surgery_site="Right Mandibular Reconstruction",
            hours_post_op=18.5,
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
        dict(
            mrn="MRN-2026-103",
            full_name="Karthik Sundaram",
            age=61,
            flap_type="Anterolateral Thigh (ALT) Flap",
            surgery_site="Anterior Maxilla",
            hours_post_op=6.0,
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


def fetch_patients_with_latest_readings(include_archived: bool = False) -> List[Dict[str, object]]:
    """
    Fetch active patients along with their latest sensor reading in constant query count
    (avoids N+1 round trips over remote PostgreSQL).
    """
    query = Patient.query
    if not include_archived:
        query = query.filter(Patient.is_archived.is_(False))
    patients = query.order_by(Patient.created_at.desc()).all()
    if not patients:
        return []

    patient_ids = [p.id for p in patients]
    surgeon_ids = {p.assigned_surgeon_id for p in patients if p.assigned_surgeon_id}

    surgeons_by_id = {}
    if surgeon_ids:
        surgeons = User.query.filter(User.id.in_(surgeon_ids)).all()
        surgeons_by_id = {u.id: u.full_name for u in surgeons}

    latest_ids_subq = (
        db.session.query(
            SensorReading.patient_id,
            func.max(SensorReading.id).label("max_id"),
        )
        .filter(SensorReading.patient_id.in_(patient_ids))
        .group_by(SensorReading.patient_id)
        .subquery()
    )

    latest_readings = (
        SensorReading.query.join(
            latest_ids_subq,
            SensorReading.id == latest_ids_subq.c.max_id,
        ).all()
    )
    readings_by_patient = {r.patient_id: r for r in latest_readings}

    serialized = []
    for p in patients:
        latest = readings_by_patient.get(p.id)
        latest_dict = None
        if latest is not None:
            latest_dict = {
                "id": latest.id,
                "patient_id": latest.patient_id,
                "patient_name": p.full_name,
                "patient_mrn": p.mrn,
                "ir_flap_temp_c": latest.ir_flap_temp_c,
                "control_skin_temp_c": latest.control_skin_temp_c,
                "temp_delta_c": latest.temp_delta_c,
                "spo2_pct": latest.spo2_pct,
                "doppler_flow_cm_s": latest.doppler_flow_cm_s,
                "visual_redness_index": latest.visual_redness_index,
                "visual_cyanosis_index": latest.visual_cyanosis_index,
                "visual_pallor_index": latest.visual_pallor_index,
                "visual_perfusion_score": latest.visual_perfusion_score,
                "visual_triage_label": latest.visual_triage_label,
                "perfusion_fusion_index": latest.perfusion_fusion_index,
                "hours_post_op": latest.hours_post_op,
                "predicted_class": latest.predicted_class,
                "risk_tier": latest.risk_tier,
                "risk_score_pct": latest.risk_score_pct,
                "compromise_probability": latest.compromise_probability,
                "alert_required": latest.alert_required,
                "alert_message": latest.alert_message,
                "alert_acknowledged": latest.alert_acknowledged,
                "acknowledged_by": latest.acknowledged_by,
                "clinical_notes": latest.clinical_notes,
                "source_mode": latest.source_mode,
                "model_version": latest.model_version,
                "created_at": latest.created_at.isoformat() if latest.created_at else None,
            }

        serialized.append(
            {
                "id": p.id,
                "mrn": p.mrn,
                "full_name": p.full_name,
                "age": p.age,
                "flap_type": p.flap_type,
                "surgery_site": p.surgery_site,
                "hours_post_op": latest.hours_post_op if latest else p.hours_post_op,
                "assigned_surgeon": surgeons_by_id.get(p.assigned_surgeon_id, "Unassigned"),
                "is_archived": bool(p.is_archived),
                "latest_reading": latest_dict,
                "created_at": p.created_at.isoformat() if p.created_at else None,
            }
        )

    return serialized


def load_sample_ward_cases(
    assigned_surgeon_id: Optional[int] = None,
    recorded_by_id: Optional[int] = None,
) -> int:
    """
    Insert or restore up to 3 realistic OMFS/microsurgical sample patients on demand.
    Skips any active MRN so repeated clicks are safe.
    """
    if assigned_surgeon_id is None:
        surgeon = User.query.filter_by(role="Surgeon").first() or User.query.first()
        assigned_surgeon_id = surgeon.id if surgeon else None

    if recorded_by_id is None:
        recorder = User.query.filter_by(role="Nurse").first() or User.query.first()
        recorded_by_id = recorder.id if recorder else assigned_surgeon_id

    created_count = 0
    for patient_data, params in SAMPLE_WARD_CASES:
        existing = Patient.query.filter_by(mrn=patient_data["mrn"]).first()
        if existing is not None:
            if not existing.is_archived:
                continue
            existing.is_archived = False
            existing.archived_at = None
            existing.archived_by_id = None
            patient = existing
        else:
            patient = Patient(
                assigned_surgeon_id=assigned_surgeon_id,
                **patient_data,
            )
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
            source_mode="Sample Clinical Baseline",
            model_version=pred["model_version"],
        )
        db.session.add(reading)
        created_count += 1

    if created_count > 0:
        db.session.commit()
    return created_count


def clear_all_ward_patients(archived_by_id: Optional[int] = None) -> int:
    """Soft-archive all active patients without destroying historical clinical data."""
    active_patients = Patient.query.filter(Patient.is_archived.is_(False)).all()
    now = utc_now()
    for p in active_patients:
        p.is_archived = True
        p.archived_at = now
        p.archived_by_id = archived_by_id
    db.session.commit()
    return len(active_patients)
