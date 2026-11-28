"""
Realistic Multisensor Free-Flap Live Stream Simulator.
Generates step-by-step IR temperature, SpO2, Doppler flow, and camera color
readings for Healthy, Venous Deterioration, and Arterial Deterioration scenarios.
"""

import random
from typing import Dict, List, Optional

from ml_engine.model_service import predict_flap_status


SCENARIOS = ("normal", "venous_deteriorating", "arterial_deteriorating")


def generate_simulated_reading(
    scenario: str = "normal",
    step: int = 0,
    seed: int = None,
    history: Optional[List[object]] = None,
    base_hours_post_op: Optional[float] = None,
) -> Dict[str, object]:
    """
    Generate one realistic sensor reading at time-step `step` (0..10)
    and run it through the local AI prediction engine.
    """
    if scenario not in SCENARIOS:
        raise ValueError(f"Unknown scenario '{scenario}'. Expected one of {SCENARIOS}.")

    rng = random.Random(seed) if seed is not None else random.Random()
    progress = min(max(step, 0), 8) / 8.0  # 0.0 (start) -> 1.0 (full compromise)
    control_temp = round(36.7 + rng.uniform(-0.12, 0.12), 2)

    if scenario == "normal":
        delta_t = 0.40 + rng.uniform(-0.15, 0.20)
        spo2 = 97.0 + rng.uniform(-1.5, 1.5)
        doppler = 21.5 + rng.uniform(-2.0, 2.0)
        redness = 0.15 + rng.uniform(-0.02, 0.02)
        cyanosis = 0.02 + rng.uniform(0.0, 0.03)
        pallor = 0.42 + rng.uniform(-0.04, 0.04)

    elif scenario == "venous_deteriorating":
        delta_t = 0.45 + progress * 1.75 + rng.uniform(-0.10, 0.15)
        spo2 = 96.5 - progress * 19.5 + rng.uniform(-1.2, 1.2)
        doppler = 20.5 - progress * 12.5 + rng.uniform(-1.0, 1.0)
        redness = 0.15 - progress * 0.05 + rng.uniform(-0.01, 0.01)
        cyanosis = 0.03 + progress * 0.56 + rng.uniform(-0.02, 0.03)
        pallor = 0.42 - progress * 0.14 + rng.uniform(-0.02, 0.02)

    else:  # arterial_deteriorating
        delta_t = 0.45 + progress * 3.10 + rng.uniform(-0.12, 0.15)
        spo2 = 97.0 - progress * 27.0 + rng.uniform(-1.2, 1.2)
        doppler = 21.0 - progress * 19.2 + rng.uniform(-0.8, 0.8)
        redness = 0.15 - progress * 0.13 + rng.uniform(-0.01, 0.01)
        cyanosis = 0.02 + rng.uniform(0.0, 0.03)
        pallor = 0.42 + progress * 0.40 + rng.uniform(-0.02, 0.02)

    ir_flap_temp = round(control_temp - max(0.0, delta_t), 2)
    spo2 = round(max(50.0, min(100.0, spo2)), 1)
    doppler = round(max(0.0, doppler), 2)
    redness = round(max(0.0, min(1.0, redness)), 4)
    cyanosis = round(max(0.0, min(1.0, cyanosis)), 4)
    pallor = round(max(0.0, min(1.0, pallor)), 4)

    if base_hours_post_op is not None:
        sim_hours = round(min(720.0, max(0.0, float(base_hours_post_op)) + max(0.2, step * 0.25)), 1)
    else:
        sim_hours = round(6.0 + step * 1.5, 1)

    prediction = predict_flap_status(
        ir_flap_temp_c=ir_flap_temp,
        control_skin_temp_c=control_temp,
        spo2_pct=spo2,
        doppler_flow_cm_s=doppler,
        visual_redness_index=redness,
        visual_cyanosis_index=cyanosis,
        visual_pallor_index=pallor,
        hours_post_op=sim_hours,
        history=history,
    )

    return {
        "scenario": scenario,
        "step": step,
        "prediction": prediction,
    }


if __name__ == "__main__":
    print("=== Live Sensor Simulator Test (Arterial Deterioration) ===")
    for step in range(0, 9, 2):
        out = generate_simulated_reading("arterial_deteriorating", step=step, seed=100 + step)
        pred = out["prediction"]
        feat = pred["features"]
        print(
            f"Step {step} | Delta-T: {feat['temp_delta_c']:4.2f} C | "
            f"SpO2: {feat['spo2_pct']:5.1f}% | Doppler: {feat['doppler_flow_cm_s']:5.2f} cm/s -> "
            f"{pred['predicted_class']:19s} | Risk: {pred['risk_tier']:8s} | Alert: {pred['alert_required']}"
        )