"""Leave management (FR-HRM): balances, department approval routing,
self-approval block, attendance auto-marks and cancellation revert."""
from datetime import date, timedelta

from django.contrib.auth import get_user_model
from rest_framework.test import APITestCase

from apps.accounts.constants import (
    ROLE_CEO,
    ROLE_GM,
    ROLE_HOTEL_MGR,
    ROLE_HOUSEKEEPING,
    ROLE_HR,
    ROLE_REST_MGR,
)
from .models import Attendance, Employee, LeaveRequest, LeaveType

User = get_user_model()

# A Monday well inside the current year so ranges never cross years.
BASE = date(date.today().year, 6, 1)


class LeaveTests(APITestCase):
    def setUp(self):
        self.hk_user = User.objects.create_user("hkuser", password="x", role=ROLE_HOUSEKEEPING)
        self.hotel_mgr = User.objects.create_user("hotelmgr", password="x", role=ROLE_HOTEL_MGR)
        self.rest_mgr = User.objects.create_user("restmgr", password="x", role=ROLE_REST_MGR)
        self.hr = User.objects.create_user("hruser", password="x", role=ROLE_HR)
        self.gm = User.objects.create_user("gmuser", password="x", role=ROLE_GM)

        self.hk_emp = Employee.objects.create(
            name="Sunita", department="Housekeeping", role="HK Supervisor", user=self.hk_user)
        self.cook = Employee.objects.create(name="Ravi", department="Kitchen", role="Cook")

        self.casual = LeaveType.objects.create(name="Casual Leave", annual_quota=12, is_paid=True)
        self.lop = LeaveType.objects.create(name="Loss of Pay", annual_quota=0, is_paid=False)

    def _apply(self, user, start, days, lt=None, employee=None):
        self.client.force_authenticate(user)
        body = {"leave_type": (lt or self.casual).id, "start_date": str(start),
                "end_date": str(start + timedelta(days=days - 1)), "reason": "test"}
        if employee:
            body["employee"] = employee.id
        return self.client.post("/api/leave/", body, format="json")

    def _decide(self, user, req_id, decision="approve"):
        self.client.force_authenticate(user)
        return self.client.post(f"/api/leave/{req_id}/decide/", {"decision": decision}, format="json")

    def test_self_service_apply_and_balance(self):
        res = self._apply(self.hk_user, BASE, 3)
        self.assertEqual(res.status_code, 201, res.data)
        self.assertEqual(res.data["days"], 3)
        # The card tells the employee whose desk it waits on.
        self.assertIn(ROLE_HOTEL_MGR, res.data["approver_roles"])
        bal = self.client.get("/api/leave/balances/").data["balances"]
        casual = next(b for b in bal if b["id"] == self.casual.id)
        self.assertEqual(casual["pending"], 3)
        self.assertEqual(casual["remaining"], 9)

    def test_two_level_routing(self):
        req = self._apply(self.hk_user, BASE, 2).data
        # Restaurant Manager must NOT decide housekeeping leave…
        res = self._decide(self.rest_mgr, req["id"])
        self.assertEqual(res.status_code, 403)
        # …and HR cannot jump the queue before the department manager.
        res = self._decide(self.hr, req["id"])
        self.assertEqual(res.status_code, 403)
        # Level 1: the Hotel Manager approves → moves to HR's desk.
        res = self._decide(self.hotel_mgr, req["id"])
        self.assertEqual(res.status_code, 200, res.data)
        self.assertEqual(res.data["status"], "manager_approved")
        self.assertEqual(res.data["manager_decided_by"], "hotelmgr")
        self.assertIn(ROLE_HR, res.data["approver_roles"])
        # A department manager is not a final approver.
        res = self._decide(self.hotel_mgr, req["id"])
        self.assertEqual(res.status_code, 403)
        # Level 2: HR's final approval.
        res = self._decide(self.hr, req["id"])
        self.assertEqual(res.status_code, 200, res.data)
        self.assertEqual(res.data["status"], "approved")
        self.assertEqual(res.data["decided_by"], "hruser")

    def test_cancel_authority_by_stage(self):
        ceo = User.objects.create_user("ceouser", password="x", role=ROLE_CEO)
        # While in the pipeline the employee can withdraw their own request…
        req = self._apply(self.hk_user, BASE, 2).data
        self.client.force_authenticate(self.hk_user)
        res = self.client.post(f"/api/leave/{req['id']}/cancel/", format="json")
        self.assertEqual(res.status_code, 200, res.data)
        # …but once approved, only a manager/HR can cancel — not the
        # employee, and not the visibility-only CEO.
        req = self._apply(self.hk_user, BASE + timedelta(days=10), 2).data
        self._decide(self.hotel_mgr, req["id"])
        self._decide(self.hr, req["id"])
        for blocked in (self.hk_user, ceo):
            self.client.force_authenticate(blocked)
            res = self.client.post(f"/api/leave/{req['id']}/cancel/", format="json")
            self.assertEqual(res.status_code, 403, res.data)
        self.client.force_authenticate(self.hr)
        res = self.client.post(f"/api/leave/{req['id']}/cancel/", format="json")
        self.assertEqual(res.status_code, 200, res.data)

    def test_hr_can_reject_at_final_level(self):
        req = self._apply(self.hk_user, BASE, 2).data
        self._decide(self.hotel_mgr, req["id"])
        res = self._decide(self.hr, req["id"], "reject")
        self.assertEqual(res.status_code, 200, res.data)
        self.assertEqual(res.data["status"], "rejected")
        # No attendance was ever written.
        self.assertEqual(Attendance.objects.count(), 0)

    def test_approval_marks_attendance_and_cancel_reverts(self):
        req = self._apply(self.hk_user, BASE, 3).data
        self._decide(self.hotel_mgr, req["id"])
        # Manager approval alone puts nothing on the attendance record.
        self.assertEqual(Attendance.objects.count(), 0)
        self._decide(self.hr, req["id"])
        marks = Attendance.objects.filter(employee=self.hk_emp, marked_by=f"leave:{req['id']}")
        self.assertEqual(marks.count(), 3)
        self.assertTrue(all(m.status == Attendance.LEAVE for m in marks))
        # Cancelling the approved leave deletes exactly those marks.
        self.client.force_authenticate(self.hotel_mgr)
        res = self.client.post(f"/api/leave/{req['id']}/cancel/", format="json")
        self.assertEqual(res.status_code, 200, res.data)
        self.assertEqual(Attendance.objects.filter(employee=self.hk_emp).count(), 0)

    def test_unpaid_leave_marks_absent(self):
        # A universal role (GM) can act at both levels.
        req = self._apply(self.hk_user, BASE, 2, lt=self.lop).data
        res = self._decide(self.gm, req["id"])
        self.assertEqual(res.data["status"], "manager_approved")
        res = self._decide(self.gm, req["id"])
        self.assertEqual(res.data["status"], "approved", res.data)
        marks = Attendance.objects.filter(employee=self.hk_emp)
        self.assertEqual(marks.count(), 2)
        self.assertTrue(all(m.status == Attendance.ABSENT for m in marks))

    def test_cannot_decide_own_request(self):
        # The GM has a staff record and applies; universal approver or not,
        # someone else must sign it off.
        Employee.objects.create(name="GM", department="Administration", role="GM", user=self.gm)
        req = self._apply(self.gm, BASE, 1).data
        res = self.client.post(f"/api/leave/{req['id']}/decide/", {"decision": "approve"}, format="json")
        self.assertEqual(res.status_code, 403)

    def test_quota_and_overlap_enforced(self):
        res = self._apply(self.hk_user, BASE, 13)   # over the 12-day quota
        self.assertEqual(res.status_code, 400)
        self.assertIn("balance", res.data["detail"])
        self._apply(self.hk_user, BASE, 3)
        res = self._apply(self.hk_user, BASE + timedelta(days=2), 2)   # overlaps
        self.assertEqual(res.status_code, 400)
        # LOP is uncapped — a long stretch is fine.
        res = self._apply(self.hk_user, BASE + timedelta(days=30), 20, lt=self.lop)
        self.assertEqual(res.status_code, 201, res.data)

    def test_on_behalf_entry(self):
        # A housekeeping login can't file for the Kitchen cook…
        res = self._apply(self.hk_user, BASE, 2, employee=self.cook)
        self.assertEqual(res.status_code, 403)
        # …but HR can, and the Restaurant Manager then approves.
        res = self._apply(self.hr, BASE, 2, employee=self.cook)
        self.assertEqual(res.status_code, 201, res.data)
        dec = self._decide(self.rest_mgr, res.data["id"])
        self.assertEqual(dec.status_code, 200, dec.data)
        self.assertEqual(dec.data["status"], "manager_approved")
        # HR filed this one — HR can't also give its final sign-off.
        dec = self._decide(self.hr, res.data["id"])
        self.assertEqual(dec.status_code, 403)
        dec = self._decide(self.gm, res.data["id"])
        self.assertEqual(dec.data["status"], "approved", dec.data)

    def test_no_employee_record(self):
        res = self._apply(self.rest_mgr, BASE, 1)
        self.assertEqual(res.status_code, 400)
        self.assertIn("staff record", res.data["detail"])

    def test_queue_view_scoped_to_my_departments(self):
        self._apply(self.hk_user, BASE, 2)                       # housekeeping
        self._apply(self.hr, BASE, 2, employee=self.cook)        # kitchen
        self.client.force_authenticate(self.hotel_mgr)
        rows = self.client.get("/api/leave/?view=queue").data
        self.assertEqual([r["department"] for r in rows], ["Housekeeping"])
        self.client.force_authenticate(self.rest_mgr)
        rows = self.client.get("/api/leave/?view=queue").data
        self.assertEqual([r["department"] for r in rows], ["Kitchen"])
        # HR's queue is empty until a manager approves; then it appears.
        self.client.force_authenticate(self.hr)
        self.assertEqual(self.client.get("/api/leave/?view=queue").data, [])
        hk_req = LeaveRequest.objects.get(employee=self.hk_emp)
        self._decide(self.hotel_mgr, hk_req.id)
        self.client.force_authenticate(self.hr)
        rows = self.client.get("/api/leave/?view=queue").data
        self.assertEqual([r["status"] for r in rows], ["manager_approved"])

    def test_leave_type_master_gated(self):
        self.client.force_authenticate(self.hk_user)
        res = self.client.post("/api/leave/save_type/", {"name": "Comp Off", "annual_quota": 5},
                               format="json")
        self.assertEqual(res.status_code, 403)
        self.client.force_authenticate(self.hr)
        res = self.client.post("/api/leave/save_type/", {"name": "Comp Off", "annual_quota": 5},
                               format="json")
        self.assertEqual(res.status_code, 200, res.data)
        self.assertTrue(LeaveType.objects.filter(name="Comp Off").exists())
