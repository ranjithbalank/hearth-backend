from decimal import Decimal

from django.test import TestCase
from django.urls import reverse
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.inventory.models import Ingredient

from .models import MaterialRequest, MaterialRequestLine


class MaterialRequestTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        # Material requests need a role with the "matreq" module (cashier is POS-only).
        self.client.force_authenticate(User.objects.create_user(
            username="c", password="Tk9$mZ2pQw!7", role="General Manager"))
        self.ing = Ingredient.objects.create(name="Onion", unit="kg", current_stock=Decimal("20"))
        self.req = MaterialRequest.objects.create(department="Kitchen")
        MaterialRequestLine.objects.create(request=self.req, ingredient=self.ing, qty=Decimal("5"))

    def test_requested_to_issued_deducts_stock(self):
        # requested -> approved
        self.client.post(reverse("matreq-advance", args=[self.req.id]))
        self.req.refresh_from_db()
        self.assertEqual(self.req.status, MaterialRequest.APPROVED)
        # approved -> issued (deducts)
        self.client.post(reverse("matreq-advance", args=[self.req.id]))
        self.req.refresh_from_db()
        self.ing.refresh_from_db()
        self.assertEqual(self.req.status, MaterialRequest.ISSUED)
        self.assertEqual(self.ing.current_stock, Decimal("15.000"))

    def test_chef_requests_restaurant_manager_approves_store_issues(self):
        """Kitchen indents: chef requests, Restaurant Manager approves (the
        'food manager'), Store Keeper does the physical issue."""
        chef = APIClient()
        chef.force_authenticate(User.objects.create_user(
            username="chefmr", password="Tk9$mZ2pQw!7", role="Chef / Kitchen"))
        r = chef.post("/api/material-requests/", {
            "department": "Kitchen",
            "lines": [{"ingredient": self.ing.id, "qty": "4"}],
        }, format="json")
        self.assertEqual(r.status_code, 201, r.data)
        self.assertEqual(r.data["requested_by"], "chefmr")
        self.assertEqual(r.data["approver_roles"], ["General Manager", "Managing Director",
                                                     "Restaurant Manager", "Super Admin"])
        req_id = r.data["id"]
        # The chef can't approve their own indent…
        r = chef.post(f"/api/material-requests/{req_id}/advance/")
        self.assertEqual(r.status_code, 403)
        # …and the Store Keeper isn't a Kitchen approver either (only issues).
        store = APIClient()
        store.force_authenticate(User.objects.create_user(
            username="storemr", password="Tk9$mZ2pQw!7", role="Store Keeper"))
        r = store.post(f"/api/material-requests/{req_id}/advance/")
        self.assertEqual(r.status_code, 403)
        self.assertIn("Restaurant Manager", r.data["detail"])
        # The Restaurant Manager approves it…
        rm = APIClient()
        rm.force_authenticate(User.objects.create_user(
            username="rmmr", password="Tk9$mZ2pQw!7", role="Restaurant Manager"))
        self.assertEqual(rm.post(f"/api/material-requests/{req_id}/advance/").data["status"],
                         "approved")
        # …and now the Store Keeper (the physical custodian) issues it.
        self.assertEqual(store.post(f"/api/material-requests/{req_id}/advance/").data["status"],
                         "issued")
        self.ing.refresh_from_db()
        self.assertEqual(self.ing.current_stock, Decimal("16.000"))  # 20 − 4

    def test_housekeeping_and_maintenance_route_to_their_own_approvers(self):
        """Housekeeping/Banquets/Front-Office indents go to Front Office;
        Maintenance goes to Housekeeping (it already owns work orders)."""
        hk = APIClient()
        hk.force_authenticate(User.objects.create_user(
            username="hkmr", password="Tk9$mZ2pQw!7", role="Housekeeping"))
        r = hk.post("/api/material-requests/", {
            "department": "Housekeeping",
            "lines": [{"ingredient": self.ing.id, "qty": "2"}],
        }, format="json")
        self.assertEqual(r.status_code, 201, r.data)
        req_id = r.data["id"]
        # Housekeeping itself can't approve its own department's indent…
        r = hk.post(f"/api/material-requests/{req_id}/advance/")
        self.assertEqual(r.status_code, 403)
        self.assertIn("Front Office", r.data["detail"])
        # …Front Office approves, Store Keeper issues.
        fo = APIClient()
        fo.force_authenticate(User.objects.create_user(
            username="fomr", password="Tk9$mZ2pQw!7", role="Front Office"))
        self.assertEqual(fo.post(f"/api/material-requests/{req_id}/advance/").data["status"],
                         "approved")
        store = APIClient()
        store.force_authenticate(User.objects.create_user(
            username="storemr2", password="Tk9$mZ2pQw!7", role="Store Keeper"))
        self.assertEqual(store.post(f"/api/material-requests/{req_id}/advance/").data["status"],
                         "issued")

        # Maintenance indent: Housekeeping approves (Front Office can't).
        r = fo.post("/api/material-requests/", {
            "department": "Maintenance",
            "lines": [{"ingredient": self.ing.id, "qty": "1"}],
        }, format="json")
        req2_id = r.data["id"]
        r = fo.post(f"/api/material-requests/{req2_id}/advance/")
        self.assertEqual(r.status_code, 403)
        self.assertIn("Housekeeping", r.data["detail"])
        self.assertEqual(hk.post(f"/api/material-requests/{req2_id}/advance/").data["status"],
                         "approved")

    def test_gm_can_approve_and_issue_any_department(self):
        """GM/MD/Super Admin override every department's approval routing."""
        cashier = APIClient()
        cashier.force_authenticate(User.objects.create_user(
            username="cashmr", password="Tk9$mZ2pQw!7", role="F&B Cashier"))
        r = cashier.post("/api/material-requests/", {
            "department": "Bar", "lines": [{"ingredient": self.ing.id, "qty": "1"}],
        }, format="json")
        self.assertEqual(r.status_code, 201, r.data)
        req_id = r.data["id"]
        self.assertEqual(self.client.post(f"/api/material-requests/{req_id}/advance/").data["status"],
                         "approved")   # self.client is GM
        self.assertEqual(self.client.post(f"/api/material-requests/{req_id}/advance/").data["status"],
                         "issued")

    def test_list_is_segregated_not_a_dump_of_every_department(self):
        """A requester with no approval authority anywhere (e.g. Housekeeping
        raising for its own department) sees only what it raised — never
        another department's indents just because both hold 'matreq'."""
        hk = APIClient()
        hk.force_authenticate(User.objects.create_user(
            username="hklist", password="Tk9$mZ2pQw!7", role="Housekeeping"))
        chef = APIClient()
        chef.force_authenticate(User.objects.create_user(
            username="cheflist", password="Tk9$mZ2pQw!7", role="Chef / Kitchen"))
        r_hk = hk.post("/api/material-requests/", {
            "department": "Housekeeping", "lines": [{"ingredient": self.ing.id, "qty": "1"}],
        }, format="json")
        r_chef = chef.post("/api/material-requests/", {
            "department": "Kitchen", "lines": [{"ingredient": self.ing.id, "qty": "1"}],
        }, format="json")
        # Housekeeping's default queue view: Kitchen's indent never appears —
        # Housekeeping has no approval authority over Kitchen.
        ids = [r["id"] for r in hk.get("/api/material-requests/").data]
        self.assertNotIn(r_chef.data["id"], ids)
        self.assertNotIn(r_hk.data["id"], ids)   # not an approver of it either (self-requested)
        # Housekeeping's "mine" view shows exactly what it raised, nothing else.
        mine = hk.get("/api/material-requests/?view=mine").data
        self.assertEqual([r["id"] for r in mine], [r_hk.data["id"]])
        # Chef's queue is likewise empty — Chef approves nothing.
        self.assertEqual(chef.get("/api/material-requests/").data, [])

    def test_approval_queue_shows_only_whats_actually_actionable(self):
        """Front Office's queue: Housekeeping's pending request shows up
        (Front Office approves it); a Kitchen request does not."""
        hk = APIClient()
        hk.force_authenticate(User.objects.create_user(
            username="hkqueue", password="Tk9$mZ2pQw!7", role="Housekeeping"))
        chef = APIClient()
        chef.force_authenticate(User.objects.create_user(
            username="chefqueue", password="Tk9$mZ2pQw!7", role="Chef / Kitchen"))
        fo = APIClient()
        fo.force_authenticate(User.objects.create_user(
            username="foqueue", password="Tk9$mZ2pQw!7", role="Front Office"))

        r_hk = hk.post("/api/material-requests/", {
            "department": "Housekeeping", "lines": [{"ingredient": self.ing.id, "qty": "1"}],
        }, format="json").data
        r_chef = chef.post("/api/material-requests/", {
            "department": "Kitchen", "lines": [{"ingredient": self.ing.id, "qty": "1"}],
        }, format="json").data

        queue_ids = [r["id"] for r in fo.get("/api/material-requests/").data]
        self.assertIn(r_hk["id"], queue_ids)
        self.assertNotIn(r_chef["id"], queue_ids)

        # Once Front Office approves the Housekeeping indent, it drops out of
        # Front Office's queue (nothing left for them to do) but a Store
        # Keeper's queue now picks it up for issuing.
        fo.post(f"/api/material-requests/{r_hk['id']}/advance/")
        queue_ids = [r["id"] for r in fo.get("/api/material-requests/").data]
        self.assertNotIn(r_hk["id"], queue_ids)
        store = APIClient()
        store.force_authenticate(User.objects.create_user(
            username="storequeue", password="Tk9$mZ2pQw!7", role="Store Keeper"))
        store_queue_ids = [r["id"] for r in store.get("/api/material-requests/").data]
        self.assertIn(r_hk["id"], store_queue_ids)

    def test_restaurant_manager_cannot_request_the_department_it_approves(self):
        """The exact bug the user caught: Restaurant Manager both requesting
        AND approving Kitchen/Bar meant one person effectively self-served —
        the only thing standing in the way was a same-username check on a
        role most properties have exactly one account for. Now blocked at
        creation, not just at approval time."""
        rm = APIClient()
        rm.force_authenticate(User.objects.create_user(
            username="rmreq", password="Tk9$mZ2pQw!7", role="Restaurant Manager"))
        r = rm.post("/api/material-requests/", {
            "department": "Kitchen", "lines": [{"ingredient": self.ing.id, "qty": "1"}],
        }, format="json")
        self.assertEqual(r.status_code, 400)
        self.assertIn("approve Kitchen indents yourself", r.data["detail"])
        r = rm.post("/api/material-requests/", {
            "department": "Bar", "lines": [{"ingredient": self.ing.id, "qty": "1"}],
        }, format="json")
        self.assertEqual(r.status_code, 400)
        # But Restaurant Manager can still request departments it doesn't approve.
        r = rm.post("/api/material-requests/", {
            "department": "Housekeeping", "lines": [{"ingredient": self.ing.id, "qty": "1"}],
        }, format="json")
        self.assertEqual(r.status_code, 201)

    def test_housekeeping_cannot_request_maintenance_it_approves(self):
        hk = APIClient()
        hk.force_authenticate(User.objects.create_user(
            username="hkself", password="Tk9$mZ2pQw!7", role="Housekeeping"))
        r = hk.post("/api/material-requests/", {
            "department": "Maintenance", "lines": [{"ingredient": self.ing.id, "qty": "1"}],
        }, format="json")
        self.assertEqual(r.status_code, 400)
        # Its own department is fine — Front Office approves that, not Housekeeping.
        r = hk.post("/api/material-requests/", {
            "department": "Housekeeping", "lines": [{"ingredient": self.ing.id, "qty": "1"}],
        }, format="json")
        self.assertEqual(r.status_code, 201)

    def test_banquets_and_front_office_departments_route_to_gm_not_front_office(self):
        """Front Office is the only role that would ever raise these two —
        so it can't also be the approver. GM/MD/Super Admin sign off instead."""
        fo = APIClient()
        fo.force_authenticate(User.objects.create_user(
            username="foself", password="Tk9$mZ2pQw!7", role="Front Office"))
        r = fo.post("/api/material-requests/", {
            "department": "Banquets", "lines": [{"ingredient": self.ing.id, "qty": "1"}],
        }, format="json")
        self.assertEqual(r.status_code, 201, r.data)
        self.assertEqual(r.data["approver_roles"],
                         ["General Manager", "Managing Director", "Super Admin"])
        req_id = r.data["id"]
        # Front Office can't approve its own Banquets indent…
        r = fo.post(f"/api/material-requests/{req_id}/advance/")
        self.assertEqual(r.status_code, 403)
        # …GM does.
        self.assertEqual(self.client.post(f"/api/material-requests/{req_id}/advance/").data["status"],
                         "approved")
        # Same story for the Front Office department's own supplies.
        r = fo.post("/api/material-requests/", {
            "department": "Front Office", "lines": [{"ingredient": self.ing.id, "qty": "1"}],
        }, format="json")
        self.assertEqual(r.data["approver_roles"],
                         ["General Manager", "Managing Director", "Super Admin"])

    def test_universal_roles_exempt_from_the_self_request_block(self):
        """GM/MD/Super Admin can still request anything — they're already
        full-access executives everywhere else in the system."""
        r = self.client.post("/api/material-requests/", {   # self.client == GM
            "department": "Kitchen", "lines": [{"ingredient": self.ing.id, "qty": "1"}],
        }, format="json")
        self.assertEqual(r.status_code, 201)

    def test_super_admin_md_gm_get_full_oversight_including_issued_history(self):
        """Universal roles see EVERY request, every department, every status
        — not just what's actionable — since they already have full
        authority everywhere. Department-scoped roles only see their queue."""
        chef = APIClient()
        chef.force_authenticate(User.objects.create_user(
            username="chefover", password="Tk9$mZ2pQw!7", role="Chef / Kitchen"))
        r = chef.post("/api/material-requests/", {
            "department": "Kitchen", "lines": [{"ingredient": self.ing.id, "qty": "1"}],
        }, format="json").data
        # Fully complete the lifecycle so it's ISSUED — normally invisible to
        # anyone once there's nothing left to action.
        rm = APIClient()
        rm.force_authenticate(User.objects.create_user(
            username="rmover", password="Tk9$mZ2pQw!7", role="Restaurant Manager"))
        rm.post(f"/api/material-requests/{r['id']}/advance/")
        store = APIClient()
        store.force_authenticate(User.objects.create_user(
            username="storeover", password="Tk9$mZ2pQw!7", role="Store Keeper"))
        store.post(f"/api/material-requests/{r['id']}/advance/")

        # Store Keeper's own queue no longer shows it (nothing left to do).
        self.assertNotIn(r["id"], [x["id"] for x in store.get("/api/material-requests/").data])
        # GM (self.client) sees it anyway — full oversight, any status.
        gm_ids = [x["id"] for x in self.client.get("/api/material-requests/").data]
        self.assertIn(r["id"], gm_ids)
        issued = next(x for x in self.client.get("/api/material-requests/").data if x["id"] == r["id"])
        self.assertEqual(issued["status"], "issued")

        for role, uname in [("Managing Director", "mdover"), ("Super Admin", "saover")]:
            u = APIClient()
            u.force_authenticate(User.objects.create_user(
                username=uname, password="Tk9$mZ2pQw!7", role=role))
            self.assertIn(r["id"], [x["id"] for x in u.get("/api/material-requests/").data])

    def test_materials_picklist_available_without_inventory_module(self):
        """Housekeeping has 'matreq' but not the full 'inventory' module — it
        still needs to browse materials to build a request."""
        hk = APIClient()
        hk.force_authenticate(User.objects.create_user(
            username="hkpick", password="Tk9$mZ2pQw!7", role="Housekeeping"))
        self.assertEqual(hk.get("/api/inventory/").status_code, 403)   # no full module
        r = hk.get("/api/material-requests/materials/")
        self.assertEqual(r.status_code, 200)
        self.assertIn("Onion", [m["name"] for m in r.data])

    def test_only_store_or_manager_issues_approved_stock(self):
        """Approving ≠ issuing: a department approver who isn't a stock
        custodian can't do the physical hand-over."""
        r = self.client.post("/api/material-requests/", {
            "department": "Kitchen", "lines": [{"ingredient": self.ing.id, "qty": "1"}],
        }, format="json")
        req_id = r.data["id"]
        rm = APIClient()
        rm.force_authenticate(User.objects.create_user(
            username="rmissue", password="Tk9$mZ2pQw!7", role="Restaurant Manager"))
        self.assertEqual(rm.post(f"/api/material-requests/{req_id}/advance/").data["status"],
                         "approved")
        chef = APIClient()
        chef.force_authenticate(User.objects.create_user(
            username="chefissue", password="Tk9$mZ2pQw!7", role="Chef / Kitchen"))
        r = chef.post(f"/api/material-requests/{req_id}/advance/")
        self.assertEqual(r.status_code, 403)
        self.assertIn("store keeper", r.data["detail"])

    def test_cannot_approve_more_than_store_holds(self):
        chef = APIClient()
        chef.force_authenticate(User.objects.create_user(
            username="chefmr2", password="Tk9$mZ2pQw!7", role="Chef / Kitchen"))
        r = chef.post("/api/material-requests/", {
            "department": "Kitchen",
            "lines": [{"ingredient": self.ing.id, "qty": "999"}],
        }, format="json")
        r = self.client.post(f"/api/material-requests/{r.data['id']}/advance/")
        self.assertEqual(r.status_code, 400)
        self.assertIn("purchase order", r.data["detail"])
