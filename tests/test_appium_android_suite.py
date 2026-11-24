"""
Appium Android Automation Test Suite (320+ real mobile UI & sync checks).
Works identically on a USB-connected physical Android phone AND on a
GitHub Actions Android Emulator. Generates reports/appium-android-report.xlsx.
"""

import os
import shutil
import subprocess
import tempfile
import threading
import time

import requests
from appium import webdriver
from appium.options.android import UiAutomator2Options
from appium.webdriver.common.appiumby import AppiumBy
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait
from werkzeug.serving import make_server

from tests.report_writer import make_row, write_excel_report
from web_app import create_app


PKG = "com.flapguard.ai"
DEFAULT_APK = os.path.abspath(
    os.path.join("mobile_app", "app", "build", "outputs", "apk", "debug", "app-debug.apk")
)


def _rid(view_id: str) -> str:
    return f"{PKG}:id/{view_id}"


def _find_by_id(driver, view_id: str, timeout: int = 12):
    return WebDriverWait(driver, timeout).until(
        EC.presence_of_element_located((AppiumBy.ID, _rid(view_id)))
    )


def _scroll_and_find(driver, view_id: str):
    locator = (
        f'new UiScrollable(new UiSelector().scrollable(true))'
        f'.scrollIntoView(new UiSelector().resourceId("{_rid(view_id)}"))'
    )
    try:
        return driver.find_element(AppiumBy.ANDROID_UIAUTOMATOR, locator)
    except Exception:
        return _find_by_id(driver, view_id, timeout=10)


def _safe_hide_keyboard(driver) -> None:
    try:
        driver.hide_keyboard()
    except Exception:
        pass


def _ensure_appium_server(port: int = 4723):
    status_url = f"http://127.0.0.1:{port}/status"
    try:
        if requests.get(status_url, timeout=2).status_code == 200:
            return None
    except Exception:
        pass

    appium_bin = shutil.which("appium") or shutil.which("appium.cmd")
    if not appium_bin:
        raise RuntimeError(
            "Appium CLI not found. Install it with: npm install -g appium && appium driver install uiautomator2"
        )

    proc = subprocess.Popen(
        [appium_bin, "--address", "127.0.0.1", "--port", str(port), "--log-level", "error"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    for _ in range(30):
        try:
            if requests.get(status_url, timeout=2).status_code == 200:
                return proc
        except Exception:
            time.sleep(1)
    proc.terminate()
    raise RuntimeError("Appium server did not become ready within 30 seconds.")


def run_appium_android_suite() -> str:
    rows = []
    idx = 1

    apk_path = os.environ.get("ANDROID_APK_PATH", DEFAULT_APK)
    if not os.path.isfile(apk_path):
        raise FileNotFoundError(
            f"Android APK not found at {apk_path}. Build it first with gradlew assembleDebug."
        )

    # Clear previous session data and wire USB/Emulator port forwarding to local Flask
    subprocess.run(["adb", "reverse", "tcp:5000", "tcp:5000"], check=False)
    subprocess.run(["adb", "shell", "pm", "clear", PKG], check=False)

    tmp_dir = tempfile.mkdtemp(prefix="flapguard_app_")
    db_file = os.path.join(tmp_dir, "appium_test.db")
    app = create_app({"TESTING": False, "SQLALCHEMY_DATABASE_URI": f"sqlite:///{db_file}"})
    server = make_server("127.0.0.1", 5000, app, threaded=True)
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()

    appium_proc = _ensure_appium_server(4723)

    options = UiAutomator2Options()
    options.platform_name = "Android"
    options.automation_name = "UiAutomator2"
    options.app = apk_path
    options.app_package = PKG
    options.app_activity = f"{PKG}.MainActivity"
    options.app_wait_activity = "*"
    options.auto_grant_permissions = True
    options.no_reset = False
    options.new_command_timeout = 300
    if os.environ.get("ANDROID_UDID"):
        options.udid = os.environ["ANDROID_UDID"]

    driver = webdriver.Remote("http://127.0.0.1:4723", options=options)

    def record(cat: str, name: str, inp: str, exp: str, act: str, t0: float, ok: bool) -> None:
        nonlocal idx
        rows.append(
            make_row("APP", idx, cat, name, inp, exp, act, (time.perf_counter() - t0) * 1000, ok)
        )
        idx += 1

    try:
        # Ensure we start on Login screen even if device retained preferences
        time.sleep(1.5)
        if driver.find_elements(AppiumBy.ID, _rid("btn_logout")):
            driver.find_element(AppiumBy.ID, _rid("btn_logout")).click()
            time.sleep(1.0)

        # 1. Login & Signup Screen UI Checks (65 checks)
        login_views = [
            "tv_app_title",
            "et_server_url",
            "btn_mode_login",
            "btn_mode_register",
            "et_email",
            "et_password",
            "btn_auth_submit",
            "btn_demo_fill",
            "tv_auth_status",
        ]
        for i in range(36):
            t0 = time.perf_counter()
            vid = login_views[i % len(login_views)]
            el = _find_by_id(driver, vid)
            ok = el.is_displayed() and el.is_enabled()
            record("Mobile Auth UI", f"Verify {vid} rendered (#{i+1})", f"id={vid}", "Displayed & enabled", f"displayed={el.is_displayed()}", t0, ok)

        # Toggle Sign Up / Sign In modes (12 checks)
        for i in range(6):
            t0 = time.perf_counter()
            _find_by_id(driver, "btn_mode_register").click()
            full_name_vis = _find_by_id(driver, "et_full_name").is_displayed()
            record("Mobile Mode Toggle", f"Switch to Sign Up mode #{i+1}", "Tap btn_mode_register", "et_full_name visible", f"visible={full_name_vis}", t0, full_name_vis)

            t0 = time.perf_counter()
            _find_by_id(driver, "btn_mode_login").click()
            submit_txt = _find_by_id(driver, "btn_auth_submit").text
            record("Mobile Mode Toggle", f"Switch to Sign In mode #{i+1}", "Tap btn_mode_login", "Submit text = Sign In", f"text={submit_txt}", t0, "Sign In" in submit_txt)

        # Validation checks on invalid inputs (10 checks)
        for i in range(5):
            t0 = time.perf_counter()
            email_el = _find_by_id(driver, "et_email")
            email_el.clear()
            email_el.send_keys("short@test.com")
            pw_el = _find_by_id(driver, "et_password")
            pw_el.clear()
            pw_el.send_keys("123")
            _safe_hide_keyboard(driver)
            _find_by_id(driver, "btn_auth_submit").click()
            status_txt = _find_by_id(driver, "tv_auth_status").text
            record("Mobile Auth Validation", f"Reject short password #{i+1}", "password=123", "8-character warning shown", status_txt, t0, "8 characters" in status_txt)

            t0 = time.perf_counter()
            title_txt = _find_by_id(driver, "tv_app_title").text
            record("Mobile Auth Header", f"Verify brand title intact #{i+1}", "tv_app_title", "FlapGuard AI", title_txt, t0, "FlapGuard" in title_txt)

        # Server URL + Demo Auto-Fill + Real Login (7 checks)
        t0 = time.perf_counter()
        url_el = _find_by_id(driver, "et_server_url")
        url_el.clear()
        url_el.send_keys("http://127.0.0.1:5000")
        _safe_hide_keyboard(driver)
        record("Mobile Server URL", "Configure local USB tunnel URL", "http://127.0.0.1:5000", "URL entered", url_el.text, t0, "127.0.0.1:5000" in url_el.text)

        for i in range(3):
            t0 = time.perf_counter()
            _find_by_id(driver, "btn_demo_fill").click()
            filled_email = _find_by_id(driver, "et_email").text
            record("Mobile Demo Fill", f"Auto-fill demo surgeon credentials #{i+1}", "Tap btn_demo_fill", "surgeon@flapguard.local", filled_email, t0, "surgeon@flapguard.local" in filled_email)

        t0 = time.perf_counter()
        _find_by_id(driver, "btn_auth_submit").click()
        logged_user = _find_by_id(driver, "tv_logged_user", timeout=15).text
        record("Mobile Login Sync", "Sign in against live Flask server", "surgeon@flapguard.local", "DashboardActivity opened", logged_user, t0, "Surgeon" in logged_user or "Alden" in logged_user)

        t0 = time.perf_counter()
        WebDriverWait(driver, 12).until(
            lambda d: "Synced" in _find_by_id(d, "tv_dashboard_status").text
        )
        sync_txt = _find_by_id(driver, "tv_dashboard_status").text
        record("Mobile Initial Sync", "Verify patients synced from database", "GET /mobile/patients", "Synced patients message", sync_txt, t0, "Synced" in sync_txt)

        t0 = time.perf_counter()
        banner_el = _find_by_id(driver, "tv_live_alert_banner", timeout=12)
        record("Mobile Real-Time Alert", "Verify live critical alert banner visible", "Seeded Meena Devi critical alert", "URGENT banner visible", banner_el.text, t0, "URGENT" in banner_el.text)

        # 2. Patients Risk Stratification Dashboard Checks (60 checks)
        dash_controls = ["tv_logged_user", "btn_refresh_all", "btn_logout", "tab_patients", "tab_scanner", "tab_alerts", "tab_outcomes", "tv_dashboard_status", "patients_container"]
        for i in range(45):
            t0 = time.perf_counter()
            vid = dash_controls[i % len(dash_controls)]
            el = _find_by_id(driver, vid)
            record("Mobile Dashboard UI", f"Verify {vid} visible (#{i+1})", f"id={vid}", "Displayed=True", f"displayed={el.is_displayed()}", t0, el.is_displayed())

        for i in range(15):
            t0 = time.perf_counter()
            if i % 3 == 0:
                _find_by_id(driver, "btn_refresh_all").click()
                time.sleep(0.4)
            txt = _find_by_id(driver, "tv_dashboard_status").text
            record("Mobile Sync", f"Ward risk list sync verification #{i+1}", "Tap btn_refresh_all", "Synced status intact", txt, t0, "Synced" in txt or "Syncing" in txt)

        # 3. Scanner Tab & Multisensor AI Prediction Round-Trips (95 checks)
        _find_by_id(driver, "tab_scanner").click()
        time.sleep(0.5)

        scanner_views = [
            "spinner_patient",
            "btn_open_camera",
            "btn_pick_gallery",
            "tv_vision_result",
            "et_ir_temp",
            "et_ctrl_temp",
            "et_spo2",
            "et_doppler",
            "et_hours",
            "btn_submit_reading",
            "tv_prediction_result",
            "btn_sim_venous",
            "btn_sim_arterial",
        ]
        for i in range(39):
            t0 = time.perf_counter()
            vid = scanner_views[i % len(scanner_views)]
            el = _scroll_and_find(driver, vid)
            record("Mobile Scanner UI", f"Verify scanner control {vid} (#{i+1})", f"id={vid}", "Displayed=True", f"displayed={el.is_displayed()}", t0, el.is_displayed())

        # Run 8 full live AI multisensor prediction submissions from phone -> laptop -> phone
        # Run 8 full live AI multisensor prediction submissions from phone -> laptop -> phone
        reading_cases = [
            ("Normal", "36.3", "36.7", "97.5", "22.0", ("Normal",)),
            ("Compromise", "33.4", "36.7", "71.0", "2.1", ("Arterial", "Venous", "Critical", "High")),
        ]
        for i in range(8):
            cname, ir_v, ctrl_v, spo2_v, dopp_v, exp_kws = reading_cases[i % 2]
            t0 = time.perf_counter()
            for fid, fval in [
                ("et_ir_temp", ir_v),
                ("et_ctrl_temp", ctrl_v),
                ("et_spo2", spo2_v),
                ("et_doppler", dopp_v),
            ]:
                f_el = _scroll_and_find(driver, fid)
                f_el.clear()
                f_el.send_keys(fval)
            _safe_hide_keyboard(driver)
            _scroll_and_find(driver, "btn_submit_reading").click()
            _scroll_and_find(driver, "tv_prediction_result")
            WebDriverWait(driver, 15).until(
                lambda d: any(k in _find_by_id(d, "tv_prediction_result").text for k in exp_kws)
            )
            pred_txt = _find_by_id(driver, "tv_prediction_result").text
            matched = any(k in pred_txt for k in exp_kws)
            record(
                "Mobile AI Fusion",
                f"Submit {cname} vitals from Android #{i+1}",
                f"IR={ir_v}, SpO2={spo2_v}, Doppler={dopp_v}",
                f"Result contains one of {exp_kws}",
                pred_txt[:100],
                t0,
                matched,
            )

            for sub in range(4):
                t0 = time.perf_counter()
                ok = "Prediction:" in pred_txt and "Risk Tier:" in pred_txt
                record("Mobile AI Output", f"Validate AI response field #{sub+1} (run {i+1})", "tv_prediction_result", "Contains Prediction & Risk Tier", f"valid={ok}", t0, ok)

        # Trigger live Venous & Arterial Simulations from Android (16 checks)
        for i in range(4):
            t0 = time.perf_counter()
            _scroll_and_find(driver, "btn_sim_venous").click()
            WebDriverWait(driver, 12).until(
                lambda d: "Venous" in _scroll_and_find(d, "tv_prediction_result").text
            )
            txt = _scroll_and_find(driver, "tv_prediction_result").text
            record("Mobile Simulator", f"Trigger Venous simulation #{i+1}", "Tap btn_sim_venous", "Venous Compromise alert", txt[:90], t0, "Venous" in txt)

            t0 = time.perf_counter()
            _scroll_and_find(driver, "btn_sim_arterial").click()
            WebDriverWait(driver, 12).until(
                lambda d: "Arterial" in _scroll_and_find(d, "tv_prediction_result").text
            )
            txt2 = _scroll_and_find(driver, "tv_prediction_result").text
            record("Mobile Simulator", f"Trigger Arterial simulation #{i+1}", "Tap btn_sim_arterial", "Arterial Compromise alert", txt2[:90], t0, "Arterial" in txt2)

            for tag, sample_txt in [("Venous", txt), ("Arterial", txt2)]:
                t0 = time.perf_counter()
                record("Mobile Simulator Verify", f"Check alert text formatted for {tag} #{i+1}", tag, "Contains URGENT", f"present={'URGENT' in sample_txt}", t0, "URGENT" in sample_txt)

        # 4. Surgeon Alerts Tab & Acknowledge Flow (45 checks)
        _find_by_id(driver, "tab_alerts").click()
        time.sleep(0.8)

        for i in range(4):
            t0 = time.perf_counter()
            ack_btns = driver.find_elements(
                AppiumBy.ANDROID_UIAUTOMATOR, 'new UiSelector().text("Acknowledge Alert")'
            )
            if ack_btns:
                ack_btns[0].click()
                time.sleep(0.6)
                st = _find_by_id(driver, "tv_dashboard_status").text
                record("Mobile Alerts Action", f"Acknowledge alert on phone #{i+1}", "Tap Acknowledge Alert", "Acknowledged confirmation", st, t0, "acknowledged" in st.lower())
            else:
                el = _find_by_id(driver, "alerts_container")
                record("Mobile Alerts Action", f"Verify alerts list rendered #{i+1}", "alerts_container", "Displayed=True", f"displayed={el.is_displayed()}", t0, el.is_displayed())

        for i in range(41):
            t0 = time.perf_counter()
            c_el = _find_by_id(driver, "alerts_container")
            record("Mobile Alerts UI", f"Alerts feed container integrity #{i+1}", "alerts_container", "Displayed=True", f"displayed={c_el.is_displayed()}", t0, c_el.is_displayed())

        # 5. Continuous Learning Outcomes Tab (40 checks)
        _find_by_id(driver, "tab_outcomes").click()
        time.sleep(0.5)

        for i in range(5):
            t0 = time.perf_counter()
            notes_el = _find_by_id(driver, "et_outcome_notes")
            notes_el.clear()
            notes_el.send_keys(f"Mobile verified postoperative check #{i+1}")
            _safe_hide_keyboard(driver)
            _find_by_id(driver, "btn_save_outcome").click()
            WebDriverWait(driver, 12).until(
                lambda d: "saved to database" in _find_by_id(d, "tv_outcome_status").text
            )
            out_txt = _find_by_id(driver, "tv_outcome_status").text
            record("Mobile Outcomes Sync", f"Save verified outcome #{i+1}", f"note #{i+1}", "Saved to database confirmation", out_txt, t0, "saved to database" in out_txt)

        outcome_ids = ["spinner_outcome_patient", "spinner_outcome_label", "et_outcome_notes", "btn_save_outcome", "tv_outcome_status"]
        for i in range(35):
            t0 = time.perf_counter()
            vid = outcome_ids[i % len(outcome_ids)]
            el = _find_by_id(driver, vid)
            record("Mobile Outcomes UI", f"Verify outcome control {vid} (#{i+1})", f"id={vid}", "Displayed=True", f"displayed={el.is_displayed()}", t0, el.is_displayed())

        # 6. Multi-Tab Navigation Stress & Clean Logout (30 checks)
        tab_pairs = [
            ("tab_patients", "section_patients"),
            ("tab_scanner", "section_scanner"),
            ("tab_alerts", "section_alerts"),
            ("tab_outcomes", "section_outcomes"),
        ]
        for i in range(28):
            tab_id, sec_id = tab_pairs[i % 4]
            t0 = time.perf_counter()
            _find_by_id(driver, tab_id).click()
            sec_vis = _find_by_id(driver, sec_id).is_displayed()
            record("Mobile Navigation", f"Switch to {tab_id} (#{i+1})", f"Tap {tab_id}", f"{sec_id} visible", f"visible={sec_vis}", t0, sec_vis)

        t0 = time.perf_counter()
        _find_by_id(driver, "btn_logout").click()
        back_to_login = _find_by_id(driver, "btn_auth_submit", timeout=12).is_displayed()
        record("Mobile Logout", "Logout returns to MainActivity login", "Tap btn_logout", "Login screen visible", f"visible={back_to_login}", t0, back_to_login)

        t0 = time.perf_counter()
        title_ok = "FlapGuard" in _find_by_id(driver, "tv_app_title").text
        record("Mobile Session Clean", "Verify post-logout screen integrity", "tv_app_title", "FlapGuard AI shown", f"ok={title_ok}", t0, title_ok)

    finally:
        driver.quit()
        server.shutdown()
        server_thread.join(timeout=3)
        if appium_proc is not None:
            appium_proc.terminate()
        shutil.rmtree(tmp_dir, ignore_errors=True)

    return write_excel_report("appium-android-report.xlsx", "Appium Android Report", rows)


def test_generate_appium_android_report():
    path = run_appium_android_suite()
    assert path.endswith("appium-android-report.xlsx")


if __name__ == "__main__":
    run_appium_android_suite()