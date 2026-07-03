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

    def test_chef_requests_store_approves_and_issues(self):
        """The full indent workflow with segregation of duties."""
        chef = APIClient()
        chef.force_authenticate(User.objects.create_user(
            username="chefmr", password="Tk9$mZ2pQw!7", role="Chef / Kitchen"))
        r = chef.post("/api/material-requests/", {
            "department": "Kitchen",
            "lines": [{"ingredient": self.ing.id, "qty": "4"}],
        }, format="json")
        self.assertEqual(r.status_code, 201, r.data)
        self.assertEqual(r.data["requested_by"], "chefmr")
        req_id = r.data["id"]
        # The chef can't approve their own indent…
        r = chef.post(f"/api/material-requests/{req_id}/advance/")
        self.assertEqual(r.status_code, 403)
        # …the store keeper approves and issues it.
        store = APIClient()
        store.force_authenticate(User.objects.create_user(
            username="storemr", password="Tk9$mZ2pQw!7", role="Store Keeper"))
        self.assertEqual(store.post(f"/api/material-requests/{req_id}/advance/").data["status"],
                         "approved")
        self.assertEqual(store.post(f"/api/material-requests/{req_id}/advance/").data["status"],
                         "issued")
        self.ing.refresh_from_db()
        self.assertEqual(self.ing.current_stock, Decimal("16.000"))  # 20 − 4

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
