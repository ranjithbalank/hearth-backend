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
