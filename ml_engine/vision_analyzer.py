"""
Local OpenCV Camera / Image Flap-Color Analyzer (Zero External APIs).
Extracts redness, cyanosis (venous congestion), and pallor (arterial ischemia)
indices from RGB, HSV, and CIE-LAB color spaces inside the central flap ROI.
"""

import os
from typing import Dict

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

    # Focus on center 50% Region of Interest (ROI) where the flap is framed
    y1, y2 = int(height * 0.25), int(height * 0.75)
    x1, x2 = int(width * 0.25), int(width * 0.75)
    roi_bgr = bgr_image[y1:y2, x1:x2]

    roi_rgb = cv2.cvtColor(roi_bgr, cv2.COLOR_BGR2RGB).astype(np.float32)
    roi_hsv = cv2.cvtColor(roi_bgr, cv2.COLOR_BGR2HSV).astype(np.float32)
    roi_lab = cv2.cvtColor(roi_bgr, cv2.COLOR_BGR2LAB).astype(np.float32)

    mean_r = float(np.mean(roi_rgb[:, :, 0]))
    mean_g = float(np.mean(roi_rgb[:, :, 1]))
    mean_b = float(np.mean(roi_rgb[:, :, 2]))
    total_rgb = mean_r + mean_g + mean_b + 1e-5

    mean_s_norm = float(np.mean(roi_hsv[:, :, 1])) / 255.0
    mean_l_norm = float(np.mean(roi_lab[:, :, 0])) / 255.0
    mean_b_lab = float(np.mean(roi_lab[:, :, 2]))  # <128 shifts toward blue/dusky

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

    return {
        "visual_redness_index": round(redness_index, 4),
        "visual_cyanosis_index": round(cyanosis_index, 4),
        "visual_pallor_index": round(pallor_index, 4),
        "visual_perfusion_score": visual_score,
        "visual_triage_label": triage_label,
        "mean_rgb": [round(mean_r, 1), round(mean_g, 1), round(mean_b, 1)],
        "image_dimensions": [int(width), int(height)],
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
    print("=== Camera Flap-Color Analyzer Self-Test ===")
    for name, path in created.items():
        result = analyze_flap_image_file(path)
        print(f"\nImage: {name}")
        for k, v in result.items():
            print(f"  {k}: {v}")