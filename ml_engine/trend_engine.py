"""
Temporal Perfusion Trend Engine for Free-Flap Monitoring.
Computes patient-relative baselines, multi-hour deterioration slopes
(Delta-T/hr, Delta-SpO2/hr, Delta-Doppler/hr), and trend-based escalation flags.
"""

from typing import Dict, List, Optional


TIER_ORDER = {"Low": 0, "Moderate": 1, "High": 2, "Critical": 3}
ORDER_TO_TIER = {v: k for k, v in TIER_ORDER.items()}


def _as_dict(item) -> Dict[str, float]:
    if isinstance(item, dict):
        return {
            "temp_delta_c": float(item.get("temp_delta_c", 0.0)),
            "spo2_pct": float(item.get("spo2_pct", 96.0)),
            "doppler_flow_cm_s": float(item.get("doppler_flow_cm_s", 20.0)),
            "visual_perfusion_score": float(item.get("visual_perfusion_score", 80.0)),
            "hours_post_op": float(item.get("hours_post_op", 0.0)),
        }
    return {
        "temp_delta_c": float(getattr(item, "temp_delta_c", 0.0)),
        "spo2_pct": float(getattr(item, "spo2_pct", 96.0)),
        "doppler_flow_cm_s": float(getattr(item, "doppler_flow_cm_s", 20.0)),
        "visual_perfusion_score": float(getattr(item, "visual_perfusion_score", 80.0)),
        "hours_post_op": float(getattr(item, "hours_post_op", 0.0)),
    }


def analyze_temporal_trend(
    current_features: Dict[str, float],
    history: Optional[List[object]] = None,
) -> Dict[str, object]:
    """
    Compare current reading against patient's earliest baseline and most recent
    historical readings (passed oldest -> newest, or arbitrary order sorted by hours_post_op).
    """
    curr = _as_dict(current_features)
    if not history:
        return {
            "trend_status": "Baseline Established",
            "trend_escalation": "None",
            "history_count": 0,
            "baseline": {
                "temp_delta_c": round(curr["temp_delta_c"], 2),
                "spo2_pct": round(curr["spo2_pct"], 1),
                "doppler_flow_cm_s": round(curr["doppler_flow_cm_s"], 2),
                "hours_post_op": round(curr["hours_post_op"], 1),
            },
            "baseline_deltas": {
                "temp_delta_change_c": 0.0,
                "spo2_change_pct": 0.0,
                "doppler_change_pct": 0.0,
            },
            "hourly_slopes": {
                "temp_delta_per_hr": 0.0,
                "spo2_per_hr": 0.0,
                "doppler_per_hr": 0.0,
            },
            "trend_notes": ["Initial patient baseline recorded for temporal trajectory tracking."],
        }

    series = [_as_dict(h) for h in history]
    baseline = series[0]
    prev = series[-1]

    # Relative changes from patient's own baseline
    temp_delta_change = round(curr["temp_delta_c"] - baseline["temp_delta_c"], 2)
    spo2_change = round(curr["spo2_pct"] - baseline["spo2_pct"], 2)
    base_dopp = max(baseline["doppler_flow_cm_s"], 1.0)
    doppler_change_pct = round(
        ((curr["doppler_flow_cm_s"] - baseline["doppler_flow_cm_s"]) / base_dopp) * 100.0,
        1,
    )

    # Elapsed postoperative hours since previous reading (clamped >= 0.25h to avoid div-by-zero spikes)
    dt_hr = curr["hours_post_op"] - prev["hours_post_op"]
    if dt_hr <= 0.05:
        dt_hr = 0.5  # fallback 30-min interval for rapid sequential checks
    dt_hr = max(dt_hr, 0.25)

    temp_slope = round((curr["temp_delta_c"] - prev["temp_delta_c"]) / dt_hr, 2)
    spo2_slope = round((curr["spo2_pct"] - prev["spo2_pct"]) / dt_hr, 2)
    doppler_slope = round((curr["doppler_flow_cm_s"] - prev["doppler_flow_cm_s"]) / dt_hr, 2)

    notes: List[str] = []
    escalation = "None"

    def _bump(level: str) -> None:
        nonlocal escalation
        if escalation == "None" or TIER_ORDER.get(level, 0) > TIER_ORDER.get(escalation, 0):
            escalation = level

    # 1. Rapid tissue desaturation (clinical NIRS / tissue oximetry trend threshold)
    if spo2_slope <= -10.0 or spo2_change <= -15.0:
        notes.append(
            f"Rapid tissue desaturation trend: SpO2 dropped {spo2_change:+.1f}% from baseline "
            f"({spo2_slope:+.1f}%/hr)."
        )
        _bump("Critical")
    elif spo2_slope <= -5.0 or spo2_change <= -8.0:
        notes.append(
            f"Declining oxygenation trend: SpO2 {spo2_change:+.1f}% vs patient baseline ({spo2_slope:+.1f}%/hr)."
        )
        _bump("High")

    # 2. Progressive flap cooling relative to patient's own baseline
    if temp_delta_change >= 1.8 or temp_slope >= 1.0:
        notes.append(
            f"Rapid flap cooling trend: Delta-T rose {temp_delta_change:+.2f} C above baseline "
            f"({temp_slope:+.2f} C/hr)."
        )
        _bump("Critical")
    elif temp_delta_change >= 1.0 or temp_slope >= 0.5:
        notes.append(
            f"Progressive flap cooling: Delta-T {temp_delta_change:+.2f} C vs baseline ({temp_slope:+.2f} C/hr)."
        )
        _bump("High")

    # 3. Doppler arterial/venous velocity loss relative to patient baseline
    if doppler_change_pct <= -55.0 or doppler_slope <= -8.0:
        notes.append(
            f"Sharp Doppler flow loss: {doppler_change_pct:+.1f}% from baseline ({doppler_slope:+.1f} cm/s/hr)."
        )
        _bump("Critical")
    elif doppler_change_pct <= -35.0 or doppler_slope <= -4.0:
        notes.append(
            f"Declining Doppler velocity: {doppler_change_pct:+.1f}% vs baseline ({doppler_slope:+.1f} cm/s/hr)."
        )
        _bump("High")

    # 4. Sustained multi-step worsening across last 2+ readings
    if len(series) >= 2:
        prev2 = series[-2]
        sustained_spo2_drop = prev["spo2_pct"] < prev2["spo2_pct"] and curr["spo2_pct"] < prev["spo2_pct"] - 1.5
        sustained_cooling = prev["temp_delta_c"] > prev2["temp_delta_c"] and curr["temp_delta_c"] > prev["temp_delta_c"] + 0.3
        if sustained_spo2_drop and sustained_cooling:
            notes.append("Sustained multi-reading deterioration across both SpO2 and flap temperature.")
            _bump("High")

    prev_spo2_step = round(curr["spo2_pct"] - prev["spo2_pct"], 2)
    prev_temp_step = round(curr["temp_delta_c"] - prev["temp_delta_c"], 2)
    critically_impaired = (
        curr["spo2_pct"] < 88.0
        or curr["temp_delta_c"] >= 2.0
        or curr["doppler_flow_cm_s"] < 10.0
    )
    recovering = (
        (spo2_change >= 5.0 and temp_delta_change <= -0.5)
        or (prev_spo2_step >= 6.0 and prev_temp_step <= -0.3 and curr["spo2_pct"] >= 90.0)
    )

    if escalation == "Critical":
        status = "Rapid Deterioration"
    elif escalation == "High":
        status = "Deteriorating"
    elif spo2_change <= -4.0 or temp_delta_change >= 0.6 or doppler_change_pct <= -20.0:
        status = "Early Watch"
        escalation = "Moderate"
        notes.append("Mild downward drift from patient baseline — shorten monitoring interval.")
    elif recovering:
        status = "Improving"
        notes.append(
            f"Perfusion improving (SpO2 {spo2_change:+.1f}% vs baseline, Delta-T {temp_delta_change:+.2f} C)."
        )
    elif critically_impaired:
        status = "Persistent Compromise"
        notes.append(
            "Flap perfusion remains critically impaired without recovery — urgent bedside re-evaluation required."
        )
    else:
        status = "Stable"
        notes.append(
            f"Perfusion trajectory stable vs baseline (SpO2 {spo2_change:+.1f}%, Delta-T {temp_delta_change:+.2f} C)."
        )

    return {
        "trend_status": status,
        "trend_escalation": escalation,
        "history_count": len(series),
        "baseline": {
            "temp_delta_c": round(baseline["temp_delta_c"], 2),
            "spo2_pct": round(baseline["spo2_pct"], 1),
            "doppler_flow_cm_s": round(baseline["doppler_flow_cm_s"], 2),
            "hours_post_op": round(baseline["hours_post_op"], 1),
        },
        "baseline_deltas": {
            "temp_delta_change_c": temp_delta_change,
            "spo2_change_pct": spo2_change,
            "doppler_change_pct": doppler_change_pct,
        },
        "hourly_slopes": {
            "temp_delta_per_hr": temp_slope,
            "spo2_per_hr": spo2_slope,
            "doppler_per_hr": doppler_slope,
        },
        "trend_notes": notes,
    }


def apply_trend_escalation(base_tier: str, trend_escalation: str) -> str:
    """Escalate risk tier if patient temporal trajectory indicates faster deterioration than static tier."""
    if not trend_escalation or trend_escalation == "None":
        return base_tier
    base_rank = TIER_ORDER.get(base_tier, 0)
    trend_rank = TIER_ORDER.get(trend_escalation, 0)
    return ORDER_TO_TIER[max(base_rank, trend_rank)]
