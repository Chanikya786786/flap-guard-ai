"""
Master Test Runner & Excel Report Verifier for FlapGuard AI.
Verifies all 7 required .xlsx reports exist, have 300+ rows, and 100% 'Pass' status.
"""

import argparse
import os
import sys
from openpyxl import load_workbook

from tests.test_deployment_suite import run_deployment_suite
from tests.test_load_suite import run_load_suite
from tests.test_selenium_web_suite import run_selenium_web_suite
from tests.test_unit_suite import run_unit_suite
from tests.test_validation_suite import run_validation_suite
from tests.test_vulnerability_suite import run_vulnerability_suite


EXPECTED_REPORTS = [
    "selenium-web-report.xlsx",
    "appium-android-report.xlsx",
    "unit-test-report.xlsx",
    "validation-test-report.xlsx",
    "deployment-test-report.xlsx",
    "load-test-report.xlsx",
    "vulnerability-test-report.xlsx",
]


def verify_reports(reports_dir: str = "reports", require_appium: bool = True) -> None:
    print("\n=== Final Excel Report Audit ===")
    targets = EXPECTED_REPORTS if require_appium else [r for r in EXPECTED_REPORTS if r != "appium-android-report.xlsx"]
    all_ok = True

    for fname in targets:
        fpath = os.path.join(reports_dir, fname)
        if not os.path.isfile(fpath):
            print(f"[MISSING] {fpath}")
            all_ok = False
            continue

        wb = load_workbook(fpath, read_only=True)
        ws = wb.active
        rows = list(ws.iter_rows(values_only=True))
        wb.close()

        headers = rows[0]
        status_idx = headers.index("Status")
        data_rows = rows[1:]
        total = len(data_rows)
        passes = sum(1 for r in data_rows if r[status_idx] == "Pass")
        fails = total - passes

        ok = total >= 300 and fails == 0
        tag = "OK" if ok else "FAIL"
        print(f"[{tag}] {fname:34s} | Rows: {total:4d} | Pass: {passes:4d} | Fail: {fails}")
        if not ok:
            all_ok = False

    if not all_ok:
        sys.exit(1)
    print("\nALL REQUIRED EXCEL REPORTS VERIFIED: 300+ ROWS & 100% PASS!")


def main():
    parser = argparse.ArgumentParser(description="Run FlapGuard AI automated test suites.")
    parser.add_argument("--skip-appium", action="store_true", help="Skip Appium Android suite.")
    parser.add_argument("--verify-only", action="store_true", help="Only audit existing reports/.xlsx files.")
    args = parser.parse_args()

    if not args.verify_only:
        run_unit_suite()
        run_validation_suite()
        run_deployment_suite()
        run_load_suite()
        run_vulnerability_suite()
        run_selenium_web_suite()
        if not args.skip_appium:
            from tests.test_appium_android_suite import run_appium_android_suite
            run_appium_android_suite()

    verify_reports(require_appium=not args.skip_appium)


if __name__ == "__main__":
    main()