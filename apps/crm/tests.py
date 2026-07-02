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


class CampaignTests(TestCase):
    def setUp(self):
        from apps.accounts.models import User
        from rest_framework.test import APIClient
        self.client = APIClient()
        self.client.force_authenticate(User.objects.create_user(
            username="gmx", password="Tk9$mZ2pQw!7", role="General Manager"))
        Customer.objects.create(name="Consented", mobile="9111111111",
                                marketing_consent=True, loyalty_points=50)
        Customer.objects.create(name="NoConsent", mobile="9222222222", marketing_consent=False)

    def test_campaign_respects_consent_and_fills_placeholders(self):
        from django.urls import reverse

        from apps.integrations.models import SentMessage
        r = self.client.post(reverse("customer-campaign"),
                             {"segment": "all", "channel": "sms",
                              "message": "Hi {name}, you have {points} points!"}, format="json")
        self.assertEqual(r.status_code, 201)
        self.assertEqual(r.data["sent"], 1)      # only the consented customer
        self.assertEqual(r.data["skipped"], 1)
        msg = SentMessage.objects.latest("id")
        self.assertIn("Consented", msg.body)
        self.assertIn("50 points", msg.body)
        # History endpoint lists it.
        r = self.client.get(reverse("customer-campaigns"))
        self.assertEqual(r.data[0]["sent_count"], 1)
