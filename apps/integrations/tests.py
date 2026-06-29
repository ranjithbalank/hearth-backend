from decimal import Decimal

from django.test import TestCase
from django.urls import reverse
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.crm.models import Customer
from apps.pos.models import Category, MenuItem, Order, OrderLine

from . import services
from .models import Otp, SentMessage


class PaymentTests(TestCase):
    def test_gateway_charge_approves_with_token(self):
        r = services.charge_card(Decimal("500"), token="tok_abc", reference="order 1")
        self.assertEqual(r["status"], "approved")
        self.assertTrue(r["ref"].startswith("MOCK-"))

    def test_gateway_charge_needs_token(self):
        r = services.charge_card(Decimal("500"), token="", reference="x")
        self.assertEqual(r["status"], "failed")


class GatewaySettleTests(TestCase):
    def test_pos_settle_via_gateway_records_ref_no_card_data(self):
        cat = Category.objects.create(name="Main")
        item = MenuItem.objects.create(name="X", category=cat, price=Decimal("400"), gst_rate=Decimal("5"))
        cust = Customer.objects.create(name="C", mobile="9999999999")
        order = Order.objects.create(mode=Order.DINEIN, customer=cust)
        OrderLine.objects.create(order=order, menu_item=item, qty=1, unit_price=Decimal("400"))
        client = APIClient()
        client.force_authenticate(User.objects.create_user(
            username="m", password="Tk9$mZ2pQw!7", role="General Manager"))
        r = client.post(reverse("order-settle", args=[order.id]),
                        {"tender": "Gateway", "token": "tok_visa"}, format="json")
        self.assertEqual(r.status_code, 200)
        from apps.frontoffice.models import Settlement
        s = Settlement.objects.latest("id")
        self.assertEqual(s.tender, "Gateway")
        self.assertTrue(s.reference.startswith("MOCK-"))
        # Receipt message was sent to the customer.
        self.assertTrue(SentMessage.objects.filter(to="9999999999").exists())


class OtpTests(TestCase):
    def test_otp_issue_and_verify(self):
        otp = services.send_otp("9876543210")
        self.assertTrue(SentMessage.objects.filter(to="9876543210").exists())
        self.assertFalse(services.verify_otp("9876543210", "000000"))
        self.assertTrue(services.verify_otp("9876543210", otp.code))
        # Second verify of the same code fails (single use).
        self.assertFalse(services.verify_otp("9876543210", otp.code))
