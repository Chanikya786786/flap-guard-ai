"""
Local OpenCV Camera / Image Flap-Color Analyzer (Zero External APIs).
Performs CIE-LAB / RGB / HSV colorimetric perfusion extraction inside the
centered 50% flap ROI, excluding specular flash glare and deep shadows,
with automated sharpness, exposure, and glare quality diagnostics.
"""

import os
from typing import Dict, List

import cv2
import numpy as np

from ml_engine.fusion import (
    classify_visual_triage,
    compute_visual_perfusion_score,
)


def _analyze_bgr_array(bgr_image: np.ndarray) -> Dict[str, object]:
    if bgr_image is None or bgr_image.size == 0:
        raise ValueError("Invalid or unreadable image data.")

    height, width = bgr_image.shape[:2]
    if height < 20 or width < 20:
        raise ValueError("Image resolution too small (minimum 20x20 pixels).")

    # Centered 50% Region of Interest (ROI) matching the on-screen flap guide box
    y1, y2 = int(height * 0.25), int(height * 0.75)
    x1, x2 = int(width * 0.25), int(width * 0.75)
    roi_bgr = bgr_image[y1:y2, x1:x2]

    roi_rgb = cv2.cvtColor(roi_bgr, cv2.COLOR_BGR2RGB).astype(np.float32)
    roi_hsv = cv2.cvtColor(roi_bgr, cv2.COLOR_BGR2HSV).astype(np.float32)
    roi_lab = cv2.cvtColor(roi_bgr, cv2.COLOR_BGR2LAB).astype(np.float32)
    roi_gray = cv2.cvtColor(roi_bgr, cv2.COLOR_BGR2GRAY)

    # Quality diagnostics: sharpness (Laplacian variance), brightness, and flash glare
    sharpness_score = float(cv2.Laplacian(roi_gray, cv2.CV_64F).var())
    brightness_score = float(np.mean(roi_lab[:, :, 0]))  # 0 (black) to 255 (white)

    glare_mask = (roi_hsv[:, :, 2] > 245.0) & (roi_hsv[:, :, 1] < 25.0)
    shadow_mask = roi_hsv[:, :, 2] < 25.0
    glare_pct = float(np.mean(glare_mask) * 100.0)
    shadow_pct = float(np.mean(shadow_mask) * 100.0)

    # Filter out glare and deep shadows so flash reflections do not skew color readings
    valid_mask = ~(glare_mask | shadow_mask)
    if float(np.mean(valid_mask)) >= 0.20:
        px_rgb = roi_rgb[valid_mask]
        px_hsv = roi_hsv[valid_mask]
        px_lab = roi_lab[valid_mask]
    else:
        px_rgb = roi_rgb.reshape(-1, 3)
        px_hsv = roi_hsv.reshape(-1, 3)
        px_lab = roi_lab.reshape(-1, 3)

    mean_r = float(np.mean(px_rgb[:, 0]))
    mean_g = float(np.mean(px_rgb[:, 1]))
    mean_b = float(np.mean(px_rgb[:, 2]))
    total_rgb = mean_r + mean_g + mean_b + 1e-5

    mean_s_norm = float(np.mean(px_hsv[:, 1])) / 255.0
    mean_l_norm = float(np.mean(px_lab[:, 0])) / 255.0
    mean_a_lab = float(np.mean(px_lab[:, 1]))  # >128 shifts toward red/pink
    mean_b_lab = float(np.mean(px_lab[:, 2]))  # <128 shifts toward blue/cyanotic

    redness_index = float(np.clip((mean_r - mean_g) / total_rgb, 0.0, 1.0))

    blue_shift_rgb = max(0.0, (mean_b - mean_g) / total_rgb)
    blue_shift_lab = max(0.0, (128.0 - mean_b_lab) / 60.0)
    cyanosis_index = float(
        np.clip(blue_shift_rgb * 2.5 + blue_shift_lab * 0.5, 0.0, 1.0)
    )

    pallor_index = float(np.clip(mean_l_norm * (1.0 - mean_s_norm), 0.0, 1.0))

    visual_score = compute_visual_perfusion_score(
        redness_index, cyanosis_index, pallor_index
    )
    triage_label = classify_visual_triage(
        redness_index, cyanosis_index, pallor_index, visual_score
    )

    quality_warnings: List[str] = []
    if brightness_score < 55.0:
        quality_warnings.append("Image is too dark — improve ambient lighting on the flap.")
    elif brightness_score > 235.0:
        quality_warnings.append("Image is overexposed — reduce direct light or turn off flash.")
    if glare_pct > 12.0:
        quality_warnings.append(
            f"Specular flash glare detected ({glare_pct:.1f}% of ROI) — angle camera slightly."
        )
    if shadow_pct > 25.0:
        quality_warnings.append("Heavy shadows in center ROI — illuminate flap evenly.")
    if sharpness_score < 20.0:
        quality_warnings.append("Low edge detail / possible motion blur — hold camera steady.")

    if len(quality_warnings) == 0:
        quality_status = "Good"
    elif brightness_score < 40.0 or brightness_score > 245.0 or glare_pct > 30.0:
        quality_status = "Poor"
    else:
        quality_status = "Caution"

    return {
        "visual_redness_index": round(redness_index, 4),
        "visual_cyanosis_index": round(cyanosis_index, 4),
        "visual_pallor_index": round(pallor_index, 4),
        "visual_perfusion_score": visual_score,
        "visual_triage_label": triage_label,
        "mean_rgb": [round(mean_r, 1), round(mean_g, 1), round(mean_b, 1)],
        "lab_a_redness": round(mean_a_lab - 128.0, 2),
        "lab_b_cyanosis": round(mean_b_lab - 128.0, 2),
        "sharpness_score": round(sharpness_score, 1),
        "brightness_score": round(brightness_score, 1),
        "glare_pct": round(glare_pct, 1),
        "quality_status": quality_status,
        "quality_ok": quality_status != "Poor",
        "quality_warnings": quality_warnings,
        "roi_box": [x1, y1, x2, y2],
        "image_dimensions": [int(width), int(height)],
        "method_note": (
            "CIE-LAB/RGB colorimetric perfusion estimator on centered flap ROI "
            "(fused with thermal, SpO2, and Doppler sensors; not a standalone CNN)."
        ),
    }


def analyze_flap_image_bytes(image_bytes: bytes) -> Dict[str, object]:
    """Analyze flap color directly from raw uploaded/camera image bytes."""
    if not image_bytes:
        raise ValueError("Empty image payload received.")

    np_buffer = np.frombuffer(image_bytes, dtype=np.uint8)
    bgr_image = cv2.imdecode(np_buffer, cv2.IMREAD_COLOR)
    return _analyze_bgr_array(bgr_image)


def analyze_flap_image_file(file_path: str) -> Dict[str, object]:
    """Analyze flap color from a local image file path."""
    if not os.path.exists(file_path):
        raise FileNotFoundError(f"Image file not found: {file_path}")

    bgr_image = cv2.imread(file_path, cv2.IMREAD_COLOR)
    return _analyze_bgr_array(bgr_image)


def create_demo_flap_images(output_dir: str) -> Dict[str, str]:
    """Generate 3 realistic synthetic test flap images for verification & demos."""
    os.makedirs(output_dir, exist_ok=True)
    rng = np.random.default_rng(42)

    # BGR colors matching clinical appearance (Healthy Pink, Venous Dusky, Arterial Pale)
    specs = {
        "healthy_pink.png": (150, 145, 225),
        "venous_dusky.png": (128, 78, 115),
        "arterial_pale.png": (208, 212, 218),
    }

    paths = {}
    for filename, bgr in specs.items():
        canvas = np.zeros((240, 320, 3), dtype=np.uint8)
        canvas[:, :] = bgr
        noise = rng.normal(0, 4, canvas.shape)
        noisy = np.clip(canvas.astype(np.float32) + noise, 0, 255).astype(np.uint8)
        full_path = os.path.join(output_dir, filename)
        cv2.imwrite(full_path, noisy)
        paths[filename] = full_path

    return paths


if __name__ == "__main__":
    sample_dir = os.path.join("ml_engine", "data", "sample_images")
    created = create_demo_flap_images(sample_dir)
    print("=== Upgraded CIE-LAB Flap-Color Analyzer Self-Test ===")
    for name, path in created.items():
        result = analyze_flap_image_file(path)
        print(f"\nImage: {name}")
        for k, v in result.items():
            print(f"  {k}: {v}")