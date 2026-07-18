from decimal import Decimal

from django.test import TestCase
from django.urls import reverse
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.inventory.models import Ingredient


class NotificationTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        Ingredient.objects.create(name="Butter", unit="kg", current_stock=Decimal("1"),
                                  reorder_level=Decimal("5"))

    def _login(self, role):
        u = User.objects.create_user(username=f"u_{role}", password="Tk9$mZ2pQw!7", role=role)
        self.client.force_authenticate(u)

    def test_manager_sees_inventory_alert(self):
        # Stores/inventory is management-scoped; the GM sees the low-stock alert.
        self._login("General Manager")
        resp = self.client.get(reverse("notifications"))
        titles = [a["title"] for a in resp.data["alerts"]]
        self.assertTrue(any("Low stock: Butter" in t for t in titles))

    def test_cashier_does_not_see_inventory_alert(self):
        # Cashier is scoped to POS only -> inventory alert is filtered out (RBAC).
        self._login("F&B Cashier")
        resp = self.client.get(reverse("notifications"))
        titles = [a["title"] for a in resp.data["alerts"]]
        self.assertFalse(any("Low stock" in t for t in titles))

    def test_housekeeping_does_not_see_inventory_alert(self):
        # Housekeeping has no 'inventory' module access -> alert filtered out (RBAC-scoped).
        self._login("Housekeeping")
        resp = self.client.get(reverse("notifications"))
        titles = [a["title"] for a in resp.data["alerts"]]
        self.assertFalse(any("Low stock" in t for t in titles))

    def test_store_keeper_notified_when_indent_approved_chef_is_not(self):
        """Chef requests chicken → Restaurant Manager approves → the Store
        Keeper gets pinged to issue it. The chef who raised it, and other
        matreq-holders with no issuing authority, don't need this alert."""
        chicken = Ingredient.objects.create(name="Chicken", unit="kg",
                                            current_stock=Decimal("10"))
        chef = User.objects.create_user(username="chefnotif", password="Tk9$mZ2pQw!7",
                                        role="Chef / Kitchen")
        chef_client = APIClient()
        chef_client.force_authenticate(chef)
        r = chef_client.post("/api/material-requests/", {
            "department": "Kitchen", "lines": [{"ingredient": chicken.id, "qty": "2"}],
        }, format="json")
        req_id = r.data["id"]

        rm = User.objects.create_user(username="rmnotif", password="Tk9$mZ2pQw!7",
                                      role="Restaurant Manager")
        rm_client = APIClient()
        rm_client.force_authenticate(rm)
        rm_client.post(f"/api/material-requests/{req_id}/advance/")

        self._login("Store Keeper")
        titles = [a["title"] for a in self.client.get(reverse("notifications")).data["alerts"]]
        self.assertTrue(any("approved — ready to issue" in t for t in titles), titles)

        # The chef who raised it doesn't need the "go issue it" alert.
        titles_chef = [a["title"] for a in chef_client.get("/api/notifications/").data["alerts"]]
        self.assertFalse(any("ready to issue" in t for t in titles_chef))

        # Front Office has matreq access too, but issues nothing.
        self._login("Front Office")
        titles_fo = [a["title"] for a in self.client.get(reverse("notifications")).data["alerts"]]
        self.assertFalse(any("ready to issue" in t for t in titles_fo))

    def test_zero_stock_is_critical_out_of_stock(self):
        """At or below zero the alert escalates: 'Out of stock' + critical —
        the kitchen can't plate the dish, it's not a reorder reminder."""
        Ingredient.objects.create(name="Curd", unit="l", current_stock=Decimal("-2"),
                                  reorder_level=Decimal("10"))
        self._login("General Manager")
        alerts = self.client.get(reverse("notifications")).data["alerts"]
        curd = next(a for a in alerts if "Curd" in a["title"])
        self.assertEqual(curd["severity"], "critical")
        self.assertIn("Out of stock", curd["title"])
        butter = next(a for a in alerts if "Butter" in a["title"])
        self.assertEqual(butter["severity"], "warning")
        self.assertIn("Low stock", butter["title"])

    def test_sensitive_actions_alert_is_rolling_24h(self):
        """Old audit entries don't pile up in the alert forever."""
        from datetime import timedelta
        from django.utils import timezone
        from apps.accounts.models import AuditLog, log_action
        log_action(None, "pos_settle", entity="Order", entity_id="1")
        stale = log_action(None, "pos_settle", entity="Order", entity_id="2")
        AuditLog.objects.filter(pk=stale.pk).update(
            created_at=timezone.now() - timedelta(days=3))
        self._login("General Manager")
        titles = [a["title"] for a in self.client.get(reverse("notifications")).data["alerts"]]
        self.assertTrue(any("1 sensitive action(s) in the last 24h" in t for t in titles), titles)

    def test_approval_inbox_is_role_sliced(self):
        """One inbox, per-role slices: the Restaurant Manager sees the pending
        PO + the Kitchen indent + the Chef's dish; the Store Keeper sees only
        the issue queue; the requester never sees their own indent; a Captain
        (no approver role) can't open the inbox at all."""
        from decimal import Decimal as D
        from apps.matreq.models import MaterialRequest, MaterialRequestLine
        from apps.pos.models import Category, MenuItem
        from apps.procurement.models import PurchaseOrder, PurchaseOrderLine, Supplier

        chicken = Ingredient.objects.create(name="Chicken", unit="kg",
                                            current_stock=D("10"))
        sup = Supplier.objects.create(name="Fresh Farms")
        po = PurchaseOrder.objects.create(supplier=sup)
        PurchaseOrderLine.objects.create(purchase_order=po, ingredient=chicken,
                                         qty=D("5"), rate=D("200"))
        req = MaterialRequest.objects.create(department="Kitchen", requested_by="chefx")
        MaterialRequestLine.objects.create(request=req, ingredient=chicken, qty=D("2"))
        issued = MaterialRequest.objects.create(department="Bar", requested_by="chefx",
                                                status=MaterialRequest.APPROVED)
        MaterialRequestLine.objects.create(request=issued, ingredient=chicken, qty=D("1"))
        cat = Category.objects.create(name="Approvals")
        MenuItem.objects.create(name="Chef Special", category=cat, price=D("400"),
                                approval_status=MenuItem.PENDING)

        self._login("Restaurant Manager")
        data = self.client.get("/api/approvals/").data
        keys = {s["key"] for s in data["sections"]}
        self.assertEqual(keys, {"po", "indents", "issues", "dishes"})
        self.assertEqual(data["count"], 4)

        self._login("Store Keeper")
        data = self.client.get("/api/approvals/").data
        keys = {s["key"] for s in data["sections"]}
        self.assertEqual(keys, {"issues"})  # issues only — store never approves

        # The requester never sees their own indent in the approve queue.
        chef = APIClient()
        chef.force_authenticate(User.objects.create_user(
            username="chefx", password="Tk9$mZ2pQw!7", role="General Manager"))
        data = chef.get("/api/approvals/").data
        indent_ids = [i["id"] for s in data["sections"] if s["key"] == "indents"
                      for i in s["items"]]
        self.assertNotIn(req.id, indent_ids)

        # No approver role → no approvals module → 403.
        self._login("Captain")
        self.assertEqual(self.client.get("/api/approvals/").status_code, 403)

    def test_housekeeping_sees_cleaning_alert_after_checkout(self):
        # A vacated (dirty) room surfaces a housekeeping cleaning alert.
        from apps.rooms.models import Room, RoomType
        rt = RoomType.objects.create(code="STD", name="Standard", base_rate=Decimal("2000"))
        Room.objects.create(number="101", room_type=rt, status=Room.VACANT_DIRTY)
        self._login("Housekeeping")
        resp = self.client.get(reverse("notifications"))
        titles = [a["title"] for a in resp.data["alerts"]]
        self.assertTrue(any("awaiting cleaning" in t for t in titles))
