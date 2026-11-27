"""
Local OpenCV Camera / Image Flap-Color Analyzer (Zero External APIs).
Performs CIE-LAB / RGB / HSV colorimetric perfusion extraction inside the
centered 50% flap ROI, excluding specular flash glare and deep shadows,
with automated sharpness/exposure diagnostics and optional paired control-skin
illumination & skin-tone calibration.
"""

import os
from typing import Dict, List, Optional

import cv2
import numpy as np

from ml_engine.fusion import (
    classify_visual_triage,
    compute_visual_perfusion_score,
)


def _extract_roi_stats(bgr_image: np.ndarray) -> Dict[str, object]:
    if bgr_image is None or bgr_image.size == 0:
        raise ValueError("Invalid or unreadable image data.")

    height, width = bgr_image.shape[:2]
    if height < 20 or width < 20:
        raise ValueError("Image resolution too small (minimum 20x20 pixels).")

    y1, y2 = int(height * 0.25), int(height * 0.75)
    x1, x2 = int(width * 0.25), int(width * 0.75)
    roi_bgr = bgr_image[y1:y2, x1:x2]

    roi_rgb = cv2.cvtColor(roi_bgr, cv2.COLOR_BGR2RGB).astype(np.float32)
    roi_hsv = cv2.cvtColor(roi_bgr, cv2.COLOR_BGR2HSV).astype(np.float32)
    roi_lab = cv2.cvtColor(roi_bgr, cv2.COLOR_BGR2LAB).astype(np.float32)
    roi_gray = cv2.cvtColor(roi_bgr, cv2.COLOR_BGR2GRAY)

    sharpness_score = float(cv2.Laplacian(roi_gray, cv2.CV_64F).var())
    brightness_score = float(np.mean(roi_lab[:, :, 0]))

    glare_mask = (roi_hsv[:, :, 2] > 245.0) & (roi_hsv[:, :, 1] < 25.0)
    shadow_mask = roi_hsv[:, :, 2] < 25.0
    glare_pct = float(np.mean(glare_mask) * 100.0)
    shadow_pct = float(np.mean(shadow_mask) * 100.0)

    valid_mask = ~(glare_mask | shadow_mask)
    if float(np.mean(valid_mask)) >= 0.20:
        px_rgb = roi_rgb[valid_mask]
        px_hsv = roi_hsv[valid_mask]
        px_lab = roi_lab[valid_mask]
    else:
        px_rgb = roi_rgb.reshape(-1, 3)
        px_hsv = roi_hsv.reshape(-1, 3)
        px_lab = roi_lab.reshape(-1, 3)

    return {
        "width": int(width),
        "height": int(height),
        "roi_box": [x1, y1, x2, y2],
        "mean_r": float(np.mean(px_rgb[:, 0])),
        "mean_g": float(np.mean(px_rgb[:, 1])),
        "mean_b": float(np.mean(px_rgb[:, 2])),
        "mean_s_norm": float(np.mean(px_hsv[:, 1])) / 255.0,
        "mean_l_norm": float(np.mean(px_lab[:, 0])) / 255.0,
        "mean_l_lab": float(np.mean(px_lab[:, 0])),
        "mean_a_lab": float(np.mean(px_lab[:, 1])),
        "mean_b_lab": float(np.mean(px_lab[:, 2])),
        "sharpness_score": sharpness_score,
        "brightness_score": brightness_score,
        "glare_pct": glare_pct,
        "shadow_pct": shadow_pct,
    }


def _analyze_bgr_array(
    bgr_image: np.ndarray,
    control_bgr: Optional[np.ndarray] = None,
) -> Dict[str, object]:
    flap = _extract_roi_stats(bgr_image)

    mean_r = flap["mean_r"]
    mean_g = flap["mean_g"]
    mean_b = flap["mean_b"]
    mean_s_norm = flap["mean_s_norm"]
    mean_l_norm = flap["mean_l_norm"]
    mean_a_lab = flap["mean_a_lab"]
    mean_b_lab = flap["mean_b_lab"]

    calibrated_with_control = False
    control_delta_lab = None
    calibration_note = "Single-frame uncalibrated scan (upload control skin photo for lighting/skin-tone calibration)."

    if control_bgr is not None and control_bgr.size > 0:
        ctrl = _extract_roi_stats(control_bgr)
        calibrated_with_control = True

        # Relative CIE-LAB shift: flap minus adjacent healthy control skin under same ambient light
        delta_l = round(flap["mean_l_lab"] - ctrl["mean_l_lab"], 2)
        delta_a = round(flap["mean_a_lab"] - ctrl["mean_a_lab"], 2)
        delta_b = round(flap["mean_b_lab"] - ctrl["mean_b_lab"], 2)
        control_delta_lab = {"delta_L": delta_l, "delta_a": delta_a, "delta_b": delta_b}

        # Normalize ambient color cast using control skin as white/skin-tone reference
        ref_r = max(ctrl["mean_r"], 15.0)
        ref_g = max(ctrl["mean_g"], 15.0)
        ref_b = max(ctrl["mean_b"], 15.0)
        # Anchor control skin to neutral healthy reference (R=200, G=150, B=140)
        mean_r = float(np.clip(mean_r * (200.0 / ref_r), 0.0, 255.0))
        mean_g = float(np.clip(mean_g * (150.0 / ref_g), 0.0, 255.0))
        mean_b = float(np.clip(mean_b * (140.0 / ref_b), 0.0, 255.0))
        mean_a_lab = float(np.clip(142.0 + delta_a, 0.0, 255.0))
        mean_b_lab = float(np.clip(134.0 + delta_b, 0.0, 255.0))
        mean_l_norm = float(np.clip(0.65 + (delta_l / 255.0), 0.0, 1.0))

        calibration_note = (
            f"Paired control-skin CIE-LAB calibrated "
            f"(ΔL={delta_l:+.1f}, Δa={delta_a:+.1f}, Δb={delta_b:+.1f})."
        )

    total_rgb = mean_r + mean_g + mean_b + 1e-5
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

    brightness_score = flap["brightness_score"]
    glare_pct = flap["glare_pct"]
    shadow_pct = flap["shadow_pct"]
    sharpness_score = flap["sharpness_score"]

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
        "calibrated_with_control": calibrated_with_control,
        "control_delta_lab": control_delta_lab,
        "calibration_note": calibration_note,
        "roi_box": flap["roi_box"],
        "image_dimensions": [flap["width"], flap["height"]],
        "method_note": (
            "CIE-LAB/RGB colorimetric perfusion estimator on centered flap ROI "
            "(supports paired control-skin calibration; fused with thermal, SpO2, and Doppler sensors)."
        ),
    }


def analyze_flap_image_bytes(
    image_bytes: bytes,
    control_image_bytes: Optional[bytes] = None,
) -> Dict[str, object]:
    """Analyze flap color from raw image bytes, with optional paired control-skin reference bytes."""
    if not image_bytes:
        raise ValueError("Empty image payload received.")

    np_buffer = np.frombuffer(image_bytes, dtype=np.uint8)
    bgr_image = cv2.imdecode(np_buffer, cv2.IMREAD_COLOR)

    control_bgr = None
    if control_image_bytes:
        ctrl_buf = np.frombuffer(control_image_bytes, dtype=np.uint8)
        control_bgr = cv2.imdecode(ctrl_buf, cv2.IMREAD_COLOR)
        if control_bgr is None or control_bgr.size == 0:
            raise ValueError("Invalid or unreadable control skin reference image.")

    return _analyze_bgr_array(bgr_image, control_bgr=control_bgr)


def analyze_flap_image_file(file_path: str, control_file_path: Optional[str] = None) -> Dict[str, object]:
    """Analyze flap color from a local image file path."""
    if not os.path.exists(file_path):
        raise FileNotFoundError(f"Image file not found: {file_path}")

    bgr_image = cv2.imread(file_path, cv2.IMREAD_COLOR)
    control_bgr = cv2.imread(control_file_path, cv2.IMREAD_COLOR) if control_file_path else None
    return _analyze_bgr_array(bgr_image, control_bgr=control_bgr)


def create_demo_flap_images(output_dir: str) -> Dict[str, str]:
    """Generate 3 realistic synthetic test flap images for verification & demos."""
    os.makedirs(output_dir, exist_ok=True)
    rng = np.random.default_rng(42)

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
