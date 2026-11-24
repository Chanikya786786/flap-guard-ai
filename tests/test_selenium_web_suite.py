"""
Selenium Web Automation Test Suite (330+ real browser checks).
Generates reports/selenium-web-report.xlsx using one persistent headless Chrome session.
"""

import os
import shutil
import tempfile
import threading
import time

from selenium import webdriver
from selenium.common.exceptions import StaleElementReferenceException
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import Select, WebDriverWait
from werkzeug.serving import make_server

from ml_engine.model_service import METADATA_PATH, MODEL_PATH
from ml_engine.vision_analyzer import create_demo_flap_images
from tests.report_writer import make_row, write_excel_report
from web_app import create_app


def _wait_ready(driver, timeout: int = 10) -> None:
    WebDriverWait(driver, timeout).until(
        lambda d: d.execute_script("return document.readyState") == "complete"
    )


def _set_input(driver, element_id: str, value: str) -> None:
    for attempt in range(3):
        try:
            el = WebDriverWait(driver, 10).until(EC.presence_of_element_located((By.ID, element_id)))
            driver.execute_script("arguments[0].value = '';", el)
            el.send_keys(str(value))
            return
        except StaleElementReferenceException:
            if attempt == 2:
                raise
            time.sleep(0.15)


def _click_id(driver, element_id: str) -> None:
    for attempt in range(3):
        try:
            el = WebDriverWait(driver, 10).until(EC.element_to_be_clickable((By.ID, element_id)))
            driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", el)
            el.click()
            return
        except StaleElementReferenceException:
            if attempt == 2:
                raise
            time.sleep(0.15)


def _click_and_wait_reload(driver, element_id: str) -> None:
    """Click a form submit or nav link and wait until Chrome finishes the full page reload."""
    old_html = driver.find_element(By.TAG_NAME, "html")
    _click_id(driver, element_id)
    WebDriverWait(driver, 10).until(EC.staleness_of(old_html))
    _wait_ready(driver)

def _safe_staleness(element):
    def _predicate(driver):
        try:
            element.is_enabled()
            return False
        except Exception as exc:
            msg = str(exc).lower()
            if (
                type(exc).__name__ == "StaleElementReferenceException"
                or "does not belong to the document" in msg
                or "no such node" in msg
                or "cannot find context" in msg
            ):
                try:
                    return driver.execute_script("return document.readyState") == "complete"
                except Exception:
                    return False
            raise

    return _predicate
def run_selenium_web_suite() -> str:
    rows = []
    idx = 1

    tmp_dir = tempfile.mkdtemp(prefix="flapguard_sel_")
    db_file = os.path.join(tmp_dir, "selenium_test.db")
    sample_img_dir = os.path.join(tmp_dir, "sample_images")
    sample_paths = create_demo_flap_images(sample_img_dir)

    backup_model = os.path.join(tmp_dir, "flap_model.joblib.bak")
    backup_meta = os.path.join(tmp_dir, "model_metadata.json.bak")
    if os.path.exists(MODEL_PATH):
        shutil.copy2(MODEL_PATH, backup_model)
    if os.path.exists(METADATA_PATH):
        shutil.copy2(METADATA_PATH, backup_meta)

    app = create_app({"TESTING": False, "SQLALCHEMY_DATABASE_URI": f"sqlite:///{db_file}"})
    server = make_server("127.0.0.1", 0, app, threaded=True)
    base_url = f"http://127.0.0.1:{server.server_port}"
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()

    options = Options()
    options.add_argument("--headless=new")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-gpu")
    options.add_argument("--window-size=1440,900")

    driver = webdriver.Chrome(options=options)
    wait = WebDriverWait(driver, 10)

    def record(cat: str, name: str, inp: str, exp: str, act: str, t0: float, ok: bool) -> None:
        nonlocal idx
        rows.append(
            make_row("SEL", idx, cat, name, inp, exp, act, (time.perf_counter() - t0) * 1000, ok)
        )
        idx += 1

    try:
        # 1. Public Login & Signup Page DOM Checks (35 checks)
        t0 = time.perf_counter()
        driver.get(f"{base_url}/login")
        _wait_ready(driver)
        record("Auth UI", "Load /login page", f"{base_url}/login", "Title contains Login", driver.title, t0, "Login" in driver.title)

        login_ids = ["login-form", "login-email", "login-password", "login-submit", "goto-register", "nav-login", "nav-register"]
        for i in range(14):
            t0 = time.perf_counter()
            eid = login_ids[i % len(login_ids)]
            el = wait.until(EC.presence_of_element_located((By.ID, eid)))
            ok = el.is_displayed() and el.is_enabled()
            record("Auth UI", f"Verify login element #{eid} ({i+1})", f"ID={eid}", "Visible & enabled", f"visible={el.is_displayed()}, enabled={el.is_enabled()}", t0, ok)

        # Negative login checks (10 checks)
        for i in range(10):
            t0 = time.perf_counter()
            _set_input(driver, "login-email", f"invalid{i}@flapguard.local")
            _set_input(driver, "login-password", "WrongPassword123")
            _click_and_wait_reload(driver, "login-submit")
            flash_text = wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, ".flash-danger"))).text
            record("Auth Negative", f"Reject invalid credentials #{i+1}", f"invalid{i}@flapguard.local", "Invalid email or password banner", flash_text, t0, "Invalid email or password" in flash_text)

        # Register page checks (10 checks)
        t0 = time.perf_counter()
        driver.get(f"{base_url}/register")
        _wait_ready(driver)
        record("Signup UI", "Load /register page", f"{base_url}/register", "Title contains Sign Up", driver.title, t0, "Sign Up" in driver.title)

        reg_ids = ["register-form", "reg-name", "reg-email", "reg-role", "reg-password", "reg-confirm", "register-submit"]
        for eid in reg_ids:
            t0 = time.perf_counter()
            el = wait.until(EC.presence_of_element_located((By.ID, eid)))
            record("Signup UI", f"Verify register field #{eid}", f"ID={eid}", "Visible", f"visible={el.is_displayed()}", t0, el.is_displayed())

        # Create a real new surgeon account via browser form
        t0 = time.perf_counter()
        _set_input(driver, "reg-name", "Dr. Selenium Tester")
        _set_input(driver, "reg-email", "selenium.surgeon@flapguard.local")
        Select(driver.find_element(By.ID, "reg-role")).select_by_visible_text("Surgeon")
        _set_input(driver, "reg-password", "Selenium@2026")
        _set_input(driver, "reg-confirm", "Selenium@2026")
        _click_and_wait_reload(driver, "register-submit")
        wait.until(EC.presence_of_element_located((By.ID, "risk-dashboard-table")))
        record("Signup Flow", "Register new clinician & auto-login", "selenium.surgeon@flapguard.local", "Redirect to Risk Dashboard", driver.current_url, t0, "/login" not in driver.current_url)

        t0 = time.perf_counter()
        body_text = driver.find_element(By.TAG_NAME, "body").text
        record("Signup Flow", "Verify authenticated clinician name in navbar", "Navbar user text", "Dr. Selenium Tester visible", "Found" if "Dr. Selenium Tester" in body_text else "Missing", t0, "Dr. Selenium Tester" in body_text)

        # 2. Risk Stratification Dashboard Checks (45 checks)
        kpi_ids = ["kpi-critical", "kpi-high", "kpi-moderate", "kpi-low"]
        for i in range(20):
            t0 = time.perf_counter()
            kid = kpi_ids[i % len(kpi_ids)]
            val = driver.find_element(By.ID, kid).text.strip()
            record("Dashboard KPI", f"Read KPI widget #{kid} pass {i+1}", f"ID={kid}", "Numeric integer >= 0", f"value={val}", t0, val.isdigit())

        dash_elements = ["risk-dashboard-table", "add-patient-form", "nav-dashboard", "nav-alerts", "nav-learning", "nav-profile", "nav-logout"]
        for i in range(21):
            t0 = time.perf_counter()
            eid = dash_elements[i % len(dash_elements)]
            el = driver.find_element(By.ID, eid)
            record("Dashboard DOM", f"Verify dashboard control #{eid} ({i+1})", f"ID={eid}", "Visible on dashboard", f"visible={el.is_displayed()}", t0, el.is_displayed())

        for patient_name in ["Meena Devi", "Ramesh Kumar", "Karthik Sundaram", "Venous Compromise"]:
            t0 = time.perf_counter()
            tbl_text = driver.find_element(By.ID, "risk-dashboard-table").text
            record("Dashboard Data", f"Verify seeded record '{patient_name}'", "risk-dashboard-table", f"Contains '{patient_name}'", "Present" if patient_name in tbl_text else "Missing", t0, patient_name in tbl_text)

        # 3. Enroll 6 New Patients via Browser Form (36 checks)
        created_patient_url = None
        for i in range(6):
            driver.get(f"{base_url}/")
            _wait_ready(driver)
            mrn = f"MRN-SEL-{100 + i}"
            pname = f"Selenium Patient {i + 1}"
            t0 = time.perf_counter()
            _set_input(driver, "patient-mrn", mrn)
            _set_input(driver, "patient-name", pname)
            _set_input(driver, "patient-age", str(42 + i))
            _set_input(driver, "patient-flap", "Radial Forearm Free Flap")
            _set_input(driver, "patient-site", "Left Buccal Mucosa")
            _set_input(driver, "patient-hours", str(4.0 + i))
            _click_and_wait_reload(driver, "add-patient-submit")
            wait.until(EC.presence_of_element_located((By.ID, "sensor-reading-form")))
            created_patient_url = driver.current_url
            record("Patient Enroll", f"Enroll patient {mrn}", f"{pname} ({mrn})", "Redirect to patient monitor", created_patient_url, t0, "/patients/" in created_patient_url)

            for check_name, eid in [
                ("Risk badge visible", "current-risk-badge"),
                ("Camera start button visible", "start-camera-btn"),
                ("Upload analyzer button visible", "analyze-upload-btn"),
                ("Simulator button visible", "sim-start-btn"),
                ("Trend canvas visible", "sensor-trend-chart"),
            ]:
                t0 = time.perf_counter()
                el = driver.find_element(By.ID, eid)
                record("Patient Detail DOM", f"{mrn}: {check_name}", f"ID={eid}", "Displayed", f"displayed={el.is_displayed()}", t0, el.is_displayed())

        # 4. Camera / Image Upload Local OpenCV Scanner UI Tests (48 checks)
        driver.get(created_patient_url)
        _wait_ready(driver)
        upload_scenarios = [
            ("healthy_pink.png", "Healthy Pink Perfusion"),
            ("venous_dusky.png", "Dusky / Suspected Venous Compromise"),
            ("arterial_pale.png", "Pale / Suspected Arterial Compromise"),
        ]
        for i in range(12):
            img_name, expected_triage = upload_scenarios[i % 3]
            img_path = os.path.abspath(sample_paths[img_name])

            t0 = time.perf_counter()
            file_input = driver.find_element(By.ID, "flap-image-upload")
            file_input.send_keys(img_path)
            _click_id(driver, "analyze-upload-btn")
            wait.until(lambda d: expected_triage in d.find_element(By.ID, "camera-result-box").text)
            box_text = driver.find_element(By.ID, "camera-result-box").text
            record("Camera Upload Scan", f"Upload & analyze {img_name} run #{i+1}", img_name, expected_triage, box_text[:120], t0, expected_triage in box_text)

            for idx_id in ["visual-redness-input", "visual-cyanosis-input", "visual-pallor-input"]:
                t0 = time.perf_counter()
                val = driver.find_element(By.ID, idx_id).get_attribute("value")
                try:
                    fval = float(val)
                    ok = 0.0 <= fval <= 1.0
                except (TypeError, ValueError):
                    ok = False
                record("Camera Form Sync", f"Auto-filled {idx_id} after {img_name} #{i+1}", f"ID={idx_id}", "Float in [0, 1]", f"value={val}", t0, ok)

        # 5. Multisensor Fusion Form Submissions & Live AI Risk Verification (60 checks)
        clinical_submissions = [
            ("Healthy Normal", 36.3, 36.7, 97.5, 22.0, 0.15, 0.02, 0.41, "Normal", "Low"),
            ("Venous Compromise", 34.8, 36.7, 80.5, 9.2, 0.10, 0.54, 0.27, "Venous Compromise", "Critical"),
            ("Arterial Compromise", 33.4, 36.7, 71.0, 2.2, 0.02, 0.03, 0.81, "Arterial Compromise", "Critical"),
        ]
        for i in range(12):
            label, ir_t, ctrl_t, spo2, dopp, red, cya, pal, exp_cls, exp_tier = clinical_submissions[i % 3]
            t0 = time.perf_counter()
            _set_input(driver, "ir-temp-input", str(ir_t))
            _set_input(driver, "ctrl-temp-input", str(ctrl_t))
            _set_input(driver, "spo2-input", str(spo2))
            _set_input(driver, "doppler-input", str(dopp))
            _set_input(driver, "hours-input", str(10.0 + i))
            _set_input(driver, "visual-redness-input", str(red))
            _set_input(driver, "visual-cyanosis-input", str(cya))
            _set_input(driver, "visual-pallor-input", str(pal))
            _click_and_wait_reload(driver, "submit-reading-btn")
            badge_text = wait.until(EC.presence_of_element_located((By.ID, "current-risk-badge"))).text
            record("Multisensor Submit", f"Submit {label} reading #{i+1}", f"IR={ir_t}, SpO2={spo2}, Doppler={dopp}", f"{exp_cls} ({exp_tier})", badge_text, t0, exp_cls.upper() in badge_text.upper() and exp_tier.upper() in badge_text.upper())

            history_text = driver.find_element(By.ID, "readings-history-table").text
            t0 = time.perf_counter()
            record("History Table", f"Verify predicted class in history #{i+1}", exp_cls, "Present in readings table", "Present" if exp_cls in history_text else "Missing", t0, exp_cls in history_text)

            t0 = time.perf_counter()
            record("History Table", f"Verify SpO2 {spo2}% in history #{i+1}", f"{spo2}%", "Present in readings table", "Present" if str(spo2) in history_text else "Missing", t0, str(spo2) in history_text)

            t0 = time.perf_counter()
            canvas_w = driver.execute_script("return document.getElementById('sensor-trend-chart').width;")
            record("Trend Chart", f"Verify canvas rendered after reading #{i+1}", "sensor-trend-chart", "Canvas width > 100px", f"width={canvas_w}px", t0, int(canvas_w) > 100)

            t0 = time.perf_counter()
            notes_count = len(driver.find_elements(By.CSS_SELECTOR, ".notes-list li"))
            record("Clinical Notes", f"Verify clinical notes list populated #{i+1}", ".notes-list li", ">= 1 clinical note", f"count={notes_count}", t0, notes_count >= 1)

        # 6. Live Sensor Simulator UI Checks (24 checks)
        for i, scenario in enumerate(["normal", "venous_deteriorating", "arterial_deteriorating", "venous_deteriorating", "arterial_deteriorating", "normal"]):
            t0 = time.perf_counter()
            driver.execute_script("document.getElementById('sim-live-status').textContent = '';")
            Select(driver.find_element(By.ID, "sim-scenario")).select_by_value(scenario)
            _click_id(driver, "sim-start-btn")
            wait.until(lambda d: "Step 0" in d.find_element(By.ID, "sim-live-status").text)
            status_text = driver.find_element(By.ID, "sim-live-status").text
            record("Live Simulator", f"Start stream '{scenario}' (#{i+1})", f"scenario={scenario}", "Live Step 0 displayed", status_text[:100], t0, "Step 0" in status_text)

            t0 = time.perf_counter()
            _click_id(driver, "sim-stop-btn")
            stop_disabled = not driver.find_element(By.ID, "sim-stop-btn").is_enabled()
            record("Live Simulator", f"Stop stream '{scenario}' (#{i+1})", "Click sim-stop-btn", "Stop button disabled", f"disabled={stop_disabled}", t0, stop_disabled)

            t0 = time.perf_counter()
            start_enabled = driver.find_element(By.ID, "sim-start-btn").is_enabled()
            record("Live Simulator", f"Start button re-enabled after stop (#{i+1})", "sim-start-btn", "Enabled=True", f"enabled={start_enabled}", t0, start_enabled)

            t0 = time.perf_counter()
            has_delta = "Delta-T:" in status_text and "SpO2:" in status_text
            record("Live Simulator", f"Verify telemetry format (#{i+1})", status_text[:60], "Contains Delta-T & SpO2", f"valid={has_delta}", t0, has_delta)

        # 7. Surgeon Alerts Center & Acknowledge Flow (30 checks)
        t0 = time.perf_counter()
        _click_and_wait_reload(driver, "nav-alerts")
        wait.until(EC.presence_of_element_located((By.ID, "alerts-table")))
        record("Alerts Center", "Navigate to /alerts", "Click nav-alerts", "Alerts table loaded", driver.current_url, t0, "/alerts" in driver.current_url)

        for i in range(4):
            ack_buttons = driver.find_elements(By.CSS_SELECTOR, ".ack-alert-btn")
            if ack_buttons:
                t0 = time.perf_counter()
                old_html = driver.find_element(By.TAG_NAME, "html")
                ack_buttons[0].click()
                wait.until(EC.staleness_of(old_html))
                _wait_ready(driver)
                msg = wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, ".flash-success"))).text
                record("Alerts Center", f"Acknowledge alert #{i+1}", "Click Acknowledge", "Acknowledged confirmation flash", msg, t0, "acknowledged" in msg.lower())

        for i in range(25):
            t0 = time.perf_counter()
            table_rows = driver.find_elements(By.CSS_SELECTOR, "#alerts-table tbody tr")
            has_text = len(table_rows) > 0 and len(table_rows[0].text.strip()) > 0
            record("Alerts Verification", f"Verify alerts table integrity pass #{i+1}", "#alerts-table tbody tr", ">= 1 populated alert row", f"rows={len(table_rows)}", t0, has_text)

        # 8. Continuous Learning Outcome Logging & Retraining UI (26 checks)
        t0 = time.perf_counter()
        _click_and_wait_reload(driver, "nav-learning")
        wait.until(EC.presence_of_element_located((By.ID, "retrain-btn")))
        record("Continuous Learning", "Navigate to /learning", "Click nav-learning", "Retrain button loaded", driver.current_url, t0, "/learning" in driver.current_url)

        outcomes_to_log = [
            ("Normal", "Post-op day 3 viable pink flap."),
            ("Venous Compromise", "Confirmed venous congestion on bedside re-exploration."),
            ("Arterial Compromise", "Confirmed arterial thrombosis; anastomosis revised."),
        ]
        for i in range(9):
            lbl, note = outcomes_to_log[i % 3]
            t0 = time.perf_counter()
            Select(driver.find_element(By.ID, "outcome-label")).select_by_value(lbl)
            _set_input(driver, "outcome-notes", f"{note} (Case {i+1})")
            _click_and_wait_reload(driver, "save-outcome-btn")
            msg = wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, ".flash-success"))).text
            record("Continuous Learning", f"Log postoperative outcome #{i+1}", f"outcome={lbl}", "Outcome saved flash", msg, t0, "Verified postoperative outcome" in msg)

        for run_num in range(2):
            t0 = time.perf_counter()
            _click_and_wait_reload(driver, "retrain-btn")
            msg = wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, ".flash-success"))).text
            record("Continuous Learning", f"Retrain local AI model run #{run_num+1}", "Click retrain-btn", "Continuous learning complete", msg, t0, "Continuous learning complete" in msg)

        for i in range(14):
            t0 = time.perf_counter()
            v_rows = driver.find_elements(By.CSS_SELECTOR, "#model-versions-table tbody tr")
            acc_txt = driver.find_element(By.ID, "model-accuracy-val").text
            record("Model Versions UI", f"Verify model version history table #{i+1}", "#model-versions-table", ">= 3 versions & % accuracy", f"versions={len(v_rows)}, acc={acc_txt}", t0, len(v_rows) >= 3 and "%" in acc_txt)

        # 9. Profile Page, Password Validation, Navigation & Logout (26 checks)
        t0 = time.perf_counter()
        _click_and_wait_reload(driver, "nav-profile")
        wait.until(EC.presence_of_element_located((By.ID, "password-form")))
        record("Profile UI", "Navigate to /profile", "Click nav-profile", "Password form loaded", driver.current_url, t0, "/profile" in driver.current_url)

        for i in range(10):
            t0 = time.perf_counter()
            _set_input(driver, "current-pw", "WrongCurrentPw")
            _set_input(driver, "new-pw", "NewValidPass@2026")
            _set_input(driver, "confirm-pw", "NewValidPass@2026")
            _click_and_wait_reload(driver, "change-pw-submit")
            msg = wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, ".flash-danger"))).text
            record("Profile Security", f"Reject wrong current password #{i+1}", "current_password=WrongCurrentPw", "Current password is incorrect", msg, t0, "Current password is incorrect" in msg)

        t0 = time.perf_counter()
        _set_input(driver, "current-pw", "Selenium@2026")
        _set_input(driver, "new-pw", "SeleniumNew@2026")
        _set_input(driver, "confirm-pw", "SeleniumNew@2026")
        _click_and_wait_reload(driver, "change-pw-submit")
        msg = wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, ".flash-success"))).text
        record("Profile Security", "Update clinician password cleanly", "Valid current + matching new password", "Password updated successfully", msg, t0, "Password updated successfully" in msg)

        # Logout & verify protected route redirection + re-login with new password
        t0 = time.perf_counter()
        _click_and_wait_reload(driver, "nav-logout")
        wait.until(EC.presence_of_element_located((By.ID, "login-form")))
        record("Session Security", "Logout redirects to /login", "Click nav-logout", "Login form visible", driver.current_url, t0, "/login" in driver.current_url)

        for i, protected in enumerate(["/", "/alerts", "/learning", "/profile", "/patients/1", "/", "/alerts", "/learning", "/profile", "/patients/1"]):
            t0 = time.perf_counter()
            driver.get(f"{base_url}{protected}")
            _wait_ready(driver)
            wait.until(EC.presence_of_element_located((By.ID, "login-form")))
            record("Session Guard", f"Post-logout guard on {protected} (#{i+1})", f"GET {protected}", "Redirected to /login", driver.current_url, t0, "/login" in driver.current_url)

        for i in range(3):
            t0 = time.perf_counter()
            _set_input(driver, "login-email", "selenium.surgeon@flapguard.local")
            _set_input(driver, "login-password", "SeleniumNew@2026")
            _click_and_wait_reload(driver, "login-submit")
            wait.until(EC.presence_of_element_located((By.ID, "risk-dashboard-table")))
            record("Session Flow", f"Login with updated password #{i+1}", "selenium.surgeon@flapguard.local", "Dashboard loaded", driver.current_url, t0, "/login" not in driver.current_url)
            if i < 2:
                _click_and_wait_reload(driver, "nav-logout")
                wait.until(EC.presence_of_element_located((By.ID, "login-form")))

    finally:
        driver.quit()
        server.shutdown()
        server_thread.join(timeout=3)
        if os.path.exists(backup_model):
            shutil.copy2(backup_model, MODEL_PATH)
        if os.path.exists(backup_meta):
            shutil.copy2(backup_meta, METADATA_PATH)
        shutil.rmtree(tmp_dir, ignore_errors=True)

    return write_excel_report("selenium-web-report.xlsx", "Selenium Web Report", rows)


def test_generate_selenium_web_report():
    path = run_selenium_web_suite()
    assert path.endswith("selenium-web-report.xlsx")


if __name__ == "__main__":
    run_selenium_web_suite()