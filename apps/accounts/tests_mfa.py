import pyotp
from django.test import TestCase
from django.urls import reverse
from rest_framework.test import APIClient

from . import mfa
from .models import User


class MfaFlowTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="mgr", password="Tk9$mZ2pQw!7", role="General Manager")
        self.client = APIClient()

    def _token(self, **extra):
        return self.client.post(reverse("token_obtain_pair"),
                                {"username": "mgr", "password": "Tk9$mZ2pQw!7", **extra},
                                format="json")

    def test_login_without_mfa_when_disabled(self):
        self.assertEqual(self._token().status_code, 200)

    def test_login_requires_otp_when_enabled(self):
        secret = mfa.new_secret()
        self.user.mfa_secret = secret
        self.user.mfa_enabled = True
        self.user.save()
        # Missing OTP -> rejected
        self.assertEqual(self._token().status_code, 400)
        # Valid OTP -> accepted
        code = pyotp.TOTP(secret).now()
        self.assertEqual(self._token(otp=code).status_code, 200)

    def test_verify_enables_mfa(self):
        self.client.force_authenticate(self.user)
        secret = self.client.post(reverse("mfa-setup")).data["secret"]
        code = pyotp.TOTP(secret).now()
        resp = self.client.post(reverse("mfa-verify"), {"otp": code}, format="json")
        self.assertTrue(resp.data["mfa_enabled"])
        self.user.refresh_from_db()
        self.assertTrue(self.user.mfa_enabled)
