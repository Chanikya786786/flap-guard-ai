"""
Generates a clinically grounded multisensor free-flap dataset combining:
- IR Flap Temperature & Adjacent Control Skin Temperature (Delta-T)
- Tissue SpO2 (%)
- Doppler Blood Flow Velocity (cm/s)
- Camera Visual Color Indices (Redness, Cyanosis, Pallor)
"""

import os
import numpy as np
import pandas as pd

from ml_engine.fusion import compute_fusion_features


LABEL_MAP = {
    0: "Normal",
    1: "Venous Compromise",
    2: "Arterial Compromise",
}


def generate_flap_dataset(
    samples_per_class: int = 500,
    seed: int = 42,
    output_csv: str = os.path.join("ml_engine", "data", "flap_multisensor_dataset.csv"),
) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    rows = []

    for label, label_name in LABEL_MAP.items():
        for _ in range(samples_per_class):
            control_temp = float(np.clip(rng.normal(36.7, 0.25), 36.0, 37.4))
            hours_post_op = float(np.clip(rng.uniform(1.0, 72.0), 1.0, 72.0))

            if label == 0:  # Normal Healthy Flap
                delta_t = float(np.clip(rng.normal(0.45, 0.32), 0.0, 1.5))
                spo2 = float(np.clip(rng.normal(96.8, 1.9), 90.0, 100.0))
                doppler = float(np.clip(rng.normal(21.0, 3.6), 11.5, 32.0))
                redness = float(np.clip(rng.normal(0.15, 0.032), 0.07, 0.28))
                cyanosis = float(np.clip(rng.normal(0.03, 0.03), 0.0, 0.16))
                pallor = float(np.clip(rng.normal(0.42, 0.08), 0.20, 0.60))

            elif label == 1:  # Venous Compromise (Congestion)
                delta_t = float(np.clip(rng.normal(1.65, 0.60), 0.4, 3.4))
                spo2 = float(np.clip(rng.normal(82.0, 5.8), 64.0, 94.0))
                doppler = float(np.clip(rng.normal(10.5, 3.4), 3.0, 19.5))
                redness = float(np.clip(rng.normal(0.10, 0.035), 0.03, 0.20))
                cyanosis = float(np.clip(rng.normal(0.50, 0.15), 0.15, 0.90))
                pallor = float(np.clip(rng.normal(0.28, 0.09), 0.08, 0.54))

            else:  # Arterial Compromise (Ischemia)
                delta_t = float(np.clip(rng.normal(2.95, 0.72), 1.3, 5.0))
                spo2 = float(np.clip(rng.normal(72.0, 6.8), 50.0, 89.0))
                doppler = float(np.clip(rng.normal(3.2, 2.3), 0.0, 10.0))
                redness = float(np.clip(rng.normal(0.025, 0.02), 0.0, 0.10))
                cyanosis = float(np.clip(rng.normal(0.04, 0.04), 0.0, 0.19))
                pallor = float(np.clip(rng.normal(0.77, 0.09), 0.52, 0.96))

            ir_flap_temp = control_temp - delta_t

            features = compute_fusion_features(
                ir_flap_temp_c=ir_flap_temp,
                control_skin_temp_c=control_temp,
                spo2_pct=spo2,
                doppler_flow_cm_s=doppler,
                visual_redness_index=redness,
                visual_cyanosis_index=cyanosis,
                visual_pallor_index=pallor,
                hours_post_op=hours_post_op,
            )
            features["label"] = label
            features["label_name"] = label_name
            rows.append(features)

    df = pd.DataFrame(rows).sample(frac=1.0, random_state=seed).reset_index(drop=True)
    os.makedirs(os.path.dirname(output_csv), exist_ok=True)
    df.to_csv(output_csv, index=False)
    return df


if __name__ == "__main__":
    dataset = generate_flap_dataset()
    print("=== Multisensor Dataset Generated Successfully ===")
    print(f"Total rows: {len(dataset)}")
    print("\nClass distribution:")
    print(dataset["label_name"].value_counts().to_string())
    print("\nAverage feature values per class:")
    summary_cols = [
        "temp_delta_c",
        "spo2_pct",
        "doppler_flow_cm_s",
        "visual_perfusion_score",
        "perfusion_fusion_index",
    ]
    print(dataset.groupby("label_name")[summary_cols].mean().round(2).to_string())