"""
Local AI Model Training, Risk Stratification, Prediction, and
Continuous Learning Pipeline (Zero External APIs).
"""

import json
import os
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

import joblib
import numpy as np
import pandas as pd
from sklearn.ensemble import RandomForestClassifier
from sklearn.metrics import (
    accuracy_score,
    confusion_matrix,
    precision_recall_fscore_support,
)
from sklearn.model_selection import train_test_split

from ml_engine.fusion import FEATURE_COLUMNS, compute_fusion_features
from ml_engine.generate_dataset import generate_flap_dataset


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

_MODEL_CACHE: Dict[str, object] = {"model": None, "metadata": None}


def _next_version(current_version: Optional[str]) -> str:
    if not current_version:
        return "v1.0"
    try:
        major, minor = current_version.lstrip("v").split(".")
        return f"v{int(major)}.{int(minor) + 1}"
    except ValueError:
        return "v1.1"


def train_and_save_model(
    extra_records: Optional[List[Dict[str, object]]] = None,
    version: Optional[str] = None,
    model_path: str = MODEL_PATH,
    metadata_path: str = METADATA_PATH,
) -> Tuple[RandomForestClassifier, Dict[str, object]]:
    """
    Train the local Random Forest classifier on the base multisensor dataset
    plus any surgeon-verified postoperative outcome records (continuous learning).
    """
    if not os.path.exists(BASE_DATASET_PATH):
        generate_flap_dataset(output_csv=BASE_DATASET_PATH)

    df = pd.read_csv(BASE_DATASET_PATH)
    base_count = len(df)
    outcomes_count = 0

    if extra_records:
        cleaned_rows = []
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

        if cleaned_rows:
            outcomes_count = len(cleaned_rows)
            extra_df = pd.DataFrame(cleaned_rows)
            df = pd.concat([df, extra_df], ignore_index=True)

    X = df[FEATURE_COLUMNS]
    y = df["label"].astype(int)

    X_train, X_test, y_train, y_test = train_test_split(
        X, y, test_size=0.20, random_state=42, stratify=y
    )

    model = RandomForestClassifier(
        n_estimators=120,
        max_depth=12,
        min_samples_leaf=2,
        random_state=42,
        n_jobs=-1,
    )
    model.fit(X_train, y_train)

    y_pred = model.predict(X_test)
    acc = float(accuracy_score(y_test, y_pred))
    precision, recall, f1, _ = precision_recall_fscore_support(
        y_test, y_pred, average="macro", zero_division=0
    )
    cm = confusion_matrix(y_test, y_pred, labels=[0, 1, 2]).tolist()

    importances = {
        col: round(float(imp), 4)
        for col, imp in sorted(
            zip(FEATURE_COLUMNS, model.feature_importances_),
            key=lambda item: item[1],
            reverse=True,
        )
    }

    metadata = {
        "version": version or "v1.0",
        "trained_at_utc": datetime.now(timezone.utc).isoformat(),
        "algorithm": "RandomForestClassifier (Multisensor Fusion)",
        "base_samples": base_count,
        "postop_outcomes_added": outcomes_count,
        "total_samples": len(df),
        "test_samples": len(y_test),
        "accuracy": round(acc, 4),
        "macro_precision": round(float(precision), 4),
        "macro_recall": round(float(recall), 4),
        "macro_f1": round(float(f1), 4),
        "confusion_matrix": cm,
        "classes": [LABEL_TO_NAME[0], LABEL_TO_NAME[1], LABEL_TO_NAME[2]],
        "feature_importances": importances,
    }

    os.makedirs(os.path.dirname(model_path), exist_ok=True)
    joblib.dump(model, model_path, compress=3)
    with open(metadata_path, "w", encoding="utf-8") as f:
        json.dump(metadata, f, indent=2)

    if model_path == MODEL_PATH:
        _MODEL_CACHE["model"] = model
        _MODEL_CACHE["metadata"] = metadata

    return model, metadata


def load_model_and_metadata(force_reload: bool = False) -> Tuple[RandomForestClassifier, Dict[str, object]]:
    """Load the saved local model into memory, training v1.0 automatically if missing."""
    if not force_reload and _MODEL_CACHE["model"] is not None and _MODEL_CACHE["metadata"] is not None:
        return _MODEL_CACHE["model"], _MODEL_CACHE["metadata"]

    if not os.path.exists(MODEL_PATH) or not os.path.exists(METADATA_PATH):
        return train_and_save_model(version="v1.0")

    model = joblib.load(MODEL_PATH)
    with open(METADATA_PATH, "r", encoding="utf-8") as f:
        metadata = json.load(f)

    _MODEL_CACHE["model"] = model
    _MODEL_CACHE["metadata"] = metadata
    return model, metadata


def determine_risk_tier(
    predicted_class: str,
    compromise_prob: float,
    temp_delta_c: float,
    spo2_pct: float,
    doppler_flow_cm_s: float,
) -> str:
    """Map AI probability + clinical safety guardrails to 4-level Risk Stratification."""
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


def build_clinical_notes(features: Dict[str, float], predicted_class: str) -> List[str]:
    """Generate concise, surgeon-readable clinical findings behind the prediction."""
    notes = []
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

    if not notes:
        notes.append(f"All multisensor modalities within healthy postoperative limits ({predicted_class}).")
    return notes


def predict_flap_status(
    ir_flap_temp_c: float,
    control_skin_temp_c: float,
    spo2_pct: float,
    doppler_flow_cm_s: float,
    visual_redness_index: float = 0.14,
    visual_cyanosis_index: float = 0.03,
    visual_pallor_index: float = 0.42,
    hours_post_op: float = 12.0,
) -> Dict[str, object]:
    """Run multisensor fusion + local AI inference + risk stratification."""
    model, metadata = load_model_and_metadata()

    features = compute_fusion_features(
        ir_flap_temp_c=ir_flap_temp_c,
        control_skin_temp_c=control_skin_temp_c,
        spo2_pct=spo2_pct,
        doppler_flow_cm_s=doppler_flow_cm_s,
        visual_redness_index=visual_redness_index,
        visual_cyanosis_index=visual_cyanosis_index,
        visual_pallor_index=visual_pallor_index,
        hours_post_op=hours_post_op,
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

    risk_tier = determine_risk_tier(
        predicted_class=predicted_class,
        compromise_prob=compromise_prob,
        temp_delta_c=features["temp_delta_c"],
        spo2_pct=features["spo2_pct"],
        doppler_flow_cm_s=features["doppler_flow_cm_s"],
    )

    alert_required = risk_tier in ("High", "Critical")
    alert_message = (
        f"URGENT [{risk_tier}]: {predicted_class} predicted "
        f"({compromise_prob * 100:.1f}% risk). Immediate flap inspection advised."
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
        "alert_required": alert_required,
        "alert_message": alert_message,
        "clinical_notes": build_clinical_notes(features, predicted_class),
        "features": features,
        "model_version": metadata["version"],
    }


def retrain_from_postop_outcomes(outcome_records: List[Dict[str, object]]) -> Dict[str, object]:
    """Continuous learning: retrain the local model with verified postoperative outcomes."""
    _, old_meta = load_model_and_metadata()
    new_version = _next_version(old_meta.get("version", "v1.0"))
    _, new_meta = train_and_save_model(extra_records=outcome_records, version=new_version)

    return {
        "old_version": old_meta["version"],
        "new_version": new_meta["version"],
        "old_accuracy": old_meta["accuracy"],
        "new_accuracy": new_meta["accuracy"],
        "outcomes_incorporated": new_meta["postop_outcomes_added"],
        "total_samples": new_meta["total_samples"],
        "metadata": new_meta,
    }


if __name__ == "__main__":
    print("=== Phase 2: Training Baseline Local AI Model (v1.0) ===")
    _, meta = train_and_save_model(version="v1.0")
    print(f"Model Version   : {meta['version']}")
    print(f"Total Samples   : {meta['total_samples']}")
    print(f"Test Accuracy   : {meta['accuracy'] * 100:.2f}%")
    print(f"Macro F1-Score  : {meta['macro_f1'] * 100:.2f}%")
    print(f"Confusion Matrix: {meta['confusion_matrix']}")
    print(f"Top 5 Features  : {list(meta['feature_importances'].items())[:5]}")

    print("\n=== Sample Inference Verification ===")
    test_cases = [
        ("Healthy Flap", dict(ir_flap_temp_c=36.3, control_skin_temp_c=36.7, spo2_pct=97.5, doppler_flow_cm_s=22.0, visual_redness_index=0.16, visual_cyanosis_index=0.02, visual_pallor_index=0.41)),
        ("Venous Congestion", dict(ir_flap_temp_c=34.9, control_skin_temp_c=36.7, spo2_pct=80.0, doppler_flow_cm_s=9.5, visual_redness_index=0.10, visual_cyanosis_index=0.55, visual_pallor_index=0.26)),
        ("Arterial Ischemia", dict(ir_flap_temp_c=33.5, control_skin_temp_c=36.7, spo2_pct=71.0, doppler_flow_cm_s=2.4, visual_redness_index=0.02, visual_cyanosis_index=0.03, visual_pallor_index=0.81)),
    ]
    for title, params in test_cases:
        res = predict_flap_status(**params)
        print(
            f"  [{title}] -> Class: {res['predicted_class']} | "
            f"Risk Tier: {res['risk_tier']} ({res['risk_score_pct']}%) | "
            f"Alert: {res['alert_required']}"
        )