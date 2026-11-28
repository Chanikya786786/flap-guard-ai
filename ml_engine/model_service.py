"""
Calibrated Local AI Model Service for FlapGuard AI:
- Platt-calibrated (CalibratedClassifierCV) multisensor Random Forest
- Holdout Macro-F1 & Brier Score safety gate for continuous learning
- Temporal trend integration (patient-relative baselines & hourly slopes)
- Missing-modality graceful handling & Out-of-Distribution (OOD) detection
- In-memory serialization helpers for persistent PostgreSQL ModelArtifact storage
"""

import hashlib
import io
import json
import math
import os
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

import joblib
import numpy as np
import pandas as pd
from sklearn.calibration import CalibratedClassifierCV
from sklearn.ensemble import RandomForestClassifier
from sklearn.metrics import (
    accuracy_score,
    confusion_matrix,
    precision_recall_fscore_support,
)
from sklearn.model_selection import train_test_split

from ml_engine.fusion import FEATURE_COLUMNS, compute_fusion_features
from ml_engine.generate_dataset import generate_flap_dataset
from ml_engine.trend_engine import analyze_temporal_trend, apply_trend_escalation


BASE_DATASET_PATH = os.path.join("ml_engine", "data", "flap_multisensor_dataset.csv")
MODEL_DIR = os.path.join("ml_engine", "models")
MODEL_PATH = os.path.join(MODEL_DIR, "flap_model.joblib")
METADATA_PATH = os.path.join(MODEL_DIR, "model_metadata.json")

LABEL_TO_NAME = {
    0: "Normal",
    1: "Venous Compromise",
    2: "Arterial Compromise",
}
NAME_TO_LABEL = {v: k for k, v in LABEL_TO_NAME.items()}

# Clinically neutral defaults used strictly when a modality is missing (flagged to clinician)
NEUTRAL_MODALITY_DEFAULTS = {
    "ir_flap_temp_c": 36.2,
    "control_skin_temp_c": 36.6,
    "spo2_pct": 96.0,
    "doppler_flow_cm_s": 20.0,
    "visual_redness_index": 0.14,
    "visual_cyanosis_index": 0.03,
    "visual_pallor_index": 0.42,
    "hours_post_op": 12.0,
}

# Physiological OOD guard rails beyond standard free-flap monitoring envelopes
PHYSIOLOGICAL_BOUNDS = {
    "ir_flap_temp_c": (28.0, 40.5),
    "control_skin_temp_c": (33.5, 40.5),
    "temp_delta_c": (-1.5, 6.5),
    "spo2_pct": (45.0, 100.0),
    "doppler_flow_cm_s": (0.0, 42.0),
}

_MODEL_CACHE: Dict[str, object] = {"model": None, "metadata": None}


def _next_version(current_version: Optional[str]) -> str:
    if not current_version:
        return "v2.0"
    try:
        major, minor = current_version.lstrip("v").split(".")
        return f"v{int(major)}.{int(minor) + 1}"
    except ValueError:
        return "v2.1"


def _multiclass_brier_score(y_true: np.ndarray, probs: np.ndarray) -> float:
    n_classes = probs.shape[1]
    one_hot = np.eye(n_classes)[y_true]
    return float(np.mean(np.sum((probs - one_hot) ** 2, axis=1)))


def _extract_feature_importances(model) -> Dict[str, float]:
    if hasattr(model, "calibrated_classifiers_") and model.calibrated_classifiers_:
        arrays = [
            cc.estimator.feature_importances_
            for cc in model.calibrated_classifiers_
            if hasattr(cc, "estimator") and hasattr(cc.estimator, "feature_importances_")
        ]
        if arrays:
            mean_imp = np.mean(arrays, axis=0)
            return {
                col: round(float(imp), 4)
                for col, imp in sorted(
                    zip(FEATURE_COLUMNS, mean_imp),
                    key=lambda item: item[1],
                    reverse=True,
                )
            }
    if hasattr(model, "feature_importances_"):
        return {
            col: round(float(imp), 4)
            for col, imp in sorted(
                zip(FEATURE_COLUMNS, model.feature_importances_),
                key=lambda item: item[1],
                reverse=True,
            )
        }
    return {col: round(1.0 / len(FEATURE_COLUMNS), 4) for col in FEATURE_COLUMNS}


def _compute_feature_stats(X: pd.DataFrame) -> Dict[str, Dict[str, float]]:
    stats: Dict[str, Dict[str, float]] = {}
    for col in FEATURE_COLUMNS:
        vals = X[col].astype(float)
        std_val = float(vals.std(ddof=0))
        stats[col] = {
            "mean": round(float(vals.mean()), 4),
            "std": round(std_val if std_val > 1e-6 else 1.0, 4),
            "p01": round(float(np.percentile(vals, 1)), 4),
            "p99": round(float(np.percentile(vals, 99)), 4),
        }
    return stats


def _clean_extra_records(extra_records: Optional[List[Dict[str, object]]]) -> List[Dict[str, object]]:
    cleaned_rows: List[Dict[str, object]] = []
    if not extra_records:
        return cleaned_rows
    for rec in extra_records:
        label_val = rec.get("label")
        if isinstance(label_val, str) and label_val in NAME_TO_LABEL:
            label_val = NAME_TO_LABEL[label_val]
        if label_val not in LABEL_TO_NAME:
            continue

        if all(col in rec for col in FEATURE_COLUMNS):
            row = {col: float(rec[col]) for col in FEATURE_COLUMNS}
        else:
            row = compute_fusion_features(
                ir_flap_temp_c=float(rec["ir_flap_temp_c"]),
                control_skin_temp_c=float(rec["control_skin_temp_c"]),
                spo2_pct=float(rec["spo2_pct"]),
                doppler_flow_cm_s=float(rec["doppler_flow_cm_s"]),
                visual_redness_index=float(rec.get("visual_redness_index", 0.14)),
                visual_cyanosis_index=float(rec.get("visual_cyanosis_index", 0.03)),
                visual_pallor_index=float(rec.get("visual_pallor_index", 0.42)),
                hours_post_op=float(rec.get("hours_post_op", 12.0)),
            )
        row["label"] = int(label_val)
        row["label_name"] = LABEL_TO_NAME[int(label_val)]
        cleaned_rows.append(row)
    return cleaned_rows


def serialize_model_bytes(model) -> Tuple[bytes, str]:
    """Serialize trained model to compressed bytes + SHA-256 digest for DB persistence."""
    buf = io.BytesIO()
    joblib.dump(model, buf, compress=3)
    raw = buf.getvalue()
    digest = hashlib.sha256(raw).hexdigest()
    return raw, digest


def load_model_from_bytes(blob: bytes, metadata: Dict[str, object], expected_sha256: Optional[str] = None) -> bool:
    """Verify checksum, deserialize model from DB blob, and refresh local cache + disk mirror."""
    if not blob:
        return False
    actual_sha = hashlib.sha256(blob).hexdigest()
    if expected_sha256 and actual_sha != expected_sha256:
        return False
    model = joblib.load(io.BytesIO(blob))
    _MODEL_CACHE["model"] = model
    _MODEL_CACHE["metadata"] = metadata
    try:
        os.makedirs(MODEL_DIR, exist_ok=True)
        with open(MODEL_PATH, "wb") as fh:
            fh.write(blob)
        with open(METADATA_PATH, "w", encoding="utf-8") as fh:
            json.dump(metadata, fh, indent=2)
    except OSError:
        pass
    return True


def _fit_calibrated_candidate(
    extra_records: Optional[List[Dict[str, object]]] = None,
    version: Optional[str] = None,
) -> Tuple[CalibratedClassifierCV, Dict[str, object]]:
    if not os.path.exists(BASE_DATASET_PATH):
        generate_flap_dataset(output_csv=BASE_DATASET_PATH)

    base_df = pd.read_csv(BASE_DATASET_PATH)
    base_count = len(base_df)

    # Fixed clean stratified holdout set (never contaminated by user-supplied outcome labels)
    X_base = base_df[FEATURE_COLUMNS]
    y_base = base_df["label"].astype(int)
    X_train_base, X_holdout, y_train_base, y_holdout = train_test_split(
        X_base, y_base, test_size=0.20, random_state=42, stratify=y_base
    )

    cleaned_rows = _clean_extra_records(extra_records)
    outcomes_count = len(cleaned_rows)

    if cleaned_rows:
        extra_df = pd.DataFrame(cleaned_rows)
        X_train = pd.concat([X_train_base, extra_df[FEATURE_COLUMNS]], ignore_index=True)
        y_train = pd.concat([y_train_base, extra_df["label"].astype(int)], ignore_index=True)
    else:
        X_train = X_train_base
        y_train = y_train_base

    base_rf = RandomForestClassifier(
        n_estimators=120,
        max_depth=12,
        min_samples_leaf=2,
        random_state=42,
        n_jobs=-1,
    )
    calibrated = CalibratedClassifierCV(estimator=base_rf, method="sigmoid", cv=3)
    calibrated.fit(X_train, y_train)

    y_pred = calibrated.predict(X_holdout)
    y_probs = calibrated.predict_proba(X_holdout)
    acc = float(accuracy_score(y_holdout, y_pred))
    precision, recall, f1, _ = precision_recall_fscore_support(
        y_holdout, y_pred, average="macro", zero_division=0
    )
    brier = _multiclass_brier_score(y_holdout.to_numpy(), y_probs)
    cm = confusion_matrix(y_holdout, y_pred, labels=[0, 1, 2]).tolist()

    metadata = {
        "version": version or "v2.0",
        "trained_at_utc": datetime.now(timezone.utc).isoformat(),
        "algorithm": "CalibratedRandomForest (Platt Sigmoid + Multisensor Fusion)",
        "calibration_method": "sigmoid (3-fold Platt scaling)",
        "base_samples": base_count,
        "postop_outcomes_added": outcomes_count,
        "total_samples": base_count + outcomes_count,
        "test_samples": len(y_holdout),
        "accuracy": round(acc, 4),
        "macro_precision": round(float(precision), 4),
        "macro_recall": round(float(recall), 4),
        "macro_f1": round(float(f1), 4),
        "brier_score": round(brier, 4),
        "confusion_matrix": cm,
        "classes": [LABEL_TO_NAME[0], LABEL_TO_NAME[1], LABEL_TO_NAME[2]],
        "feature_importances": _extract_feature_importances(calibrated),
        "feature_stats": _compute_feature_stats(X_base),
        "data_provenance": (
            "Research prototype trained on physiological simulation baseline + clinician-logged outcomes; "
            "requires prospective clinical validation before autonomous diagnostic use."
        ),
    }
    return calibrated, metadata


def train_and_save_model(
    extra_records: Optional[List[Dict[str, object]]] = None,
    version: Optional[str] = None,
    model_path: str = MODEL_PATH,
    metadata_path: str = METADATA_PATH,
) -> Tuple[CalibratedClassifierCV, Dict[str, object]]:
    """Train Platt-calibrated multisensor model and save artifact + metadata."""
    model, metadata = _fit_calibrated_candidate(extra_records=extra_records, version=version)

    os.makedirs(os.path.dirname(model_path), exist_ok=True)
    joblib.dump(model, model_path, compress=3)
    with open(metadata_path, "w", encoding="utf-8") as f:
        json.dump(metadata, f, indent=2)

    if model_path == MODEL_PATH:
        _MODEL_CACHE["model"] = model
        _MODEL_CACHE["metadata"] = metadata

    return model, metadata


def load_model_and_metadata(force_reload: bool = False) -> Tuple[object, Dict[str, object]]:
    """Load the saved local model into memory, upgrading uncalibrated legacy artifacts automatically."""
    if not force_reload and _MODEL_CACHE["model"] is not None and _MODEL_CACHE["metadata"] is not None:
        return _MODEL_CACHE["model"], _MODEL_CACHE["metadata"]

    if not os.path.exists(MODEL_PATH) or not os.path.exists(METADATA_PATH):
        return train_and_save_model(version="v2.0")

    with open(METADATA_PATH, "r", encoding="utf-8") as f:
        metadata = json.load(f)

    # Upgrade pre-Track-2 uncalibrated artifacts cleanly to calibrated v2.0
    if "brier_score" not in metadata or "feature_stats" not in metadata:
        return train_and_save_model(version="v2.0")

    model = joblib.load(MODEL_PATH)
    _MODEL_CACHE["model"] = model
    _MODEL_CACHE["metadata"] = metadata
    return model, metadata


def evaluate_ood(features: Dict[str, float], metadata: Dict[str, object]) -> Tuple[bool, List[str], float]:
    """Check whether inputs fall outside expected physiological / training distributions."""
    reasons: List[str] = []
    for col, (lo, hi) in PHYSIOLOGICAL_BOUNDS.items():
        val = float(features.get(col, 0.0))
        if val < lo or val > hi:
            reasons.append(f"{col}={val:.2f} outside physiological envelope [{lo}, {hi}]")

    stats = metadata.get("feature_stats") or {}
    max_z = 0.0
    for col in ("temp_delta_c", "spo2_pct", "doppler_flow_cm_s", "ir_flap_temp_c"):
        if col in stats:
            mean = float(stats[col]["mean"])
            std = max(float(stats[col]["std"]), 1e-4)
            z = abs((float(features[col]) - mean) / std)
            if z > max_z:
                max_z = z
            if z >= 5.5 and not any(col in r for r in reasons):
                reasons.append(f"{col} deviates strongly from training distribution (z={z:.1f})")

    # Cross-sensor physiological discordance guardrails (probable probe detachment / artifact)
    td = float(features.get("temp_delta_c", 0.0))
    sp = float(features.get("spo2_pct", 96.0))
    dp = float(features.get("doppler_flow_cm_s", 20.0))
    if (td >= 4.5 or sp <= 55.0) and dp >= 18.0:
        reasons.append(
            f"Discordant sensors: severe thermal/SpO2 drop (ΔT={td:.1f}°C, SpO2={sp:.1f}%) "
            f"with strong Doppler ({dp:.1f} cm/s) — verify probe contact"
        )
    if dp <= 2.0 and sp >= 95.0 and td <= 0.6:
        reasons.append(
            f"Discordant sensors: absent Doppler ({dp:.1f} cm/s) with warm, well-oxygenated tissue "
            f"(SpO2={sp:.1f}%, ΔT={td:.1f}°C) — verify Doppler placement"
        )

    return len(reasons) > 0, reasons, round(max_z, 2)


def determine_risk_tier(
    predicted_class: str,
    compromise_prob: float,
    temp_delta_c: float,
    spo2_pct: float,
    doppler_flow_cm_s: float,
) -> str:
    """Map calibrated probability + clinical safety guardrails to 4-level Risk Stratification."""
    if predicted_class == "Arterial Compromise" and (
        compromise_prob >= 0.70 or temp_delta_c >= 2.5 or doppler_flow_cm_s <= 4.0
    ):
        return "Critical"
    if predicted_class in ("Venous Compromise", "Arterial Compromise") and compromise_prob >= 0.78:
        return "Critical"
    if predicted_class in ("Venous Compromise", "Arterial Compromise") or compromise_prob >= 0.45:
        return "High"
    if compromise_prob >= 0.22 or temp_delta_c >= 1.4 or spo2_pct < 92.0 or doppler_flow_cm_s < 12.0:
        return "Moderate"
    return "Low"


def _compute_confidence_label(
    probs: np.ndarray,
    ood_flag: bool,
    missing_modalities: List[str],
) -> Tuple[str, float]:
    """Compute calibrated entropy-based reliability score and clinician-facing confidence tier."""
    clipped = np.clip(probs, 1e-9, 1.0)
    entropy = -float(np.sum(clipped * np.log(clipped)))
    max_entropy = math.log(len(probs))
    certainty = max(0.0, min(1.0, 1.0 - (entropy / max_entropy)))

    if ood_flag or len(missing_modalities) >= 2:
        return "Low — Verify Bedside", round(certainty, 3)
    if len(missing_modalities) == 1 or certainty < 0.55:
        return "Moderate — Partial Data", round(certainty, 3)
    return "High", round(certainty, 3)


def build_clinical_notes(
    features: Dict[str, float],
    predicted_class: str,
    trend_summary: Optional[Dict[str, object]] = None,
    ood_reasons: Optional[List[str]] = None,
    missing_modalities: Optional[List[str]] = None,
) -> List[str]:
    """Generate concise, surgeon-readable clinical findings behind the prediction."""
    notes: List[str] = []
    if features["temp_delta_c"] >= 2.0:
        notes.append(
            f"Significant flap cooling detected (Delta-T = {features['temp_delta_c']:.2f} C >= 2.0 C threshold)."
        )
    elif features["temp_delta_c"] >= 1.2:
        notes.append(f"Mild flap temperature drop (Delta-T = {features['temp_delta_c']:.2f} C).")

    if features["spo2_pct"] < 85.0:
        notes.append(f"Critically low tissue SpO2 ({features['spo2_pct']:.1f}%).")
    elif features["spo2_pct"] < 92.0:
        notes.append(f"Declining tissue SpO2 ({features['spo2_pct']:.1f}%).")

    if features["doppler_flow_cm_s"] <= 5.0:
        notes.append(f"Absent/severely diminished Doppler flow ({features['doppler_flow_cm_s']:.1f} cm/s).")
    elif features["doppler_flow_cm_s"] < 12.0:
        notes.append(f"Reduced Doppler blood flow ({features['doppler_flow_cm_s']:.1f} cm/s).")

    if features["visual_cyanosis_index"] >= 0.22:
        notes.append(f"Camera detected dusky/bluish congestion (cyanosis index {features['visual_cyanosis_index']:.2f}).")
    if features["visual_pallor_index"] >= 0.65 and features["visual_redness_index"] < 0.06:
        notes.append(f"Camera detected flap pallor (pallor index {features['visual_pallor_index']:.2f}).")

    if trend_summary and trend_summary.get("trend_escalation") not in (None, "None"):
        for tn in trend_summary.get("trend_notes", []):
            notes.append(f"Trend: {tn}")

    if missing_modalities:
        notes.append(
            f"Missing modality warning ({', '.join(missing_modalities)} unavailable): "
            "assessment computed with partial sensors — confirm at bedside."
        )

    if ood_reasons:
        notes.append(f"Out-of-distribution sensor warning: {ood_reasons[0]} — verify probe placement.")

    if not notes:
        notes.append(f"All multisensor modalities within healthy postoperative limits ({predicted_class}).")
    return notes


def predict_flap_status(
    ir_flap_temp_c: Optional[float],
    control_skin_temp_c: Optional[float],
    spo2_pct: Optional[float],
    doppler_flow_cm_s: Optional[float],
    visual_redness_index: Optional[float] = 0.14,
    visual_cyanosis_index: Optional[float] = 0.03,
    visual_pallor_index: Optional[float] = 0.42,
    hours_post_op: Optional[float] = 12.0,
    history: Optional[List[object]] = None,
) -> Dict[str, object]:
    """
    Run multisensor fusion + calibrated AI inference + temporal trend escalation
    + missing-modality & OOD safety guards.
    """
    model, metadata = load_model_and_metadata()

    missing_modalities: List[str] = []
    if ir_flap_temp_c is None or control_skin_temp_c is None:
        missing_modalities.append("IR Temperature")
        ir_val = NEUTRAL_MODALITY_DEFAULTS["ir_flap_temp_c"] if ir_flap_temp_c is None else float(ir_flap_temp_c)
        ctrl_val = NEUTRAL_MODALITY_DEFAULTS["control_skin_temp_c"] if control_skin_temp_c is None else float(control_skin_temp_c)
    else:
        ir_val = float(ir_flap_temp_c)
        ctrl_val = float(control_skin_temp_c)

    if spo2_pct is None:
        missing_modalities.append("Tissue SpO2")
        spo2_val = NEUTRAL_MODALITY_DEFAULTS["spo2_pct"]
    else:
        spo2_val = float(spo2_pct)

    if doppler_flow_cm_s is None:
        missing_modalities.append("Doppler Flow")
        dopp_val = NEUTRAL_MODALITY_DEFAULTS["doppler_flow_cm_s"]
    else:
        dopp_val = float(doppler_flow_cm_s)

    if visual_redness_index is None or visual_cyanosis_index is None or visual_pallor_index is None:
        missing_modalities.append("Camera Colorimetry")
    red_val = NEUTRAL_MODALITY_DEFAULTS["visual_redness_index"] if visual_redness_index is None else float(visual_redness_index)
    cya_val = NEUTRAL_MODALITY_DEFAULTS["visual_cyanosis_index"] if visual_cyanosis_index is None else float(visual_cyanosis_index)
    pal_val = NEUTRAL_MODALITY_DEFAULTS["visual_pallor_index"] if visual_pallor_index is None else float(visual_pallor_index)
    hrs_val = NEUTRAL_MODALITY_DEFAULTS["hours_post_op"] if hours_post_op is None else float(hours_post_op)

    features = compute_fusion_features(
        ir_flap_temp_c=ir_val,
        control_skin_temp_c=ctrl_val,
        spo2_pct=spo2_val,
        doppler_flow_cm_s=dopp_val,
        visual_redness_index=red_val,
        visual_cyanosis_index=cya_val,
        visual_pallor_index=pal_val,
        hours_post_op=hrs_val,
    )

    X_row = pd.DataFrame([features])[FEATURE_COLUMNS]
    probs = model.predict_proba(X_row)[0]

    pred_label = int(np.argmax(probs))
    predicted_class = LABEL_TO_NAME[pred_label]

    prob_map = {
        LABEL_TO_NAME[0]: round(float(probs[0]), 4),
        LABEL_TO_NAME[1]: round(float(probs[1]), 4),
        LABEL_TO_NAME[2]: round(float(probs[2]), 4),
    }
    compromise_prob = round(float(probs[1] + probs[2]), 4)

    static_tier = determine_risk_tier(
        predicted_class=predicted_class,
        compromise_prob=compromise_prob,
        temp_delta_c=features["temp_delta_c"],
        spo2_pct=features["spo2_pct"],
        doppler_flow_cm_s=features["doppler_flow_cm_s"],
    )

    trend_summary = analyze_temporal_trend(features, history=history)
    risk_tier = apply_trend_escalation(static_tier, str(trend_summary.get("trend_escalation", "None")))

    ood_flag, ood_reasons, ood_z_max = evaluate_ood(features, metadata)
    confidence_label, certainty_score = _compute_confidence_label(probs, ood_flag, missing_modalities)

    alert_required = risk_tier in ("High", "Critical")
    trend_tag = ""
    if trend_summary.get("trend_escalation") in ("High", "Critical") and risk_tier != static_tier:
        trend_tag = f" [Escalated by {trend_summary['trend_status']}]"

    alert_message = (
        f"URGENT [{risk_tier}]: {predicted_class} predicted "
        f"({compromise_prob * 100:.1f}% risk){trend_tag}. Immediate flap inspection advised."
        if alert_required
        else f"Stable [{risk_tier}]: {predicted_class} ({prob_map['Normal'] * 100:.1f}% normal confidence)."
    )

    return {
        "predicted_label": pred_label,
        "predicted_class": predicted_class,
        "probabilities": prob_map,
        "compromise_probability": compromise_prob,
        "risk_score_pct": round(compromise_prob * 100.0, 1),
        "risk_tier": risk_tier,
        "static_risk_tier": static_tier,
        "alert_required": alert_required,
        "alert_message": alert_message,
        "clinical_notes": build_clinical_notes(
            features,
            predicted_class,
            trend_summary=trend_summary,
            ood_reasons=ood_reasons,
            missing_modalities=missing_modalities,
        ),
        "features": features,
        "trend_summary": trend_summary,
        "trend_status": trend_summary["trend_status"],
        "ood_flag": ood_flag,
        "ood_reasons": ood_reasons,
        "ood_z_max": ood_z_max,
        "missing_modalities": missing_modalities,
        "confidence_label": confidence_label,
        "certainty_score": certainty_score,
        "model_version": metadata["version"],
    }


def retrain_from_postop_outcomes(
    outcome_records: List[Dict[str, object]],
    min_holdout_f1: float = 0.90,
    max_f1_drop: float = 0.03,
) -> Dict[str, object]:
    """
    Safe Continuous Learning with Holdout Macro-F1 Quality Gate:
    Trains a candidate calibrated model on base + verified outcomes, evaluates it on the
    clean holdout set, and rejects promotion if holdout F1 degrades past safety bounds.
    """
    old_model, old_meta = load_model_and_metadata()
    old_f1 = float(old_meta.get("macro_f1", 0.95))
    new_version = _next_version(old_meta.get("version", "v2.0"))

    cand_model, cand_meta = _fit_calibrated_candidate(
        extra_records=outcome_records,
        version=new_version,
    )
    cand_f1 = float(cand_meta["macro_f1"])
    f1_drop = round(old_f1 - cand_f1, 4)
    old_brier = float(old_meta.get("brier_score", 0.05))
    cand_brier = float(cand_meta.get("brier_score", 0.0))
    brier_inc = round(cand_brier - old_brier, 4)

    if cand_f1 < min_holdout_f1 or f1_drop > max_f1_drop or brier_inc > 0.08:
        return {
            "promoted": False,
            "rejection_reason": (
                f"Holdout safety gate blocked promotion "
                f"(candidate F1={cand_f1:.4f}, baseline F1={old_f1:.4f}, "
                f"Brier={cand_brier:.4f}, +{brier_inc:.4f})."
            ),
            "old_version": old_meta["version"],
            "new_version": old_meta["version"],
            "old_accuracy": old_meta["accuracy"],
            "new_accuracy": old_meta["accuracy"],
            "candidate_macro_f1": cand_f1,
            "outcomes_incorporated": 0,
            "total_samples": old_meta["total_samples"],
            "metadata": old_meta,
            "model": old_model,
        }

    # Gate passed — promote candidate to disk + memory cache
    os.makedirs(MODEL_DIR, exist_ok=True)
    joblib.dump(cand_model, MODEL_PATH, compress=3)
    with open(METADATA_PATH, "w", encoding="utf-8") as f:
        json.dump(cand_meta, f, indent=2)
    _MODEL_CACHE["model"] = cand_model
    _MODEL_CACHE["metadata"] = cand_meta

    return {
        "promoted": True,
        "rejection_reason": "",
        "old_version": old_meta["version"],
        "new_version": cand_meta["version"],
        "old_accuracy": old_meta["accuracy"],
        "new_accuracy": cand_meta["accuracy"],
        "candidate_macro_f1": cand_f1,
        "outcomes_incorporated": cand_meta["postop_outcomes_added"],
        "total_samples": cand_meta["total_samples"],
        "metadata": cand_meta,
        "model": cand_model,
    }


def get_evaluation_basis(metadata: Dict[str, object], verified_outcomes_count: int = 0) -> str:
    """Honest, surgeon-transparent disclosure of model validation basis."""
    n = int(verified_outcomes_count or metadata.get("postop_outcomes_added") or 0)
    if n > 0:
        return f"Synthetic OMFS benchmark + {n} surgeon-verified outcome(s) (prospective clinical validation pending)"
    return "Synthetic OMFS benchmark only (prospective clinical validation pending)"
