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

    def test_list_annotates_hotel_vs_restaurant_activity(self):
        """The customer list carries stay/order counts so the CRM screen can
        filter hotel guests apart from restaurant diners."""
        r = self.client.get(reverse("customer-list"))
        row = next(c for c in r.data if c["id"] == self.c.id)
        self.assertEqual(row["stay_count"], 0)
        self.assertEqual(row["order_count"], 0)

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

    def test_erase_wipes_dob_and_anniversary(self):
        from datetime import date
        self.c.date_of_birth = date(1990, 5, 1)
        self.c.anniversary_date = date(2015, 6, 1)
        self.c.save(update_fields=["date_of_birth", "anniversary_date"])
        self.client.post(reverse("customer-erase", args=[self.c.id]))
        self.c.refresh_from_db()
        self.assertIsNone(self.c.date_of_birth)
        self.assertIsNone(self.c.anniversary_date)

    def test_export_includes_loyalty_ledger(self):
        from .models import LoyaltyLedger
        LoyaltyLedger.objects.create(customer=self.c, kind=LoyaltyLedger.EARN, points=10, balance_after=10)
        r = self.client.get(reverse("customer-export", args=[self.c.id]))
        self.assertEqual(len(r.data["loyalty_ledger"]), 1)
        self.assertEqual(r.data["loyalty_ledger"][0]["points"], 10)

    def test_erase_refused_while_money_is_owed(self):
        """A debtor can't be anonymised — identity is retained for the claim
        (DPDP permits this); settle or write off first, then erase."""
        from decimal import Decimal
        self.c.outstanding = Decimal("5000")
        self.c.save(update_fields=["outstanding"])
        r = self.client.post(reverse("customer-erase", args=[self.c.id]))
        self.assertEqual(r.status_code, 400)
        self.assertIn("outstanding", r.data["detail"])
        self.c.refresh_from_db()
        self.assertEqual(self.c.name, "Asha")   # untouched
        # Collect the balance, then erasure goes through.
        self.client.post(reverse("customer-settle-ar", args=[self.c.id]),
                         {"amount": "5000"}, format="json")
        r = self.client.post(reverse("customer-erase", args=[self.c.id]))
        self.assertEqual(r.status_code, 200)
        self.c.refresh_from_db()
        self.assertTrue(self.c.name.startswith("Erased"))


class LoyaltyTierAndRewardTests(TestCase):
    """Tiers/rewards CRUD (Settings-gated, same MasterViewSet shape as
    Kitchen Stations/Payment Methods) + the earn-multiplier they drive."""

    def setUp(self):
        self.gm = APIClient()
        self.gm.force_authenticate(User.objects.create_user(
            username="gmtier", password="Tk9$mZ2pQw!7", role="General Manager"))
        self.captain = APIClient()
        self.captain.force_authenticate(User.objects.create_user(
            username="captier", password="Tk9$mZ2pQw!7", role="Captain"))

    def test_tier_crud_is_settings_gated_but_readable_by_any_role(self):
        # Any authenticated role can read the tier list (POS settle needs it).
        r = self.captain.get(reverse("loyalty-tier-list"))
        self.assertEqual(r.status_code, 200)
        # Only a settings-capable role can create one.
        r = self.captain.post(reverse("loyalty-tier-list"),
                              {"name": "Gold", "min_lifetime_points": 500, "earn_multiplier": "2.00"},
                              format="json")
        self.assertEqual(r.status_code, 403)
        r = self.gm.post(reverse("loyalty-tier-list"),
                         {"name": "Gold", "min_lifetime_points": 500, "earn_multiplier": "2.00"},
                         format="json")
        self.assertEqual(r.status_code, 201, r.data)

    def test_customer_current_tier_picks_highest_qualifying_threshold(self):
        from .models import LoyaltyTier
        LoyaltyTier.objects.create(name="Silver", min_lifetime_points=100, earn_multiplier="1.50")
        LoyaltyTier.objects.create(name="Gold", min_lifetime_points=500, earn_multiplier="2.00")
        cust = Customer.objects.create(name="Tiered", mobile="9333333333", lifetime_points=600)
        self.assertEqual(cust.current_tier().name, "Gold")
        cust.lifetime_points = 200
        self.assertEqual(cust.current_tier().name, "Silver")
        cust.lifetime_points = 0
        self.assertEqual(cust.current_tier().name, "Base")   # seeded default, 0 pts / 1.0x

    def test_reward_redeem_blocked_when_in_use(self):
        from .models import LoyaltyReward
        reward = LoyaltyReward.objects.create(name="₹50 off", points_cost=500, kind="fixed", value="50")
        from apps.pos.models import Category, MenuItem, Order, OrderLine, Table
        cat = Category.objects.create(name="Main")
        item = MenuItem.objects.create(name="Thali", category=cat, price="200")
        table = Table.objects.create(name="RT1", section="AC", seats=4)
        cust = Customer.objects.create(name="Redeemer", mobile="9444444444", loyalty_points=500)
        order = Order.objects.create(mode=Order.DINEIN, table=table, status=Order.KOT_FIRED, customer=cust,
                                     loyalty_redeemed=50, loyalty_reward=reward)
        OrderLine.objects.create(order=order, menu_item=item, qty=1, unit_price="200", kot_fired=True)
        r = self.gm.delete(reverse("loyalty-reward-detail", args=[reward.id]))
        self.assertEqual(r.status_code, 400)
        self.assertIn("used by", r.data["detail"])


class BirthdayAnniversaryCommandTests(TestCase):
    """The daily greeting command: consent-gated, bonus points, deduped."""

    def test_sends_birthday_bonus_once_per_day(self):
        from datetime import date

        from django.core.management import call_command
        from django.utils import timezone

        from apps.integrations.models import SentMessage

        from .models import LoyaltyLedger
        today = timezone.localdate()
        cust = Customer.objects.create(
            name="Birthday Guest", mobile="9555555555", marketing_consent=True,
            date_of_birth=date(1990, today.month, today.day))
        call_command("send_loyalty_greetings")
        cust.refresh_from_db()
        self.assertEqual(cust.loyalty_points, 50)
        self.assertEqual(cust.lifetime_points, 50)
        self.assertEqual(LoyaltyLedger.objects.filter(customer=cust, kind="bonus").count(), 1)
        self.assertTrue(SentMessage.objects.filter(to="9555555555").exists())
        # Re-running the same day must not double-pay.
        call_command("send_loyalty_greetings")
        cust.refresh_from_db()
        self.assertEqual(cust.loyalty_points, 50)

    def test_no_consent_no_greeting(self):
        from datetime import date

        from django.core.management import call_command
        from django.utils import timezone
        today = timezone.localdate()
        cust = Customer.objects.create(
            name="No Consent", mobile="9666666666", marketing_consent=False,
            date_of_birth=date(1990, today.month, today.day))
        call_command("send_loyalty_greetings")
        cust.refresh_from_db()
        self.assertEqual(cust.loyalty_points, 0)


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
