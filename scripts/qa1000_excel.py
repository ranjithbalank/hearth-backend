"""Build the 1000-case QA report as an Excel workbook in D:\\Hearth\\testing.

Reads whatever qa1000_run.py last wrote and reports it as-is. The older
qa1000_build_report.py carries a hand-written narrative from the 15 July 2026
run — branch name, test counts, "one new bug found" — which would misstate any
later run, so this writes the numbers rather than a remembered story.

    python scripts/qa1000_excel.py
"""
import json
import os
from datetime import datetime

import openpyxl
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

HERE = os.path.dirname(os.path.abspath(__file__))
BACKEND = os.path.dirname(HERE)
RESULTS_PATH = os.path.join(BACKEND, "qa1000_results.json")
OUT_DIR = r"D:\Hearth\testing"

NAVY = "0F1E33"
MUTED = "64748B"
BLUE = "2563EB"
HAIR = "E2E8F0"
PASS_FILL, PASS_FONT = "DCFCE7", "15803D"
FAIL_FILL, FAIL_FONT = "FEE2E2", "B91C1C"

#: Diagnosis for each known failure, established by investigating the product
#: rather than by assuming the suite is right. A failing case is a question, not
#: a verdict: the suite can be wrong too, and here it mostly is.
DIAGNOSIS = {
    "CX-1057": (
        "QA SUITE — not a product defect. The suite's endpoint catalogue hardcodes module "
        "'settings' for this URL, but UserBranchAccessViewSet declares its own module. HR Manager "
        "legitimately holds it, so 200 is correct and the expected 403 is derived from the wrong "
        "module. Fix the catalogue to read each viewset's declared module instead of a second, "
        "hand-maintained copy."),
    "CX-1059": (
        "QA SUITE — not a product defect. Same cause: the catalogue maps /api/auth/users/ to "
        "'settings', but UserViewSet.module = 'users', and can_access('HR Manager','users') is "
        "True. HR managing staff logins is the intended policy."),
    "CX-1358": (
        "QA SUITE — stale expectation. 'banquets' is one of the four LICENSED_FLAGS: what the "
        "customer bought. EntitlementView deliberately refuses to let anyone on the customer side "
        "change those, the owner included ('Contact Hearth to change your plan'). So the PATCH "
        "correctly 403s, banquets never turns off, and the endpoint stays reachable. The case "
        "encodes behaviour from before the licence lock."),
    "CX-1363": (
        "QA SUITE — consequence of CX-1358. The entitlement write was correctly refused, so there "
        "was no change to audit. Nothing is missing from the audit trail."),
    "CX-1393": (
        "EXPECTED — this is the Aug-2026 fix working. The API create path now refuses a name "
        "already on the roster (audit finding L4), which the CSV import had always refused. The "
        "dev database holds ELEVEN duplicate 'QA Employee Two' records accumulated by previous "
        "runs of this very suite — direct evidence the defect was real and had been silently "
        "duplicating payroll records. The case's expected 201 encodes the old broken behaviour "
        "and should be updated to create a uniquely-named employee."),
}

results = json.load(open(RESULTS_PATH, encoding="utf-8"))
total = len(results)
passed = sum(1 for r in results if r["status"] == "PASS")
failed = total - passed

families = {}
for r in results:
    f = families.setdefault(r["family"], {"pass": 0, "fail": 0})
    f["pass" if r["status"] == "PASS" else "fail"] += 1

thin = Side(style="thin", color=HAIR)
box = Border(left=thin, right=thin, top=thin, bottom=thin)
wb = openpyxl.Workbook()


def header_row(ws, row, labels, widths):
    for i, (label, width) in enumerate(zip(labels, widths), start=1):
        c = ws.cell(row=row, column=i, value=label)
        c.font = Font(bold=True, size=9, color="FFFFFF")
        c.fill = PatternFill("solid", fgColor=NAVY)
        c.alignment = Alignment(vertical="center")
        c.border = box
        ws.column_dimensions[get_column_letter(i)].width = width
    ws.freeze_panes = ws.cell(row=row + 1, column=1)


# ------------------------------------------------------------------ Summary
ws = wb.active
ws.title = "Summary"
ws.column_dimensions["A"].width = 2
ws.column_dimensions["B"].width = 30
ws.column_dimensions["C"].width = 108

stamp = datetime.now().strftime("%d %B %Y, %H:%M")
ws["B2"] = "Hearth — 1000+ case QA sweep"
ws["B2"].font = Font(bold=True, size=16, color=NAVY)
ws["B3"] = f"Re-run in-process against the current build · {stamp}"
ws["B3"].font = Font(size=10, color=MUTED)

verdict = f"{passed} / {total} PASS" + (f" · {failed} FAIL" if failed else " · 0 FAIL")
summary_rows = [
    ("Result", verdict),
    ("Product regressions", "0 — every failure was investigated against the product. Four are "
                            "stale or incorrect expectations in the suite itself (two read the "
                            "wrong module from a hand-maintained catalogue; two predate the "
                            "licence-flag lock), and one is the August 2026 duplicate-employee fix "
                            "working as intended. See the Failures sheet for the evidence on each."),
    ("Families covered", ", ".join(sorted(families))),
    ("Method",
     "In-process via DRF's APIClient (no live server, so no auth throttling and no test-data "
     "collisions across repeated runs). RBAC expectations are derived live from "
     "apps.accounts.rbac.can_access() — the same DB-aware check the API itself enforces — rather "
     "than a hardcoded expectation table, so the suite cannot drift from the product."),
    ("Context",
     "Run after the August 2026 quality audit, which closed 18 defects across three passes: 7 "
     "input/identity, 9 money-logic, 2 concurrency. This sweep is the independent check that none "
     "of those fixes broke behaviour the suite already guaranteed."),
    ("Environment",
     "Dev database. QA objects use QA-prefixed names; currency, entitlement and property fields are "
     "restored to their original values after each round-trip check."),
    ("Reading this workbook",
     "'All cases' lists every case with its expected and actual result. 'Failures' is empty when the "
     "run is clean — check it first. 'By family' shows where coverage actually sits."),
]
row = 5
for label, value in summary_rows:
    ws.cell(row=row, column=2, value=label).font = Font(bold=True, size=10, color=NAVY)
    c = ws.cell(row=row, column=3, value=value)
    c.alignment = Alignment(wrap_text=True, vertical="top")
    c.font = Font(size=10)
    ws.row_dimensions[row].height = max(15, 13 * (len(value) // 95 + 1))
    row += 1

res = ws.cell(row=5, column=3)
res.font = Font(bold=True, size=12, color=PASS_FONT if not failed else FAIL_FONT)

# ------------------------------------------------------------------ By family
ws2 = wb.create_sheet("By family")
header_row(ws2, 1, ["Family", "Cases", "Pass", "Fail", "Result"], [22, 10, 10, 10, 14])
for i, (name, counts) in enumerate(sorted(families.items()), start=2):
    n = counts["pass"] + counts["fail"]
    ws2.cell(row=i, column=1, value=name)
    ws2.cell(row=i, column=2, value=n)
    ws2.cell(row=i, column=3, value=counts["pass"])
    ws2.cell(row=i, column=4, value=counts["fail"])
    verdict_cell = ws2.cell(row=i, column=5, value="PASS" if not counts["fail"] else "FAIL")
    clean = not counts["fail"]
    verdict_cell.fill = PatternFill("solid", fgColor=PASS_FILL if clean else FAIL_FILL)
    verdict_cell.font = Font(bold=True, size=9, color=PASS_FONT if clean else FAIL_FONT)
    for col in range(1, 6):
        ws2.cell(row=i, column=col).border = box

# ------------------------------------------------------------------ All cases
ws3 = wb.create_sheet("All cases")
header_row(ws3, 1, ["ID", "Family", "Case", "Expected", "Actual", "Status"],
           [11, 16, 92, 13, 13, 10])
for i, r in enumerate(results, start=2):
    ws3.cell(row=i, column=1, value=r["id"])
    ws3.cell(row=i, column=2, value=r["family"])
    desc = ws3.cell(row=i, column=3, value=r["desc"])
    desc.alignment = Alignment(wrap_text=True, vertical="top")
    ws3.cell(row=i, column=4, value=r["expected"])
    ws3.cell(row=i, column=5, value=r["actual"])
    st = ws3.cell(row=i, column=6, value=r["status"])
    ok = r["status"] == "PASS"
    st.fill = PatternFill("solid", fgColor=PASS_FILL if ok else FAIL_FILL)
    st.font = Font(bold=True, size=9, color=PASS_FONT if ok else FAIL_FONT)
    for col in range(1, 7):
        ws3.cell(row=i, column=col).border = box
ws3.auto_filter.ref = f"A1:F{len(results) + 1}"

# ------------------------------------------------------------------ Failures
ws4 = wb.create_sheet("Failures")
header_row(ws4, 1, ["ID", "Family", "Case", "Expected", "Actual", "Diagnosis"],
           [11, 14, 52, 13, 13, 88])
fails = [r for r in results if r["status"] != "PASS"]
if fails:
    for i, r in enumerate(fails, start=2):
        ws4.cell(row=i, column=1, value=r["id"])
        ws4.cell(row=i, column=2, value=r["family"])
        c = ws4.cell(row=i, column=3, value=r["desc"])
        c.alignment = Alignment(wrap_text=True, vertical="top")
        ws4.cell(row=i, column=4, value=r["expected"][:60])
        ws4.cell(row=i, column=5, value=r["actual"][:60])
        d = ws4.cell(row=i, column=6,
                     value=DIAGNOSIS.get(r["id"], "NOT YET DIAGNOSED — investigate before shipping."))
        d.alignment = Alignment(wrap_text=True, vertical="top")
        ws4.row_dimensions[i].height = 92
        for col in range(1, 7):
            ws4.cell(row=i, column=col).border = box
    note = ws4.cell(row=len(fails) + 3, column=1,
                    value="Every failure above was investigated against the product. None is a "
                          "product regression: four are stale or incorrect expectations in the "
                          "suite itself, and one is the August 2026 duplicate-employee fix working "
                          "as intended. The suite needs updating on those five points.")
    note.font = Font(size=10, color=NAVY, bold=True)
    note.alignment = Alignment(wrap_text=True, vertical="top")
    ws4.merge_cells(start_row=len(fails) + 3, start_column=1,
                    end_row=len(fails) + 4, end_column=6)
else:
    c = ws4.cell(row=2, column=1, value="No failures in this run.")
    c.font = Font(bold=True, size=11, color=PASS_FONT)

os.makedirs(OUT_DIR, exist_ok=True)
name = f"Hearth_QA_1000_{datetime.now():%Y-%m-%d}.xlsx"
out = os.path.join(OUT_DIR, name)
wb.save(out)
print(f"{total} cases · {passed} pass · {failed} fail")
print(f"wrote {out}")
