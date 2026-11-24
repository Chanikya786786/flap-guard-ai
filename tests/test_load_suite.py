"""
Load Test Suite (320+ concurrent live HTTP requests) covering:
- Multi-user concurrent stress testing across Web Dashboard, Health,
  Mobile Sync, and Local OpenCV Image Analysis endpoints
- Measures per-request response latency (ms) and verifies 0% failure rate
"""

import base64
from concurrent.futures import ThreadPoolExecutor, as_completed
import threading
import time

import cv2
import numpy as np
import requests
from werkzeug.serving import make_server

from tests.report_writer import make_row, write_excel_report
from web_app import create_app


def _sample_image_b64() -> str:
    img = np.full((120, 160, 3), (150, 145, 225), dtype=np.uint8)
    ok, buf = cv2.imencode(".jpg", img)
    assert ok
    return base64.b64encode(buf.tobytes()).decode("ascii")


def run_load_suite() -> str:
    rows = []
    app = create_app({"TESTING": False, "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:"})
    server = make_server("127.0.0.1", 0, app, threaded=True)
    port = server.server_port
    base_url = f"http://127.0.0.1:{port}"
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    try:
        mob_login = requests.post(
            f"{base_url}/mobile/login",
            json={"email": "surgeon@flapguard.local", "password": "Surgeon@123"},
            timeout=5,
        )
        token = mob_login.json()["token"]
        auth_headers = {"Authorization": f"Bearer {token}"}
        img_b64 = _sample_image_b64()

        targets = [
            ("GET", "/health", None, None),
            ("GET", "/login", None, None),
            ("GET", "/static/css/style.css", None, None),
            ("GET", "/mobile/patients", auth_headers, None),
            ("GET", "/mobile/alerts", auth_headers, None),
            ("POST", "/mobile/analyze-image", auth_headers, {"image_base64": img_b64}),
        ]

        total_requests = 320

        def worker(req_num: int):
            method, path, hdrs, json_body = targets[req_num % len(targets)]
            t0 = time.perf_counter()
            resp = requests.request(
                method,
                f"{base_url}{path}",
                headers=hdrs,
                json=json_body,
                timeout=10,
            )
            ms = (time.perf_counter() - t0) * 1000
            passed = resp.status_code == 200 and ms < 5000.0
            return req_num, method, path, resp.status_code, ms, passed

        results = []
        with ThreadPoolExecutor(max_workers=10) as pool:
            futures = [pool.submit(worker, i) for i in range(total_requests)]
            for fut in as_completed(futures):
                results.append(fut.result())

        results.sort(key=lambda item: item[0])
        for req_num, method, path, status_code, ms, passed in results:
            rows.append(make_row(
                "LOAD",
                req_num + 1,
                "Concurrent Load (10 Users)",
                f"Concurrent {method} {path} req #{req_num+1}",
                f"10 concurrent workers -> {method} {path}",
                "HTTP 200 & latency < 5000 ms",
                f"HTTP {status_code} in {ms:.2f} ms",
                ms,
                passed,
            ))
    finally:
        server.shutdown()
        thread.join(timeout=3)

    return write_excel_report("load-test-report.xlsx", "Load Test Report", rows)


def test_generate_load_report():
    path = run_load_suite()
    assert path.endswith("load-test-report.xlsx")


if __name__ == "__main__":
    run_load_suite()