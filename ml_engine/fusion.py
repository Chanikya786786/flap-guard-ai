"""
Shared multisensor fusion calculations for IR temperature, SpO2,
Doppler blood flow, and Camera visual color perfusion indices.
"""

from typing import Dict


FEATURE_COLUMNS = [
    "ir_flap_temp_c",
    "control_skin_temp_c",
    "temp_delta_c",
    "spo2_pct",
    "doppler_flow_cm_s",
    "visual_redness_index",
    "visual_cyanosis_index",
    "visual_pallor_index",
    "visual_perfusion_score",
    "perfusion_fusion_index",
    "hours_post_op",
]


def clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, float(value)))


def compute_visual_perfusion_score(
    redness_index: float,
    cyanosis_index: float,
    pallor_index: float,
) -> float:
    """Compute a 0-100 visual tissue perfusion score from camera color indices."""
    red_component = clamp(redness_index / 0.18, 0.0, 1.0)
    cyanosis_penalty = clamp(cyanosis_index / 0.60, 0.0, 1.0)
    pallor_penalty = clamp((pallor_index - 0.52) / 0.35, 0.0, 1.0)
    dominant_penalty = max(cyanosis_penalty, pallor_penalty)

    score = 100.0 * (0.45 * red_component + 0.55 * (1.0 - dominant_penalty))
    return round(clamp(score, 0.0, 100.0), 2)


def classify_visual_triage(
    redness_index: float,
    cyanosis_index: float,
    pallor_index: float,
    visual_perfusion_score: float,
) -> str:
    """Quick camera-only triage label (adjunct screening before full fusion)."""
    if cyanosis_index >= 0.22:
        return "Dusky / Suspected Venous Compromise"
    if pallor_index >= 0.65 and redness_index < 0.06:
        return "Pale / Suspected Arterial Compromise"
    if visual_perfusion_score >= 65.0:
        return "Healthy Pink Perfusion"
    return "Borderline / Monitor Closely"


def compute_fusion_features(
    ir_flap_temp_c: float,
    control_skin_temp_c: float,
    spo2_pct: float,
    doppler_flow_cm_s: float,
    visual_redness_index: float = 0.14,
    visual_cyanosis_index: float = 0.03,
    visual_pallor_index: float = 0.42,
    hours_post_op: float = 12.0,
) -> Dict[str, float]:
    """
    Fuse IR temperature, SpO2, Doppler flow, and camera color indices
    into one standardized feature dictionary for the AI model.
    """
    ir_flap_temp_c = float(ir_flap_temp_c)
    control_skin_temp_c = float(control_skin_temp_c)
    spo2_pct = float(spo2_pct)
    doppler_flow_cm_s = float(doppler_flow_cm_s)
    visual_redness_index = float(visual_redness_index)
    visual_cyanosis_index = float(visual_cyanosis_index)
    visual_pallor_index = float(visual_pallor_index)
    hours_post_op = float(hours_post_op)

    # Temperature drop relative to adjacent healthy skin (positive = cooler flap)
    temp_delta_c = round(control_skin_temp_c - ir_flap_temp_c, 3)

    visual_score = compute_visual_perfusion_score(
        visual_redness_index,
        visual_cyanosis_index,
        visual_pallor_index,
    )

    # Normalize each modality to [0, 1] where 1.0 = optimal perfusion
    temp_norm = 1.0 - clamp(max(0.0, temp_delta_c) / 4.0, 0.0, 1.0)
    spo2_norm = clamp((spo2_pct - 55.0) / 45.0, 0.0, 1.0)
    doppler_norm = clamp(doppler_flow_cm_s / 24.0, 0.0, 1.0)
    visual_norm = clamp(visual_score / 100.0, 0.0, 1.0)

    # Weighted multisensor fusion index (0-100)
    perfusion_fusion_index = round(
        100.0 * (
            0.30 * temp_norm
            + 0.30 * spo2_norm
            + 0.25 * doppler_norm
            + 0.15 * visual_norm
        ),
        2,
    )

    return {
        "ir_flap_temp_c": round(ir_flap_temp_c, 2),
        "control_skin_temp_c": round(control_skin_temp_c, 2),
        "temp_delta_c": temp_delta_c,
        "spo2_pct": round(spo2_pct, 2),
        "doppler_flow_cm_s": round(doppler_flow_cm_s, 2),
        "visual_redness_index": round(visual_redness_index, 4),
        "visual_cyanosis_index": round(visual_cyanosis_index, 4),
        "visual_pallor_index": round(visual_pallor_index, 4),
        "visual_perfusion_score": visual_score,
        "perfusion_fusion_index": perfusion_fusion_index,
        "hours_post_op": round(hours_post_op, 1),
    }