from decimal import Decimal

from django.test import TestCase
from django.urls import reverse
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.inventory.models import Ingredient

from .models import GoodsReceipt, PurchaseOrder, PurchaseOrderLine, Supplier


class ProcurementApiTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.client.force_authenticate(User.objects.create_user(
            username="gm", password="Tk9$mZ2pQw!7", role="General Manager"))
        self.ing = Ingredient.objects.create(name="Rice", unit="kg", current_stock=Decimal("10"))
        self.sup = Supplier.objects.create(name="Metro")
        self.po = PurchaseOrder.objects.create(supplier=self.sup, status=PurchaseOrder.PENDING)
        PurchaseOrderLine.objects.create(purchase_order=self.po, ingredient=self.ing,
                                          qty=Decimal("40"), rate=Decimal("90"))

    def test_po_approval_follows_raiser_authority(self):
        """A spend authority (Restaurant Manager) raising a PO IS the
        approval — it's approved on creation, ready for GRN. A Store Keeper's
        PO queues for an approver, and nobody ever approves their own."""
        rm = APIClient()
        rm.force_authenticate(User.objects.create_user(
            username="rmpo", password="Tk9$mZ2pQw!7", role="Restaurant Manager"))
        r = rm.post("/api/purchase-orders/", {
            "supplier": self.sup.id,
            "lines": [{"ingredient": self.ing.id, "qty": "5", "rate": "90"}],
        }, format="json")
        self.assertEqual(r.status_code, 201, r.data)
        self.assertEqual(r.data["status"], "approved")   # auto-approved
        self.assertEqual(r.data["requested_by"], "rmpo")

        # Store Keeper's PO waits for a real approver…
        store = APIClient()
        store.force_authenticate(User.objects.create_user(
            username="storepo", password="Tk9$mZ2pQw!7", role="Store Keeper"))
        r = store.post("/api/purchase-orders/", {
            "supplier": self.sup.id,
            "lines": [{"ingredient": self.ing.id, "qty": "2", "rate": "90"}],
        }, format="json")
        self.assertEqual(r.data["status"], "pending")
        po_id = r.data["id"]
        # …the store itself can't approve (not a spend authority)…
        self.assertEqual(store.post(reverse("po-approve", args=[po_id])).status_code, 403)
        # …and it shows in the RM's approvals inbox, who signs it off.
        inbox = rm.get("/api/approvals/").data
        po_ids = [i["id"] for s in inbox["sections"] if s["key"] == "po" for i in s["items"]]
        self.assertIn(po_id, po_ids)
        r = rm.post(reverse("po-approve", args=[po_id]))
        self.assertEqual(r.data["status"], "approved")

        # A legacy pending PO raised by an approver still can't self-approve.
        stale = PurchaseOrder.objects.create(supplier=self.sup, requested_by="gm")
        r = self.client.post(reverse("po-approve", args=[stale.id]))
        self.assertEqual(r.status_code, 403)
        self.assertIn("someone else", r.data["detail"])

    def test_supplier_import(self):
        """Bulk supplier onboarding via CSV: template + per-row report."""
        from io import BytesIO
        r = self.client.get("/api/suppliers/import/")
        self.assertEqual(r.status_code, 200)
        self.assertIn("name,gstin", r.content.decode())
        body = ("name,gstin,contact,payment_terms,lead_time_days\n"
                "Metro,,x,Net 30,3\n"                      # exists → skipped
                "Green Valley Farms,29ABCDE1234F1Z5,9876500000,Net 15,2\n")
        f = BytesIO(body.encode())
        f.name = "suppliers.csv"
        r = self.client.post("/api/suppliers/import/", {"file": f}, format="multipart")
        self.assertEqual(r.status_code, 200, r.data)
        self.assertEqual(r.data["created"], 1)
        self.assertEqual(r.data["skipped_existing"], ["Metro"])
        s = Supplier.objects.get(name="Green Valley Farms")
        self.assertEqual(s.lead_time_days, 2)

    def test_supplier_master_edit(self):
        """The supplier master is editable: terms/lead time/rating change,
        duplicate names are refused, rating stays within 0–5."""
        r = self.client.patch(f"/api/suppliers/{self.sup.id}/", {
            "payment_terms": "Net 30", "lead_time_days": 5, "rating": "4.5",
            "contact": "9876543210",
        }, format="json")
        self.assertEqual(r.status_code, 200, r.data)
        self.sup.refresh_from_db()
        self.assertEqual(self.sup.payment_terms, "Net 30")
        self.assertEqual(self.sup.lead_time_days, 5)
        self.assertEqual(self.sup.rating, Decimal("4.5"))

        Supplier.objects.create(name="Fresh Farms")
        r = self.client.patch(f"/api/suppliers/{self.sup.id}/", {"name": "fresh farms"},
                              format="json")
        self.assertEqual(r.status_code, 400)  # case-insensitive duplicate

        r = self.client.patch(f"/api/suppliers/{self.sup.id}/", {"rating": "9"}, format="json")
        self.assertEqual(r.status_code, 400)

        from apps.accounts.models import AuditLog
        self.assertTrue(AuditLog.objects.filter(
            action="supplier_updated", entity_id=str(self.sup.id)).exists())

    def test_approve_then_receive_updates_stock(self):
        r = self.client.post(reverse("po-approve", args=[self.po.id]))
        self.assertEqual(r.data["status"], "approved")
        r = self.client.post(reverse("po-receive", args=[self.po.id]))
        self.assertEqual(r.data["status"], "received")
        self.ing.refresh_from_db()
        self.assertEqual(self.ing.current_stock, Decimal("50.000"))  # 10 + 40
        self.assertTrue(GoodsReceipt.objects.filter(purchase_order=self.po).exists())

    def test_partial_receipt_keeps_po_open_until_closed(self):
        """A short delivery books only what arrived: stock rises by the
        received qty, the PO stays approved with the remainder outstanding,
        and a second receipt (its own GRN) closes it."""
        self.client.post(reverse("po-approve", args=[self.po.id]))
        line = self.po.lines.first()

        r = self.client.post(reverse("po-receive", args=[self.po.id]),
                             {"lines": [{"line": line.id, "qty": "25"}]}, format="json")
        self.assertEqual(r.status_code, 200, r.data)
        self.assertEqual(r.data["status"], "approved")   # still open
        self.assertEqual(r.data["lines"][0]["received_qty"], "25.000")
        self.ing.refresh_from_db()
        self.assertEqual(self.ing.current_stock, Decimal("35.000"))  # 10 + 25

        # Over-receiving the remainder is refused.
        r = self.client.post(reverse("po-receive", args=[self.po.id]),
                             {"lines": [{"line": line.id, "qty": "20"}]}, format="json")
        self.assertEqual(r.status_code, 400)
        self.assertIn("outstanding", r.data["detail"])

        # Receiving the remaining 15 closes the PO; two GRNs on file.
        r = self.client.post(reverse("po-receive", args=[self.po.id]),
                             {"lines": [{"line": line.id, "qty": "15"}]}, format="json")
        self.assertEqual(r.data["status"], "received")
        self.ing.refresh_from_db()
        self.assertEqual(self.ing.current_stock, Decimal("50.000"))
        self.assertEqual(GoodsReceipt.objects.filter(purchase_order=self.po).count(), 2)

        # A zero-quantity receipt is meaningless.
        po2 = PurchaseOrder.objects.create(supplier=self.sup, status=PurchaseOrder.APPROVED)
        line2 = PurchaseOrderLine.objects.create(purchase_order=po2, ingredient=self.ing,
                                                 qty=Decimal("5"), rate=Decimal("90"))
        r = self.client.post(reverse("po-receive", args=[po2.id]),
                             {"lines": [{"line": line2.id, "qty": "0"}]}, format="json")
        self.assertEqual(r.status_code, 400)

    def test_receipt_stores_bill_photo(self):
        """A photo of the supplier's bill rides along with the GRN; junk that
        isn't an image is refused; the image comes back from /bill/."""
        pixel = ("data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAf"
                 "FcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg==")
        self.client.post(reverse("po-approve", args=[self.po.id]))
        r = self.client.post(reverse("po-receive", args=[self.po.id]),
                             {"bill_image": "data:text/html,<script>x</script>"}, format="json")
        self.assertEqual(r.status_code, 400)
        r = self.client.post(reverse("po-receive", args=[self.po.id]),
                             {"bill_image": pixel, "note": "all good"}, format="json")
        self.assertEqual(r.status_code, 200, r.data)
        grn = GoodsReceipt.objects.filter(purchase_order=self.po).latest("created_at")
        self.assertEqual(grn.bill_image, pixel)
        self.assertEqual(r.data["grns"][0]["has_bill"], True)
        b = self.client.get(f"/api/goods-receipts/{grn.id}/bill/")
        self.assertEqual(b.status_code, 200)
        self.assertEqual(b.data["bill_image"], pixel)

    def test_cannot_receive_before_approval(self):
        r = self.client.post(reverse("po-receive", args=[self.po.id]))
        self.assertEqual(r.status_code, 400)

    def test_create_po_via_api_and_rate_defaults(self):
        """POs can be raised from the app; rate falls back to the material's cost."""
        self.ing.unit_cost = Decimal("85")
        self.ing.save()
        r = self.client.post("/api/purchase-orders/", {
            "supplier": self.sup.id,
            "lines": [{"ingredient": self.ing.id, "qty": "20"}],   # no rate given
        }, format="json")
        self.assertEqual(r.status_code, 201, r.data)
        po = PurchaseOrder.objects.get(pk=r.data["id"])
        # GM is a spend authority — their PO is approved on creation.
        self.assertEqual(po.status, PurchaseOrder.APPROVED)
        self.assertEqual(po.lines.get().rate, Decimal("85"))
        # Validation: unknown material / empty lines / bad qty.
        self.assertEqual(self.client.post("/api/purchase-orders/", {
            "supplier": self.sup.id, "lines": []}, format="json").status_code, 400)
        self.assertEqual(self.client.post("/api/purchase-orders/", {
            "supplier": self.sup.id,
            "lines": [{"ingredient": self.ing.id, "qty": "-1"}]}, format="json").status_code, 400)

    def test_po_approval_is_a_manager_decision(self):
        """Store keeper raises but can't approve (spend control); the
        Restaurant Manager can — then the store receives."""
        store = APIClient()
        store.force_authenticate(User.objects.create_user(
            username="storepo", password="Tk9$mZ2pQw!7", role="Store Keeper"))
        r = store.post("/api/purchase-orders/", {
            "supplier": self.sup.id,
            "lines": [{"ingredient": self.ing.id, "qty": "5", "rate": "80"}],
        }, format="json")
        self.assertEqual(r.status_code, 201)
        po_id = r.data["id"]
        self.assertEqual(store.post(f"/api/purchase-orders/{po_id}/approve/").status_code, 403)
        rm = APIClient()
        rm.force_authenticate(User.objects.create_user(
            username="rmpo", password="Tk9$mZ2pQw!7", role="Restaurant Manager"))
        self.assertEqual(rm.post(f"/api/purchase-orders/{po_id}/approve/").status_code, 200)
        self.assertEqual(store.post(f"/api/purchase-orders/{po_id}/receive/").status_code, 200)
        self.ing.refresh_from_db()
        self.assertEqual(self.ing.current_stock, Decimal("15.000"))  # 10 + 5

    def test_finance_approves_but_never_creates_or_receives(self):
        """Finance reaches POs via the "pomanage" door (AnyModuleViewSetMixin)
        and sits in PO_APPROVER_ROLES — but must never be able to raise or
        receive a PO itself (PO_HANDLER_ROLES), or approval stops meaning
        anything: the same role would be both sides of the spend decision."""
        finance = APIClient()
        finance.force_authenticate(User.objects.create_user(
            username="financepo", password="Tk9$mZ2pQw!7", role="Finance"))
        r = finance.post("/api/purchase-orders/", {
            "supplier": self.sup.id,
            "lines": [{"ingredient": self.ing.id, "qty": "5", "rate": "80"}],
        }, format="json")
        self.assertEqual(r.status_code, 403)
        # But Finance CAN approve one someone else raised.
        self.assertEqual(finance.post(f"/api/purchase-orders/{self.po.id}/approve/").status_code, 200)
        self.assertEqual(finance.post(f"/api/purchase-orders/{self.po.id}/receive/").status_code, 403)

    def test_grn_recosts_material_weighted_average(self):
        """Receiving at a new rate re-costs held stock: (10 kg @60 + 40 kg @90)/50 = 84."""
        self.ing.unit_cost = Decimal("60")
        self.ing.save()
        self.client.post(reverse("po-approve", args=[self.po.id]))
        self.client.post(reverse("po-receive", args=[self.po.id]))
        self.ing.refresh_from_db()
        self.assertEqual(self.ing.current_stock, Decimal("50.000"))
        self.assertEqual(self.ing.unit_cost, Decimal("84.00"))

    def test_finance_can_view_and_approve_pos(self):
        """Finance sits in PO_APPROVER_ROLES and its Purchase Orders screen
        gates on "pomanage" — but the viewset used to gate on "procurement"
        alone, so Finance was a designated approver who 403'd on every PO
        endpoint. Regression: Finance can list and approve, and the approve
        guard still rejects non-approver roles that also carry pomanage."""
        fin = APIClient()
        fin.force_authenticate(User.objects.create_user(
            username="finpo", password="Tk9$mZ2pQw!7", role="Finance"))
        r = fin.get("/api/purchase-orders/")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(fin.post(f"/api/purchase-orders/{self.po.id}/approve/").status_code, 200)
