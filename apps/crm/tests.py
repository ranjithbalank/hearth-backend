from django.test import TestCase
from django.urls import reverse
from rest_framework.test import APIClient

from apps.accounts.models import User

from .models import Customer


class CrmApiTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.client.force_authenticate(User.objects.create_user(
            username="g", password="Tk9$mZ2pQw!7", role="General Manager"))
        self.c = Customer.objects.create(name="Asha", mobile="9000000001",
                                         email="asha@example.com")

    def test_lookup_by_mobile(self):
        r = self.client.get(reverse("customer-lookup") + "?mobile=9000000001")
        self.assertTrue(r.data["found"])
        self.assertEqual(r.data["customer"]["name"], "Asha")

    def test_dpdp_export_and_erase(self):
        r = self.client.get(reverse("customer-export", args=[self.c.id]))
        self.assertEqual(r.data["profile"]["mobile"], "9000000001")
        self.client.post(reverse("customer-erase", args=[self.c.id]))
        self.c.refresh_from_db()
        self.assertTrue(self.c.name.startswith("Erased"))
        self.assertNotEqual(self.c.mobile, "9000000001")
        self.assertEqual(self.c.email, "")
