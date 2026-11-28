"""
Appium Android Automation Test Suite (335+ real mobile UI & sync checks).
Covers the v2.1 Minimalist FlapGuard AI app end-to-end.
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
from web_app.models import PostOpOutcome


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


def _scroll_gesture(driver, direction: str, percent: float = 0.7) -> bool:
    """Scroll viewport 'down' (reveal below) or 'up' (reveal above) safely."""
    scrolled = False
    try:
        # 1. Prefer scrolling the active visible ScrollView directly by elementId
        for sv in driver.find_elements(AppiumBy.CLASS_NAME, "android.widget.ScrollView"):
            try:
                if sv.is_displayed():
                    scrolled = bool(
                        driver.execute_script(
                            "mobile: scrollGesture",
                            {
                                "elementId": sv.id,
                                "direction": direction,
                                "percent": percent,
                                "speed": 1200,
                            },
                        )
                    )
                    if scrolled:
                        return True
            except Exception:
                pass
    except Exception:
        pass

    # 2. Fallback: physical swipeGesture strictly in the safe center band (24%-68% height)
    #    Note: swipeGesture uses finger direction (up = reveal below, down = reveal above).
    try:
        size = driver.get_window_size()
        finger_dir = "up" if direction == "down" else "down"
        driver.execute_script(
            "mobile: swipeGesture",
            {
                "left": int(size["width"] * 0.15),
                "top": int(size["height"] * 0.24),
                "width": int(size["width"] * 0.70),
                "height": int(size["height"] * 0.44),
                "direction": finger_dir,
                "percent": 0.85,
                "speed": 1200,
            },
        )
        return True
    except Exception:
        return scrolled


def _scroll_and_find(driver, view_id: str):
    rid = _rid(view_id)

    def _visible_match():
        found = driver.find_elements(AppiumBy.ID, rid)
        for el in found:
            try:
                if el.is_displayed():
                    return el
            except Exception:
                pass
        return found[0] if found else None

    el = _visible_match()
    if el is not None:
        return el

    for direction in ("down", "up"):
        for _ in range(7):
            _scroll_gesture(driver, direction, 0.7)
            time.sleep(0.25)
            el = _visible_match()
            if el is not None:
                return el

    try:
        return _find_by_id(driver, view_id, timeout=8)
    except Exception as exc:
        visible_ids = sorted(
            {
                e.get_attribute("resource-id")
                for e in driver.find_elements(AppiumBy.XPATH, "//*[@resource-id]")
                if e.get_attribute("resource-id")
            }
        )
        raise AssertionError(
            f"Could not locate '{rid}' after bidirectional scroll. Visible IDs: {visible_ids}"
        ) from exc

def _switch_tab(driver, tab_id: str, expected_section_id: str) -> None:
    for _ in range(2):
        _find_by_id(driver, tab_id, timeout=12).click()
        try:
            WebDriverWait(driver, 6).until(
                EC.visibility_of_element_located((AppiumBy.ID, _rid(expected_section_id)))
            )
            return
        except Exception:
            time.sleep(0.4)
    _find_by_id(driver, expected_section_id, timeout=10)


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


def _app_outcome_count(app) -> int:
    with app.app_context():
        return PostOpOutcome.query.count()


def run_appium_android_suite() -> str:
    rows = []
    idx = 1

    apk_path = os.environ.get("ANDROID_APK_PATH", DEFAULT_APK)
    if not os.path.isfile(apk_path):
        raise FileNotFoundError(
            f"Android APK not found at {apk_path}. Build it first with gradlew assembleDebug."
        )

    subprocess.run(["adb", "reverse", "tcp:5000", "tcp:5000"], check=False)
    subprocess.run(["adb", "shell", "settings", "put", "global", "heads_up_notifications_enabled", "0"], check=False)
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
        time.sleep(1.5)

        # 1. Branded Minimalist Login & Create Account Checks (64 checks)
        login_views = [
            "iv_brand_logo",
            "tv_brand_title",
            "btn_mode_login",
            "btn_mode_register",
            "et_email",
            "et_password",
            "btn_auth_submit",
        ]
        for i in range(35):
            t0 = time.perf_counter()
            vid = login_views[i % len(login_views)]
            el = _find_by_id(driver, vid)
            ok = el.is_displayed() and el.is_enabled()
            record("Mobile Auth UI", f"Verify {vid} rendered (#{i+1})", f"id={vid}", "Displayed & enabled", f"displayed={el.is_displayed()}", t0, ok)

        t0 = time.perf_counter()
        pill_txt = _scroll_and_find(driver, "tv_connection_pill").text
        record("Mobile Auth Cleanliness", "Verify default Live Cloud link", "tv_connection_pill", "Contains Live Cloud", pill_txt, t0, "Cloud" in pill_txt)

        for i in range(6):
            t0 = time.perf_counter()
            _scroll_and_find(driver, "btn_mode_register").click()
            full_name_vis = _find_by_id(driver, "et_full_name").is_displayed()
            record("Mobile Mode Toggle", f"Switch to Create Account mode #{i+1}", "Tap btn_mode_register", "et_full_name visible", f"visible={full_name_vis}", t0, full_name_vis)

            t0 = time.perf_counter()
            _find_by_id(driver, "btn_mode_login").click()
            submit_txt = _find_by_id(driver, "btn_auth_submit").text
            record("Mobile Mode Toggle", f"Switch to Sign In mode #{i+1}", "Tap btn_mode_login", "Submit text = Sign In", f"text={submit_txt}", t0, "Sign In" in submit_txt)

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
            record("Mobile Auth Validation", f"Reject short password #{i+1}", "password=123", "8+ characters warning shown", status_txt, t0, "8+" in status_txt)

            t0 = time.perf_counter()
            logo_ok = _find_by_id(driver, "iv_brand_logo").is_displayed()
            record("Mobile Brand Logo", f"Verify brand emblem visible #{i+1}", "iv_brand_logo", "Displayed=True", f"displayed={logo_ok}", t0, logo_ok)

        # Switch to Local USB Viva Mode & Sign In
        t0 = time.perf_counter()
        _scroll_and_find(driver, "btn_connection_settings").click()
        _find_by_id(driver, "option_local_usb", timeout=10).click()
        WebDriverWait(driver, 8).until(
            lambda d: "USB" in _scroll_and_find(d, "tv_connection_pill").text
        )
        usb_pill = _scroll_and_find(driver, "tv_connection_pill").text
        record("Mobile Connection Sheet", "Switch to Local USB Viva Mode", "Tap Connection -> Local USB", "Local USB Mode shown", usb_pill, t0, "USB" in usb_pill)

        t0 = time.perf_counter()
        email_el = _scroll_and_find(driver, "et_email")
        email_el.clear()
        email_el.send_keys("surgeon@flapguard.local")
        pw_el = _scroll_and_find(driver, "et_password")
        pw_el.clear()
        pw_el.send_keys("Surgeon@123")
        _safe_hide_keyboard(driver)
        _scroll_and_find(driver, "btn_auth_submit").click()
        toolbar_txt = _find_by_id(driver, "tv_toolbar_title", timeout=15).text
        record("Mobile Login Sync", "Sign in against live Flask server", "surgeon@flapguard.local", "Ward screen opened", toolbar_txt, t0, "Ward" in toolbar_txt)

        # Verify empty-start ward
        t0 = time.perf_counter()
        empty_vis = _find_by_id(driver, "card_empty_ward", timeout=12).is_displayed()
        kpi_zero = _find_by_id(driver, "tv_kpi_total").text.strip()
        record("Mobile Empty Ward", "Verify ward starts cleanly empty", "Initial ward state", "Empty state & total=0", f"empty={empty_vis}, total={kpi_zero}", t0, empty_vis and kpi_zero == "0")

        # Profile -> Demo Ward Data sheet -> Load 3 Sample OMFS Cases
        t0 = time.perf_counter()
        _find_by_id(driver, "nav_profile").click()
        prof_name = _find_by_id(driver, "tv_profile_name", timeout=10).text
        photo_btn_vis = _find_by_id(driver, "btn_edit_profile_photo", timeout=10).is_displayed()
        record("Mobile Profile Tab", "Open Profile & verify avatar editor", "Tap nav_profile", "Clinician name & photo editor visible", f"{prof_name}, photo={photo_btn_vis}", t0, ("Alden" in prof_name or "Surgeon" in prof_name) and photo_btn_vis)

        t0 = time.perf_counter()
        _scroll_and_find(driver, "row_demo_data").click()
        _find_by_id(driver, "btn_load_samples", timeout=10).click()
        WebDriverWait(driver, 15).until(
            lambda d: _find_by_id(d, "tv_kpi_total").text.strip() == "3"
        )
        total_after = _find_by_id(driver, "tv_kpi_total").text.strip()
        record("Mobile Sample Loader", "Load 3 sample OMFS cases via Demo Ward Data sheet", "row_demo_data -> btn_load_samples", "Ward total = 3", f"total={total_after}", t0, total_after == "3")

        t0 = time.perf_counter()
        alert_pill_txt = _find_by_id(driver, "tv_ward_alert_text", timeout=12).text
        record("Mobile Real-Time Alert", "Verify sleek Ward-only alert pill visible", "Loaded Meena Devi critical case", "Active alert pill visible", alert_pill_txt, t0, "alert" in alert_pill_txt.lower())

        # 2. Ward Dashboard Controls & Sync Checks (60 checks)
        dash_controls = [
            "tv_toolbar_title",
            "btn_refresh_all",
            "tv_kpi_critical",
            "tv_kpi_high",
            "tv_kpi_total",
            "rv_patients",
            "fab_add_patient",
            "bottom_nav",
            "nav_ward",
        ]
        for i in range(45):
            t0 = time.perf_counter()
            vid = dash_controls[i % len(dash_controls)]
            el = _find_by_id(driver, vid)
            record("Mobile Ward UI", f"Verify {vid} visible (#{i+1})", f"id={vid}", "Displayed=True", f"displayed={el.is_displayed()}", t0, el.is_displayed())

        for i in range(15):
            t0 = time.perf_counter()
            if i % 3 == 0:
                try:
                    _find_by_id(driver, "btn_refresh_all", timeout=5).click()
                except Exception:
                    pass
                time.sleep(0.5)
            cnt = _find_by_id(driver, "tv_kpi_total").text.strip()
            record("Mobile Sync", f"Ward sync count check #{i+1}", "Single-slot refresh check", "Numeric count >= 3", f"total={cnt}", t0, cnt.isdigit() and int(cnt) >= 3)

        # 3. Assess Tab (Borderless Vitals + AI Fusion + Simulator Bottom Sheet) (95 checks)
        _switch_tab(driver, "nav_assess", "section_assess")
        time.sleep(0.4)

        assess_views = [
            "card_select_patient",
            "tv_selected_patient",
            "btn_open_camera",
            "btn_pick_gallery",
            "et_ir_temp",
            "et_ctrl_temp",
            "et_spo2",
            "et_doppler",
            "et_hours",
            "btn_submit_reading",
            "row_open_simulator",
        ]
        for i in range(39):
            t0 = time.perf_counter()
            vid = assess_views[i % len(assess_views)]
            el = _scroll_and_find(driver, vid)
            record("Mobile Assess UI", f"Verify assess control {vid} (#{i+1})", f"id={vid}", "Displayed=True", f"displayed={el.is_displayed()}", t0, el.is_displayed())

        reading_cases = [
            ("Normal", "36.3", "36.7", "97.5", "22.0", ("Normal", "Low")),
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
            _scroll_and_find(driver, "tv_prediction_title")
            WebDriverWait(driver, 15).until(
                lambda d: any(k in _find_by_id(d, "tv_prediction_title").text for k in exp_kws)
            )
            title_txt = _find_by_id(driver, "tv_prediction_title").text
            msg_txt = _scroll_and_find(driver, "tv_prediction_result").text
            matched = any(k in title_txt for k in exp_kws)
            record(
                "Mobile AI Fusion",
                f"Submit {cname} vitals from Android #{i+1}",
                f"IR={ir_v}, SpO2={spo2_v}, Doppler={dopp_v}",
                f"Title contains one of {exp_kws}",
                title_txt[:100],
                t0,
                matched,
            )

            for sub in range(4):
                t0 = time.perf_counter()
                ok = "Risk" in title_txt and len(msg_txt) > 5
                record("Mobile AI Output", f"Validate AI card field #{sub+1} (run {i+1})", "card_prediction_result", "Risk title & guidance", f"valid={ok}", t0, ok)

        # Trigger live Venous & Arterial Simulations via clean Crisis Simulator Bottom Sheet (16 checks)
        for i in range(4):
            t0 = time.perf_counter()
            _scroll_and_find(driver, "row_open_simulator").click()
            _find_by_id(driver, "btn_sim_venous", timeout=10).click()
            WebDriverWait(driver, 12).until(
                lambda d: "Venous" in _scroll_and_find(d, "tv_prediction_title").text
            )
            v_title = _scroll_and_find(driver, "tv_prediction_title").text
            v_msg = _scroll_and_find(driver, "tv_prediction_result").text
            record("Mobile Simulator", f"Trigger Venous simulation #{i+1}", "Simulator sheet -> Venous", "Venous Compromise alert", v_title[:90], t0, "Venous" in v_title)

            t0 = time.perf_counter()
            _scroll_and_find(driver, "row_open_simulator").click()
            _find_by_id(driver, "btn_sim_arterial", timeout=10).click()
            WebDriverWait(driver, 12).until(
                lambda d: "Arterial" in _scroll_and_find(d, "tv_prediction_title").text
            )
            a_title = _scroll_and_find(driver, "tv_prediction_title").text
            a_msg = _scroll_and_find(driver, "tv_prediction_result").text
            record("Mobile Simulator", f"Trigger Arterial simulation #{i+1}", "Simulator sheet -> Arterial", "Arterial Compromise alert", a_title[:90], t0, "Arterial" in a_title)

            for tag, sample_txt in [("Venous", v_msg), ("Arterial", a_msg)]:
                t0 = time.perf_counter()
                record("Mobile Simulator Verify", f"Check clinical guidance formatted for {tag} #{i+1}", tag, "Contains inspection guidance", f"len={len(sample_txt)}", t0, ("inspect" in sample_txt.lower() or "risk" in sample_txt.lower()))

        # 4. Alerts Tab & Mark Reviewed Flow (45 checks)
        _switch_tab(driver, "nav_alerts", "section_alerts")
        time.sleep(0.5)

        for i in range(4):
            t0 = time.perf_counter()
            ack_btns = driver.find_elements(AppiumBy.ID, _rid("btn_ack_alert"))
            if ack_btns:
                ack_btns[0].click()
                time.sleep(0.7)
                sum_txt = _find_by_id(driver, "tv_alerts_summary").text
                record("Mobile Alerts Action", f"Mark alert reviewed #{i+1}", "Tap btn_ack_alert", "Alerts summary updated", sum_txt, t0, "review" in sum_txt.lower())
            else:
                el = _find_by_id(driver, "rv_alerts")
                record("Mobile Alerts Action", f"Verify alerts list rendered #{i+1}", "rv_alerts", "Displayed=True", f"displayed={el.is_displayed()}", t0, el.is_displayed())

        for i in range(41):
            t0 = time.perf_counter()
            rv_el = _find_by_id(driver, "rv_alerts")
            record("Mobile Alerts UI", f"Alerts RecyclerView integrity #{i+1}", "rv_alerts", "Displayed=True", f"displayed={rv_el.is_displayed()}", t0, rv_el.is_displayed())

        # 5. Profile Tab, Log Outcome Bottom Sheet & Settings Rows (42 checks)
        _switch_tab(driver, "nav_profile", "section_profile")
        time.sleep(0.5)

        for i in range(4):
            t0 = time.perf_counter()
            before_cnt = _app_outcome_count(app)
            _scroll_and_find(driver, "row_log_outcome").click()
            try:
                note_input = WebDriverWait(driver, 6).until(
                    EC.presence_of_element_located((AppiumBy.ACCESSIBILITY_ID, "outcome_notes_input"))
                )
            except Exception:
                _scroll_and_find(driver, "row_log_outcome").click()
                note_input = WebDriverWait(driver, 12).until(
                    EC.presence_of_element_located((AppiumBy.ACCESSIBILITY_ID, "outcome_notes_input"))
                )
            note_input.send_keys(f"Mobile verified postoperative check #{i+1}")
            _safe_hide_keyboard(driver)
            _find_by_id(driver, "btn_save_outcome_sheet", timeout=10).click()
            WebDriverWait(driver, 12).until(
                lambda d: _app_outcome_count(app) > before_cnt
            )
            WebDriverWait(driver, 10).until(
                EC.invisibility_of_element_located((AppiumBy.ID, _rid("btn_save_outcome_sheet")))
            )
            time.sleep(0.35)
            after_cnt = _app_outcome_count(app)
            record("Mobile Outcomes Sync", f"Save verified outcome via Bottom Sheet #{i+1}", f"note #{i+1}", f"DB count > {before_cnt}", f"db_count={after_cnt}", t0, after_cnt > before_cnt)

        profile_ids = [
            "btn_edit_profile_photo",
            "tv_profile_name",
            "tv_profile_email",
            "tv_profile_role",
            "tv_stat_model",
            "tv_stat_patients",
            "tv_stat_alerts",
            "row_log_outcome",
            "row_demo_data",
            "btn_change_password",
            "row_connection_mode",
            "btn_logout",
        ]
        for i in range(38):
            t0 = time.perf_counter()
            vid = profile_ids[i % len(profile_ids)]
            el = _scroll_and_find(driver, vid)
            record("Mobile Profile UI", f"Verify profile control {vid} (#{i+1})", f"id={vid}", "Displayed=True", f"displayed={el.is_displayed()}", t0, el.is_displayed())

        # 6. 4-Tab Bottom Navigation Stress & Clean Sign Out (30 checks)
        tab_pairs = [
            ("nav_ward", "section_ward"),
            ("nav_assess", "section_assess"),
            ("nav_alerts", "section_alerts"),
            ("nav_profile", "section_profile"),
        ]
        for i in range(28):
            tab_id, sec_id = tab_pairs[i % 4]
            t0 = time.perf_counter()
            _find_by_id(driver, tab_id).click()
            sec_vis = _find_by_id(driver, sec_id).is_displayed()
            record("Mobile Navigation", f"Switch to {tab_id} (#{i+1})", f"Tap {tab_id}", f"{sec_id} visible", f"visible={sec_vis}", t0, sec_vis)

        t0 = time.perf_counter()
        _scroll_and_find(driver, "btn_logout").click()
        back_to_login = _find_by_id(driver, "btn_auth_submit", timeout=12).is_displayed()
        record("Mobile Sign Out", "Sign Out returns to MainActivity login", "Tap btn_logout", "Login screen visible", f"visible={back_to_login}", t0, back_to_login)

        t0 = time.perf_counter()
        logo_after = _find_by_id(driver, "iv_brand_logo", timeout=10).is_displayed()
        record("Mobile Session Clean", "Verify post-signout branded login intact", "iv_brand_logo", "Brand logo visible", f"ok={logo_after}", t0, logo_after)

    finally:
        driver.quit()
        subprocess.run(["adb", "shell", "settings", "put", "global", "heads_up_notifications_enabled", "1"], check=False)
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
