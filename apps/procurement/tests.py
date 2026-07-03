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

    def test_approve_then_receive_updates_stock(self):
        r = self.client.post(reverse("po-approve", args=[self.po.id]))
        self.assertEqual(r.data["status"], "approved")
        r = self.client.post(reverse("po-receive", args=[self.po.id]))
        self.assertEqual(r.data["status"], "received")
        self.ing.refresh_from_db()
        self.assertEqual(self.ing.current_stock, Decimal("50.000"))  # 10 + 40
        self.assertTrue(GoodsReceipt.objects.filter(purchase_order=self.po).exists())

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
        self.assertEqual(po.status, PurchaseOrder.PENDING)
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

    def test_grn_recosts_material_weighted_average(self):
        """Receiving at a new rate re-costs held stock: (10 kg @60 + 40 kg @90)/50 = 84."""
        self.ing.unit_cost = Decimal("60")
        self.ing.save()
        self.client.post(reverse("po-approve", args=[self.po.id]))
        self.client.post(reverse("po-receive", args=[self.po.id]))
        self.ing.refresh_from_db()
        self.assertEqual(self.ing.current_stock, Decimal("50.000"))
        self.assertEqual(self.ing.unit_cost, Decimal("84.00"))
