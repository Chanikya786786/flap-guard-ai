"""
Shared Excel (.xlsx) Test Report Writer for FlapGuard AI.
Ensures every generated report contains structured columns, 300+ rows,
and exact Status = 'Pass' verification.
"""

import os
from datetime import datetime, timezone
from typing import Dict, List

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill


REPORTS_DIR = "reports"
HEADERS = [
    "Test_ID",
    "Category",
    "Test_Name",
    "Input_Details",
    "Expected_Result",
    "Actual_Result",
    "Duration_ms",
    "Status",
    "Timestamp_UTC",
]


def utc_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def make_row(
    prefix: str,
    index: int,
    category: str,
    test_name: str,
    input_details: str,
    expected: str,
    actual: str,
    duration_ms: float,
    passed: bool,
) -> Dict[str, object]:
    return {
        "Test_ID": f"{prefix}-{index:04d}",
        "Category": category,
        "Test_Name": test_name,
        "Input_Details": str(input_details)[:250],
        "Expected_Result": str(expected)[:250],
        "Actual_Result": str(actual)[:250],
        "Duration_ms": round(float(duration_ms), 2),
        "Status": "Pass" if passed else "Fail",
        "Timestamp_UTC": utc_stamp(),
    }


def write_excel_report(filename: str, sheet_title: str, rows: List[Dict[str, object]]) -> str:
    os.makedirs(REPORTS_DIR, exist_ok=True)
    out_path = os.path.join(REPORTS_DIR, filename)

    wb = Workbook()
    ws = wb.active
    ws.title = sheet_title[:31]

    header_fill = PatternFill(start_color="0B2545", end_color="0B2545", fill_type="solid")
    header_font = Font(color="FFFFFF", bold=True)
    pass_fill = PatternFill(start_color="DCFCE7", end_color="DCFCE7", fill_type="solid")
    pass_font = Font(color="166534", bold=True)
    fail_fill = PatternFill(start_color="FEE2E2", end_color="FEE2E2", fill_type="solid")
    fail_font = Font(color="991B1B", bold=True)

    ws.append(HEADERS)
    for col_idx in range(1, len(HEADERS) + 1):
        cell = ws.cell(row=1, column=col_idx)
        cell.fill = header_fill
        cell.font = header_font
        cell.alignment = Alignment(horizontal="center", vertical="center")

    pass_count = 0
    fail_count = 0

    for row_data in rows:
        ws.append([row_data.get(col, "") for col in HEADERS])
        status_cell = ws.cell(row=ws.max_row, column=HEADERS.index("Status") + 1)
        if row_data.get("Status") == "Pass":
            pass_count += 1
            status_cell.fill = pass_fill
            status_cell.font = pass_font
        else:
            fail_count += 1
            status_cell.fill = fail_fill
            status_cell.font = fail_font
        status_cell.alignment = Alignment(horizontal="center")

    ws.freeze_panes = "A2"

    col_widths = {
        "A": 14,
        "B": 22,
        "C": 36,
        "D": 40,
        "E": 34,
        "F": 34,
        "G": 14,
        "H": 12,
        "I": 22,
    }
    for col_letter, width in col_widths.items():
        ws.column_dimensions[col_letter].width = width

    wb.save(out_path)
    print(f"[REPORT] {out_path} -> Total: {len(rows)} | Pass: {pass_count} | Fail: {fail_count}")

    if len(rows) < 300:
        raise AssertionError(f"{filename} has only {len(rows)} rows (expected 300+).")
    if fail_count > 0:
        raise AssertionError(f"{filename} has {fail_count} failed test cases.")
    return out_path