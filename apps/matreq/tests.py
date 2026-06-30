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
        self.client.force_authenticate(User.objects.create_user(
            username="c", password="Tk9$mZ2pQw!7", role="F&B Cashier"))
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
