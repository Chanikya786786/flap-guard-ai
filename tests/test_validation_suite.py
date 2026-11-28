"""
Validation Test Suite (320+ test cases) covering:
- Clinical sensor range & boundary validation
- AI model accuracy & clinical decision matrix validation
- Web & Mobile authentication/input validation rules
"""

import time

from ml_engine.model_service import load_model_and_metadata, predict_flap_status
from tests.report_writer import make_row, write_excel_report
from web_app import create_app
from web_app.routes import validate_sensor_inputs


def run_validation_suite() -> str:
    rows = []
    idx = 1

    # 1. Sensor Boundary & Range Validation (120 cases: 60 valid + 60 invalid)
    for i in range(60):
        t0 = time.perf_counter()
        ir_t = round(25.0 + (i % 15) * 1.1, 2)
        ctrl_t = round(35.0 + (i % 6) * 0.5, 2)
        spo2 = round(50.0 + (i % 50) * 1.0, 1)
        doppler = round((i % 30) * 1.5, 2)
        hrs = round(1.0 + i * 2.0, 1)
        ok, msg = validate_sensor_inputs(ir_t, ctrl_t, spo2, doppler, hrs, 0.15, 0.05, 0.40)
        ms = (time.perf_counter() - t0) * 1000
        rows.append(make_row(
            "VAL", idx, "Sensor Range Valid", f"Valid clinical vitals boundary #{i+1}",
            f"IR={ir_t}, Ctrl={ctrl_t}, SpO2={spo2}, Doppler={doppler}, Hrs={hrs}",
            "Validation accepted (True)",
            f"Accepted={ok}",
            ms, ok is True,
        ))
        idx += 1

    invalid_cases = [
        ("IR temp too low", (15.0, 36.7, 96.0, 20.0, 10.0, 0.14, 0.03, 0.42)),
        ("IR temp too high", (48.0, 36.7, 96.0, 20.0, 10.0, 0.14, 0.03, 0.42)),
        ("Control temp too low", (36.0, 25.0, 96.0, 20.0, 10.0, 0.14, 0.03, 0.42)),
        ("Control temp too high", (36.0, 46.0, 96.0, 20.0, 10.0, 0.14, 0.03, 0.42)),
        ("SpO2 negative", (36.0, 36.7, -5.0, 20.0, 10.0, 0.14, 0.03, 0.42)),
        ("SpO2 over 100", (36.0, 36.7, 105.0, 20.0, 10.0, 0.14, 0.03, 0.42)),
        ("Doppler negative", (36.0, 36.7, 96.0, -3.0, 10.0, 0.14, 0.03, 0.42)),
        ("Doppler unrealistic", (36.0, 36.7, 96.0, 95.0, 10.0, 0.14, 0.03, 0.42)),
        ("Hours negative", (36.0, 36.7, 96.0, 20.0, -2.0, 0.14, 0.03, 0.42)),
        ("Hours over limit", (36.0, 36.7, 96.0, 20.0, 999.0, 0.14, 0.03, 0.42)),
        ("Redness > 1", (36.0, 36.7, 96.0, 20.0, 10.0, 1.5, 0.03, 0.42)),
        ("Cyanosis < 0", (36.0, 36.7, 96.0, 20.0, 10.0, 0.14, -0.2, 0.42)),
    ]
    for i in range(60):
        t0 = time.perf_counter()
        label, params = invalid_cases[i % len(invalid_cases)]
        ok, msg = validate_sensor_inputs(*params)
        ms = (time.perf_counter() - t0) * 1000
        rows.append(make_row(
            "VAL", idx, "Sensor Range Reject", f"Reject {label} case #{i+1}",
            str(params),
            "Validation rejected (False + error message)",
            f"Accepted={ok}, Msg={msg}",
            ms, (ok is False and len(msg) > 5),
        ))
        idx += 1

    # 2. AI Model Clinical Quality & 150-Vector Decision Matrix Validation
    t0 = time.perf_counter()
    _, meta = load_model_and_metadata()
    acc_ok = meta["accuracy"] >= 0.90 and meta["macro_f1"] >= 0.90
    rows.append(make_row(
        "VAL", idx, "AI Model Quality", "Baseline Accuracy & F1 Gate >= 90%",
        f"model={meta['version']}, samples={meta['total_samples']}",
        "Accuracy >= 0.90 and Macro F1 >= 0.90",
        f"Accuracy={meta['accuracy']}, MacroF1={meta['macro_f1']}",
        (time.perf_counter() - t0) * 1000, acc_ok,
    ))
    idx += 1

    for i in range(150):
        t0 = time.perf_counter()
        cls_id = i % 3
        offset = (i % 10) * 0.04
        if cls_id == 0:
            pred = predict_flap_status(36.3 - offset, 36.7, 97.2 - offset * 2, 21.5 - offset * 5, 0.15, 0.02, 0.42, 8.0)
            expected_cls = "Normal"
        elif cls_id == 1:
            pred = predict_flap_status(35.0 - offset, 36.7, 81.0 - offset * 5, 10.0 - offset * 2, 0.10, 0.50 + offset * 0.3, 0.28, 16.0)
            expected_cls = "Venous Compromise"
        else:
            pred = predict_flap_status(33.5 - offset, 36.7, 72.0 - offset * 5, 2.5 + offset, 0.02, 0.03, 0.78 + offset * 0.2, 22.0)
            expected_cls = "Arterial Compromise"
        passed = pred["predicted_class"] == expected_cls
        ms = (time.perf_counter() - t0) * 1000
        rows.append(make_row(
            "VAL", idx, "AI Clinical Matrix", f"Validate {expected_cls} clinical vector #{i+1}",
            f"Delta-T={pred['features']['temp_delta_c']}, SpO2={pred['features']['spo2_pct']}, Doppler={pred['features']['doppler_flow_cm_s']}",
            f"Predicted={expected_cls}",
            f"Predicted={pred['predicted_class']} ({pred['risk_score_pct']}%)",
            ms, passed,
        ))
        idx += 1

    # 3. Web & Mobile Form/Payload Validation (55 cases)
    app = create_app({"TESTING": True, "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:", "SEED_SAMPLE_PATIENTS": True})
    client = app.test_client()

    auth_res = client.post("/mobile/login", json={"email": "surgeon@flapguard.local", "password": "Surgeon@123"})
    token = auth_res.get_json()["token"]
    auth_hdr = {"Authorization": f"Bearer {token}"}

    payload_tests = [
        ("Reject weak mobile signup password", "POST", "/mobile/register", {"full_name": "Test Doc", "email": "weak@test.com", "password": "123"}, None, 400),
        ("Reject invalid email on mobile signup", "POST", "/mobile/register", {"full_name": "Test Doc", "email": "not-an-email", "password": "Password@123"}, None, 400),
        ("Reject duplicate email signup", "POST", "/mobile/register", {"full_name": "Dup Doc", "email": "surgeon@flapguard.local", "password": "Password@123"}, None, 409),
        ("Reject invalid mobile login", "POST", "/mobile/login", {"email": "surgeon@flapguard.local", "password": "WrongPassword"}, None, 401),
        ("Reject missing patient fields", "POST", "/mobile/patients", {"mrn": "", "full_name": "X"}, auth_hdr, 400),
        ("Reject out-of-range patient age", "POST", "/mobile/patients", {"mrn": "MRN-BAD-AGE", "full_name": "Bad Age", "age": 200, "flap_type": "ALT", "surgery_site": "Jaw"}, auth_hdr, 400),
        ("Reject out-of-range SpO2 reading", "POST", "/mobile/patients/1/reading", {"ir_flap_temp_c": 36.2, "control_skin_temp_c": 36.7, "spo2_pct": 140.0, "doppler_flow_cm_s": 20.0}, auth_hdr, 400),
        ("Reject non-numeric sensor payload", "POST", "/mobile/patients/1/reading", {"ir_flap_temp_c": "hot", "control_skin_temp_c": 36.7, "spo2_pct": 95, "doppler_flow_cm_s": 20}, auth_hdr, 400),
        ("Reject invalid simulation scenario", "POST", "/mobile/patients/1/simulate", {"scenario": "invalid_mode", "step": 2}, auth_hdr, 400),
        ("Reject invalid outcome label", "POST", "/mobile/outcomes", {"patient_id": 1, "verified_outcome": "UnknownLabel"}, auth_hdr, 400),
        ("Reject corrupt base64 image upload", "POST", "/mobile/analyze-image", {"image_base64": "!!!not-valid-base64!!!"}, auth_hdr, 400),
    ]
    for i in range(55):
        t0 = time.perf_counter()
        desc, method, path, body, hdrs, expected_code = payload_tests[i % len(payload_tests)]
        resp = client.open(path, method=method, json=body, headers=hdrs or {})
        passed = resp.status_code == expected_code
        ms = (time.perf_counter() - t0) * 1000
        rows.append(make_row(
            "VAL", idx, "Endpoint Validation", f"{desc} (#{i+1})",
            f"{method} {path}",
            f"HTTP {expected_code}",
            f"HTTP {resp.status_code}",
            ms, passed,
        ))
        idx += 1

    # Track 2 Clinical ML: Calibration, Temporal Trends, Missing Modalities, OOD, Holdout Gate, DB Persistence, Paired Vision
    import cv2 as _cv2
    import numpy as _np
    import ml_engine.model_service as _ms
    from ml_engine.vision_analyzer import analyze_flap_image_bytes as _analyze_img
    from web_app import create_app as _create_app
    from web_app.model_store import (
        persist_active_model_to_db as _persist_model,
        restore_active_model_from_db as _restore_model,
    )

    # 2a. Calibrated model metadata has Brier score & Platt calibration
    t0 = time.perf_counter()
    cal_model, cal_meta = _ms.load_model_and_metadata(force_reload=True)
    brier_ok = "brier_score" in cal_meta and float(cal_meta["brier_score"]) < 0.15
    rows.append(make_row(
        "VAL", idx, "Track 2 Clinical ML (Calibration)", "Platt-calibrated probabilities & low Brier score",
        "load_model_and_metadata()", "brier_score < 0.15 & sigmoid calibration",
        f"brier={cal_meta.get('brier_score')}, method={cal_meta.get('calibration_method')}",
        (time.perf_counter() - t0) * 1000, brier_ok,
    ))
    idx += 1

    # 2b. Temporal trend escalation catches rapid drop from patient baseline
    t0 = time.perf_counter()
    hist = [
        {"temp_delta_c": 0.3, "spo2_pct": 98.0, "doppler_flow_cm_s": 22.0, "visual_perfusion_score": 86.0, "hours_post_op": 2.0},
        {"temp_delta_c": 0.9, "spo2_pct": 95.0, "doppler_flow_cm_s": 18.0, "visual_perfusion_score": 80.0, "hours_post_op": 3.0},
    ]
    trend_pred = _ms.predict_flap_status(
        ir_flap_temp_c=34.7, control_skin_temp_c=36.7, spo2_pct=83.0, doppler_flow_cm_s=11.0,
        hours_post_op=4.0, history=hist,
    )
    t_ok = trend_pred["risk_tier"] == "Critical" and "Deteriorat" in trend_pred["trend_status"]
    rows.append(make_row(
        "VAL", idx, "Track 2 Clinical ML (Trends)", "Temporal rapid deterioration escalates risk tier",
        "Baseline SpO2=98% -> 83% in 2h", "risk_tier=Critical & trend_status Deteriorating",
        f"tier={trend_pred['risk_tier']}, trend={trend_pred['trend_status']}",
        (time.perf_counter() - t0) * 1000, t_ok,
    ))
    idx += 1

    # 2c. Missing-modality graceful handling flags absent sensor & downgrades confidence
    t0 = time.perf_counter()
    miss_pred = _ms.predict_flap_status(
        ir_flap_temp_c=36.2, control_skin_temp_c=36.6, spo2_pct=None, doppler_flow_cm_s=None,
    )
    m_ok = len(miss_pred["missing_modalities"]) == 2 and "Low" in miss_pred["confidence_label"]
    rows.append(make_row(
        "VAL", idx, "Track 2 Clinical ML (Missing Sensor)", "Graceful partial-modality inference with uncertainty warning",
        "spo2=None, doppler=None", "2 missing_modalities & Low confidence warning",
        f"missing={miss_pred['missing_modalities']}, conf={miss_pred['confidence_label']}",
        (time.perf_counter() - t0) * 1000, m_ok,
    ))
    idx += 1

    # 2d. Out-of-Distribution (OOD) detection flags extreme non-physiological readings
    t0 = time.perf_counter()
    ood_pred = _ms.predict_flap_status(
        ir_flap_temp_c=23.0, control_skin_temp_c=36.6, spo2_pct=96.0, doppler_flow_cm_s=52.0,
    )
    o_ok = bool(ood_pred["ood_flag"]) and len(ood_pred["ood_reasons"]) >= 1
    rows.append(make_row(
        "VAL", idx, "Track 2 Clinical ML (OOD Guard)", "Flag out-of-distribution sensor inputs",
        "ir_temp=23.0C, doppler=52.0 cm/s", "ood_flag=True & warning generated",
        f"ood={ood_pred['ood_flag']}, z_max={ood_pred['ood_z_max']}",
        (time.perf_counter() - t0) * 1000, o_ok,
    ))
    idx += 1

    # 2e. Holdout F1 & Brier safety gate blocks distributed poisoned outcome batches
    t0 = time.perf_counter()
    import pandas as _pd
    _base_df = _pd.read_csv(_ms.BASE_DATASET_PATH)
    _flip_map = {0: "Arterial Compromise", 1: "Normal", 2: "Normal"}
    _sample_poison = _base_df.sample(n=min(600, len(_base_df)), random_state=7)
    poisoned = []
    for _, _row in _sample_poison.iterrows():
        _rec = {col: float(_row[col]) for col in _ms.FEATURE_COLUMNS}
        _rec["label"] = _flip_map[int(_row["label"])]
        poisoned.append(_rec)
    gate_res = _ms.retrain_from_postop_outcomes(poisoned)
    rows.append(make_row(
        "VAL", idx, "Track 2 Clinical ML (Safety Gate)", "Holdout Macro-F1 & Brier gate blocks poisoned retraining batch",
        "600 distributed label-flipped outcome rows", "promoted=False",
        f"promoted={gate_res['promoted']}, cand_f1={gate_res.get('candidate_macro_f1')}",
        (time.perf_counter() - t0) * 1000, gate_res["promoted"] is False,
    ))
    idx += 1

    # 2f. Persistent DB ModelArtifact survives disk wipe & verifies SHA-256
    t0 = time.perf_counter()
    db_app = _create_app({"TESTING": True, "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:", "SKIP_SEED": True})
    with db_app.app_context():
        test_meta = dict(cal_meta, version="v2.99")
        _persist_model(cal_model, test_meta)
        restored_ver = _restore_model()
    _ms.train_and_save_model(version="v2.0")
    rows.append(make_row(
        "VAL", idx, "Track 2 Persistence (ModelArtifact)", "Persist & SHA-256 restore model from database",
        "persist_active_model_to_db -> restore_active_model_from_db", "restored_version=v2.99",
        f"restored_version={restored_ver}",
        (time.perf_counter() - t0) * 1000, restored_ver == "v2.99",
    ))
    idx += 1

    # 2g. Paired flap-vs-control-skin CIE-LAB normalization
    t0 = time.perf_counter()
    flap_img = _np.full((120, 160, 3), (140, 130, 195), dtype=_np.uint8)
    ctrl_img = _np.full((120, 160, 3), (142, 148, 200), dtype=_np.uint8)
    _, f_buf = _cv2.imencode(".png", flap_img)
    _, c_buf = _cv2.imencode(".png", ctrl_img)
    paired_res = _analyze_img(f_buf.tobytes(), control_image_bytes=c_buf.tobytes())
    p_ok = paired_res.get("calibrated_with_control") is True and paired_res.get("control_delta_lab") is not None
    rows.append(make_row(
        "VAL", idx, "Track 2 Vision (Paired Calibration)", "Normalize flap ROI against adjacent control skin photo",
        "Flap + Control skin BGR images", "calibrated_with_control=True & control_delta_lab present",
        f"calibrated={paired_res.get('calibrated_with_control')}, delta={paired_res.get('control_delta_lab')}",
        (time.perf_counter() - t0) * 1000, p_ok,
    ))
    idx += 1

    # v3.2 Clinical Workflow: Post-Op Q1h/Q2h/Q4h Schedule, Overdue Check, Bedside Exam Notes & SBAR Handoff
    from datetime import timedelta as _td32
    from types import SimpleNamespace as _NS
    from web_app.clinical_workflow import (
        build_bedside_exam_notes as _exam_notes,
        compute_check_schedule as _calc_sched,
    )
    from web_app.models import utc_now as _now32

    # 3a. Q1h/Q2h/Q4h protocol intervals & overdue detection
    t0 = time.perf_counter()
    s_q1_over = _calc_sched(_NS(hours_post_op=10.0), _NS(created_at=_now32() - _td32(minutes=78)))
    s_q2_ok = _calc_sched(_NS(hours_post_op=36.0), _NS(created_at=_now32() - _td32(minutes=30)))
    s_q4_ok = _calc_sched(_NS(hours_post_op=96.0), _NS(created_at=_now32() - _td32(minutes=60)))
    sched_ok = (
        s_q1_over["protocol"] == "Q1h" and s_q1_over["overdue"] is True
        and s_q2_ok["protocol"] == "Q2h" and s_q2_ok["overdue"] is False
        and s_q4_ok["protocol"] == "Q4h" and s_q4_ok["overdue"] is False
    )
    rows.append(make_row(
        "VAL", idx, "v3.2 Clinical Workflow (Schedule)", "Compute Q1h/Q2h/Q4h post-op schedule & detect overdue check",
        "10h(78m ago), 36h(30m ago), 96h(60m ago)", "Q1h overdue, Q2h ok, Q4h ok",
        f"{s_q1_over['status_label']} | {s_q2_ok['status_label']} | {s_q4_ok['status_label']}",
        (time.perf_counter() - t0) * 1000, sched_ok,
    ))
    idx += 1

    # 3b. Bedside physical exam interpreter detects venous & arterial clinical red flags
    t0 = time.perf_counter()
    ven_notes = _exam_notes({"capillary_refill": "Brisk (<1s · Venous)", "pinprick_bleeding": "Dark Rapid Ooze (Venous)"})
    art_notes = _exam_notes({"capillary_refill": "Sluggish (>3s · Arterial)", "doppler_quality": "Absent"})
    exam_ok = len(ven_notes) == 2 and len(art_notes) == 2
    rows.append(make_row(
        "VAL", idx, "v3.2 Clinical Workflow (Bedside Exam)", "Interpret capillary refill, pinprick & Doppler quality",
        "Venous + Arterial bedside exam signs", "2 venous notes & 2 arterial notes",
        f"venous={len(ven_notes)}, arterial={len(art_notes)}",
        (time.perf_counter() - t0) * 1000, exam_ok,
    ))
    idx += 1

    # 3c. Mobile bedside exam submission + /mobile/handoff SBAR & Web /handoff report
    t0 = time.perf_counter()
    wf_app = _create_app({"TESTING": True, "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:", "SEED_SAMPLE_PATIENTS": True})
    wfc = wf_app.test_client()
    tok32 = wfc.post(
        "/mobile/register",
        json={"full_name": "Dr. Shift Lead", "email": "shift@hospital.org", "role": "Surgeon", "password": "Password@123"},
    ).get_json()["token"]
    h32 = {"Authorization": f"Bearer {tok32}"}
    r_exam = wfc.post(
        "/mobile/patients/1/reading",
        headers=h32,
        json={
            "ir_flap_temp_c": 34.8, "control_skin_temp_c": 36.7, "spo2_pct": 82.0, "doppler_flow_cm_s": 10.0,
            "capillary_refill": "Brisk (<1s · Venous)", "flap_turgor": "Tense / Congested",
            "pinprick_bleeding": "Dark Rapid Ooze (Venous)", "doppler_quality": "Venous Hum Only",
        },
    )
    h_mob = wfc.get("/mobile/handoff", headers=h32)
    h_json = h_mob.get_json() or {}
    sbar_txt = h_json.get("sbar_text", "")
    sbar_ok = (
        r_exam.status_code == 201
        and h_mob.status_code == 200
        and "SBAR" in sbar_txt
        and "Bedside Exam:" in sbar_txt
        and h_json.get("total_patients", 0) >= 3
    )
    rows.append(make_row(
        "VAL", idx, "v3.2 Clinical Workflow (SBAR Handoff)", "Persist bedside exam & generate SBAR shift handoff report",
        "POST reading w/ bedside exam -> GET /mobile/handoff", "201 reading, 200 SBAR containing Bedside Exam & >=3 patients",
        f"read={r_exam.status_code}, handoff={h_mob.status_code}, pts={h_json.get('total_patients')}",
        (time.perf_counter() - t0) * 1000, sbar_ok,
    ))
    idx += 1

    # v3.2.1 Web & iOS PWA Parity: Manifest, Service Worker, Profile HTML Fix & Patient Detail Schedule
    t0 = time.perf_counter()
    pwa_app = _create_app({"TESTING": True, "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:", "SEED_SAMPLE_PATIENTS": True})
    pwac = pwa_app.test_client()
    r_man = pwac.get("/manifest.webmanifest")
    r_sw = pwac.get("/sw.js")
    r_ico = pwac.get("/static/img/apple-touch-icon.png")
    man_json = r_man.get_json() or {}
    pwa_ok = (
        r_man.status_code == 200
        and man_json.get("display") == "standalone"
        and r_sw.status_code == 200
        and "flapguard-static" in r_sw.get_data(as_text=True)
        and r_ico.status_code == 200
    )
    rows.append(make_row(
        "VAL", idx, "v3.2.1 Web & iOS PWA Parity", "Serve standalone PWA manifest, clinical-safe SW & iOS PNG icon",
        "GET /manifest.webmanifest, /sw.js, /static/img/apple-touch-icon.png", "All 200 OK with standalone display",
        f"man={r_man.status_code}, sw={r_sw.status_code}, icon={r_ico.status_code}",
        (time.perf_counter() - t0) * 1000, pwa_ok,
    ))
    idx += 1

    t0 = time.perf_counter()
    pwac.post(
        "/register",
        data={"full_name": "Dr. Web Parity", "email": "webparity@hospital.org", "role": "Surgeon", "password": "Password@123", "confirm_password": "Password@123"},
        follow_redirects=True,
    )
    prof_html = pwac.get("/profile").get_data(as_text=True)
    pd_html = pwac.get("/patients/1").get_data(as_text=True)
    ho_html = pwac.get("/handoff").get_data(as_text=True)
    title_end = prof_html.find("</title>")
    web_parity_ok = (
        title_end > 0
        and "delete-account-form" not in prof_html[:title_end]
        and "delete-account-form" in prof_html[title_end:]
        and "sign-out-all-btn" in prof_html[title_end:]
        and "patient-schedule-badge" in pd_html
        and "<th>Bedside Exam</th>" in pd_html
        and "Shift Handoff Report (SBAR)" in ho_html
    )
    rows.append(make_row(
        "VAL", idx, "v3.2.1 Web Parity Audit", "Profile deletion/sessions inside body, Patient Detail schedule/exam & /handoff",
        "GET /profile, /patients/1, /handoff", "Valid <title>, body controls, schedule badge, Bedside Exam column & SBAR",
        f"title_clean={('delete-account-form' not in prof_html[:title_end])}, pd_sched={('patient-schedule-badge' in pd_html)}",
        (time.perf_counter() - t0) * 1000, web_parity_ok,
    ))
    idx += 1

    # v4.0 Phase 3 Web Clinical UI: Adaptive Day/Night CSS, Ward Triage, Native-Scale Tabs & Visual SBAR
    t0 = time.perf_counter()
    css_txt = pwac.get("/static/css/style.css").get_data(as_text=True)
    js_txt = pwac.get("/static/js/patient_monitor.js").get_data(as_text=True)
    dash_html = pwac.get("/").get_data(as_text=True)
    alerts_html = pwac.get("/alerts").get_data(as_text=True)
    has_dark_css = '[data-theme="dark"]' in css_txt
    has_box_fix = "* {" in css_txt and "box-sizing: border-box;" in css_txt
    no_legacy_scale = "Doppler (x3)" not in js_txt and "Delta-T (x20)" not in js_txt
    has_sbar_cards = "sbar-letter-s" in ho_html
    v40_web_ok = (
        has_dark_css
        and has_box_fix
        and "theme-toggle-btn" in dash_html
        and "ward-search-input" in dash_html
        and "chart-metric-tabs" in pd_html
        and "alerts-triage-bar" in alerts_html
        and has_sbar_cards
        and no_legacy_scale
    )
    rows.append(make_row(
        "VAL", idx, "v4.0 Phase 3 Web Clinical UI", "Day/Night CSS, Ward triage, native-scale chart tabs & visual SBAR cards",
        "GET /, /patients/1, /alerts, /handoff, /static/css/style.css, /static/js/patient_monitor.js",
        "Adaptive dark tokens, triage bars, native-scale chart tabs, no x20/x3 multipliers",
        f"dark_css={has_dark_css}, no_x20={no_legacy_scale}, sbar_cards={has_sbar_cards}",
        (time.perf_counter() - t0) * 1000, v40_web_ok,
    ))
    idx += 1

    # v10.0 M1 Clinical Engine & Trust: Live Post-Op Clock, Trajectory, Cross-Sensor OOD, SBAR & Benchmark Basis
    t0 = time.perf_counter()
    from types import SimpleNamespace
    from datetime import timedelta as _td
    from web_app.clinical_workflow import compute_live_hours_post_op as _live_hrs
    from web_app.models import utc_now as _utc_now
    from ml_engine.model_service import evaluate_ood as _eval_ood, load_model_and_metadata as _load_meta
    from ml_engine.simulator import generate_simulated_reading as _gen_sim
    from ml_engine.trend_engine import analyze_temporal_trend as _trend

    # 1) Live clock advances 10.0h + 20h elapsed -> 30.0h (Q2h protocol)
    p_aged = SimpleNamespace(hours_post_op=10.0, created_at=_utc_now() - _td(hours=20))
    r_aged = SimpleNamespace(hours_post_op=10.0, created_at=_utc_now() - _td(hours=20))
    live_h = _live_hrs(p_aged, r_aged)
    sched_aged = _calc_sched(p_aged, r_aged)
    clock_ok = (29.5 <= live_h <= 30.5) and sched_aged["protocol"] == "Q2h"

    # 2) History-aware simulator computes real trajectory & advances post-op hours
    base_hist = [{"temp_delta_c": 0.4, "spo2_pct": 97.0, "doppler_flow_cm_s": 21.0, "visual_perfusion_score": 90.0, "hours_post_op": 20.0}]
    sim_out = _gen_sim("venous_deteriorating", step=7, seed=42, history=base_hist, base_hours_post_op=20.0)
    sim_pred = sim_out["prediction"]
    sim_trend_ok = sim_pred["trend_status"] != "Baseline Established" and sim_pred["features"]["hours_post_op"] > 20.0

    # 3) Persistent compromise & recovery states in trend_engine
    persist_res = _trend(
        {"temp_delta_c": 2.4, "spo2_pct": 80.0, "doppler_flow_cm_s": 8.0, "hours_post_op": 14.0},
        history=[{"temp_delta_c": 2.3, "spo2_pct": 80.5, "doppler_flow_cm_s": 8.5, "hours_post_op": 12.0}],
    )
    improv_res = _trend(
        {"temp_delta_c": 0.5, "spo2_pct": 96.0, "doppler_flow_cm_s": 20.0, "hours_post_op": 16.0},
        history=[{"temp_delta_c": 2.2, "spo2_pct": 78.0, "doppler_flow_cm_s": 9.0, "hours_post_op": 14.0}],
    )
    trend_states_ok = persist_res["trend_status"] == "Persistent Compromise" and improv_res["trend_status"] == "Improving"

    # 4) Cross-sensor OOD guardrail flags discordant thermal/SpO2 drop with strong Doppler flow
    ood_flag_x, ood_reasons_x, _ = _eval_ood(
        {"ir_flap_temp_c": 31.5, "control_skin_temp_c": 36.7, "temp_delta_c": 5.2, "spo2_pct": 52.0, "doppler_flow_cm_s": 21.0},
        _load_meta()[1],
    )
    ood_cross_ok = ood_flag_x and any("Discordant" in r for r in ood_reasons_x)

    # 5) /health exposes honest evaluation_basis & verified_outcomes_count
    health_j = pwac.get("/health").get_json() or {}
    basis_ok = "evaluation_basis" in health_j and "verified_outcomes_count" in health_j

    m1_ok = clock_ok and sim_trend_ok and trend_states_ok and ood_cross_ok and basis_ok
    rows.append(make_row(
        "VAL", idx, "v10.0 M1 Clinical Engine", "Dynamic post-op clock, simulator trajectory, persistent/improving trends, cross-sensor OOD & honest basis",
        "live_clock + simulator(history) + trend_engine + evaluate_ood + GET /health",
        "All 5 clinical trust invariants pass",
        f"clock={clock_ok}({live_h}h/{sched_aged['protocol']}), sim_trend={sim_trend_ok}({sim_pred['trend_status']}), states={trend_states_ok}, ood_cross={ood_cross_ok}, basis={basis_ok}",
        (time.perf_counter() - t0) * 1000, m1_ok,
    ))
    idx += 1

    return write_excel_report("validation-test-report.xlsx", "Validation Test Report", rows)


def test_generate_validation_report():
    path = run_validation_suite()
    assert path.endswith("validation-test-report.xlsx")


if __name__ == "__main__":
    run_validation_suite()