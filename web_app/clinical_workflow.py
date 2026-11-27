"""
Clinical Workflow Utilities for FlapGuard AI (v3.2):
- Post-op surveillance protocol schedule (Q1h / Q2h / Q4h) & overdue detection
- Bedside physical exam clinical finding interpreter (Capillary Refill, Turgor, Pinprick, Doppler)
- Priority-sorted SBAR (Situation, Background, Assessment, Recommendation) shift handoff generator
"""

from datetime import timezone
from typing import Dict, List, Optional

from web_app.models import utc_now


def _norm_utc(dt):
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _fmt_duration(minutes: int) -> str:
    mins = max(0, int(minutes))
    if mins >= 60:
        h, m = divmod(mins, 60)
        return f"{h}h {m}m" if m else f"{h}h"
    return f"{mins}m"


def compute_check_schedule(patient, latest_reading=None) -> Dict[str, object]:
    """
    Standard microsurgical free-flap bedside surveillance schedule:
    - 0 to 24h post-op: Q1h (every 60 minutes)
    - 24 to 72h post-op: Q2h (every 120 minutes)
    - > 72h post-op: Q4h (every 240 minutes)
    Advisory protocol helper; institutional nursing orders take precedence.
    """
    hours = float(getattr(patient, "hours_post_op", 0.0) or 0.0)
    if hours <= 24.0:
        protocol, interval_min = "Q1h", 60
    elif hours <= 72.0:
        protocol, interval_min = "Q2h", 120
    else:
        protocol, interval_min = "Q4h", 240

    if latest_reading is None or getattr(latest_reading, "created_at", None) is None:
        return {
            "protocol": protocol,
            "interval_minutes": interval_min,
            "overdue": True,
            "minutes_until_due": 0,
            "status_label": f"{protocol} · Initial Check Due",
            "badge_tone": "warning",
        }

    created = _norm_utc(latest_reading.created_at)
    elapsed_min = max(0, int((utc_now() - created).total_seconds() // 60))
    remaining_min = interval_min - elapsed_min

    if remaining_min < 0:
        overdue_by = abs(remaining_min)
        return {
            "protocol": protocol,
            "interval_minutes": interval_min,
            "overdue": True,
            "minutes_until_due": remaining_min,
            "status_label": f"{protocol} · Overdue {_fmt_duration(overdue_by)}",
            "badge_tone": "danger",
        }

    return {
        "protocol": protocol,
        "interval_minutes": interval_min,
        "overdue": False,
        "minutes_until_due": remaining_min,
        "status_label": f"{protocol} · Next in {_fmt_duration(remaining_min)}",
        "badge_tone": "warning" if remaining_min <= 15 else "ok",
    }


def extract_bedside_exam(source: Dict[str, object]) -> Dict[str, Optional[str]]:
    """Safely extract optional bedside physical exam fields from form or JSON."""
    def _clean(key: str) -> Optional[str]:
        val = source.get(key)
        if val is None:
            return None
        s = str(val).strip()
        return s[:40] if s and s.lower() not in ("none", "not recorded", "not performed", "") else None

    return {
        "capillary_refill": _clean("capillary_refill"),
        "flap_turgor": _clean("flap_turgor"),
        "pinprick_bleeding": _clean("pinprick_bleeding"),
        "doppler_quality": _clean("doppler_quality"),
    }


def format_bedside_exam_summary(exam: Dict[str, Optional[str]]) -> str:
    parts: List[str] = []
    if exam.get("capillary_refill"):
        parts.append(f"Cap Refill: {exam['capillary_refill']}")
    if exam.get("flap_turgor"):
        parts.append(f"Turgor: {exam['flap_turgor']}")
    if exam.get("pinprick_bleeding"):
        parts.append(f"Pinprick: {exam['pinprick_bleeding']}")
    if exam.get("doppler_quality"):
        parts.append(f"Doppler: {exam['doppler_quality']}")
    return " · ".join(parts)


def build_bedside_exam_notes(exam: Optional[Dict[str, Optional[str]]]) -> List[str]:
    """Translate recorded bedside physical exam findings into concise clinical notes."""
    if not exam:
        return []
    notes: List[str] = []
    cap = (exam.get("capillary_refill") or "").lower()
    turgor = (exam.get("flap_turgor") or "").lower()
    pin = (exam.get("pinprick_bleeding") or "").lower()
    dopp = (exam.get("doppler_quality") or "").lower()

    if "brisk" in cap or "<1" in cap:
        notes.append("Bedside exam: Brisk capillary refill (<1s) — classic sign of venous congestion.")
    elif "sluggish" in cap or ">3" in cap or "absent" in cap:
        notes.append("Bedside exam: Sluggish or absent capillary refill — raises concern for arterial inflow loss.")

    if "tense" in turgor or "congested" in turgor:
        notes.append("Bedside exam: Tense/congested flap turgor consistent with venous outflow obstruction.")
    elif "flaccid" in turgor or "pale" in turgor:
        notes.append("Bedside exam: Pale/flaccid flap turgor consistent with arterial insufficiency.")

    if "dark" in pin or "rapid" in pin:
        notes.append("Bedside exam: Dark rapid pinprick bleeding strongly supports venous congestion.")
    elif "none" in pin or "minimal" in pin:
        notes.append("Bedside exam: Minimal/absent pinprick bleeding strongly supports arterial occlusion.")

    if "absent" in dopp:
        notes.append("Bedside exam: Absent bedside Doppler signal — urgent surgical re-evaluation required.")
    elif "venous" in dopp or "monophasic" in dopp or "weak" in dopp:
        notes.append(f"Bedside exam: Abnormal Doppler signal ({exam.get('doppler_quality')}) — monitor closely.")

    return notes


def generate_ward_sbar(patients_with_latest: List[Dict[str, object]], active_alerts_count: int, generated_by: str) -> Dict[str, object]:
    """Generate a structured & plain-text SBAR shift handoff report for the active ward."""
    tier_priority = {"Critical": 0, "High": 1, "Moderate": 2, "Low": 3, "Unassessed": 4}
    now_str = utc_now().strftime("%d %b %Y, %H:%M UTC")

    items = []
    overdue_count = 0
    critical_high_count = 0

    for entry in patients_with_latest:
        p = entry["patient"]
        r = entry["latest"]
        tier = entry["tier"]
        sched = compute_check_schedule(p, r)
        if sched["overdue"]:
            overdue_count += 1
        if tier in ("Critical", "High"):
            critical_high_count += 1

        exam_dict = {
            "capillary_refill": getattr(r, "capillary_refill", None) if r else None,
            "flap_turgor": getattr(r, "flap_turgor", None) if r else None,
            "pinprick_bleeding": getattr(r, "pinprick_bleeding", None) if r else None,
            "doppler_quality": getattr(r, "doppler_quality", None) if r else None,
        }
        exam_summary = format_bedside_exam_summary(exam_dict)

        if r:
            situation = f"{p.full_name} ({p.mrn}) — {tier} Risk ({r.predicted_class}, {r.risk_score_pct:.1f}%). Schedule: {sched['status_label']}."
            assessment = (
                f"ΔT {r.temp_delta_c:.1f}°C | SpO2 {r.spo2_pct:.1f}% | Doppler {r.doppler_flow_cm_s:.1f} cm/s | "
                f"Visual {r.visual_perfusion_score:.0f}/100 | Trend: {r.trend_status} | Reliability: {r.confidence_label}."
            )
            if exam_summary:
                assessment += f" Bedside Exam: {exam_summary}."
            if r.ood_flag:
                assessment += " [OOD sensor warning — re-verify probes]"
        else:
            situation = f"{p.full_name} ({p.mrn}) — Awaiting initial flap check ({sched['status_label']})."
            assessment = "No sensor readings recorded yet."

        background = f"{p.age}y, {p.flap_type} ({p.surgery_site}), {p.hours_post_op:.1f}h post-op."

        if tier == "Critical":
            recommendation = "IMMEDIATE surgical bedside inspection; verify pedicle/anastomosis and prepare for possible OR take-back."
        elif tier == "High":
            recommendation = "Urgent bedside flap evaluation within 15 minutes; repeat full multisensor + physical exam."
        elif sched["overdue"]:
            recommendation = f"Check overdue ({sched['status_label']}) — perform bedside flap assessment now."
        elif tier == "Moderate":
            recommendation = "Shorten monitoring interval to Q30m–Q1h and reassess perfusion trajectory."
        else:
            recommendation = f"Continue standard {sched['protocol']} free-flap surveillance protocol."

        items.append(
            {
                "patient_id": p.id,
                "patient_name": p.full_name,
                "mrn": p.mrn,
                "tier": tier,
                "priority": (tier_priority.get(tier, 4), 0 if sched["overdue"] else 1, -float(p.hours_post_op or 0.0)),
                "schedule": sched,
                "situation": situation,
                "background": background,
                "assessment": assessment,
                "recommendation": recommendation,
            }
        )

    items.sort(key=lambda x: x["priority"])
    for it in items:
        it.pop("priority", None)

    lines = [
        "FLAPGUARD AI — SHIFT HANDOFF REPORT (SBAR)",
        f"Generated: {now_str} | By: {generated_by}",
        f"Ward Summary: {len(items)} active patients | {critical_high_count} Critical/High | {overdue_count} Overdue Checks | {active_alerts_count} Pending Alerts",
        "=" * 54,
    ]
    if not items:
        lines.append("No active postoperative flap patients currently enrolled.")
    else:
        for i, it in enumerate(items, 1):
            lines.extend(
                [
                    f"{i}. [{it['tier'].upper()}] {it['patient_name']} ({it['mrn']})",
                    f"   S: {it['situation']}",
                    f"   B: {it['background']}",
                    f"   A: {it['assessment']}",
                    f"   R: {it['recommendation']}",
                    "-" * 54,
                ]
            )
    lines.append("Note: Research & engineering prototype — confirm all findings clinically at bedside.")

    return {
        "generated_at_utc": now_str,
        "generated_by": generated_by,
        "total_patients": len(items),
        "critical_high_count": critical_high_count,
        "overdue_count": overdue_count,
        "active_alerts_count": active_alerts_count,
        "patients": items,
        "sbar_text": "\n".join(lines),
    }
