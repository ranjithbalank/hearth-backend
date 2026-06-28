from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse
from rest_framework.test import APIClient

from .models import AuditLog, log_action
from .security import BreachedPasswordValidator, mask_pii


class SecurityHeadersTests(TestCase):
    def test_headers_present_on_response(self):
        resp = self.client.get("/api/health/")
        self.assertEqual(resp["X-Content-Type-Options"], "nosniff")
        self.assertEqual(resp["X-Frame-Options"], "DENY")
        self.assertIn("Content-Security-Policy", resp)
        self.assertIn("Referrer-Policy", resp)


class ThrottleTests(TestCase):
    def test_auth_endpoint_throttles_brute_force(self):
        client = APIClient()
        url = reverse("token_obtain_pair")
        statuses = []
        for _ in range(12):
            r = client.post(url, {"username": "nobody", "password": "wrong"}, format="json")
            statuses.append(r.status_code)
        # 'auth' scope is 10/min, so at least one later attempt must be rate-limited.
        self.assertIn(429, statuses)


class BreachedPasswordTests(TestCase):
    def test_rejects_known_breached(self):
        with self.assertRaises(ValidationError):
            BreachedPasswordValidator().validate("password")

    def test_allows_strong(self):
        BreachedPasswordValidator().validate("Tk9$mZ2pQw!7")


class PiiMaskingTests(TestCase):
    def test_masks_email_and_phone(self):
        masked = mask_pii("contact a.guest@example.com or 9876543210")
        self.assertNotIn("a.guest@example.com", masked)
        self.assertNotIn("9876543210", masked)


class AuditImmutabilityTests(TestCase):
    def test_audit_entries_are_append_only(self):
        entry = log_action(None, "test_action", note="x")
        entry.note = "tampered"
        with self.assertRaises(PermissionError):
            entry.save()
        with self.assertRaises(PermissionError):
            entry.delete()
        self.assertEqual(AuditLog.objects.get(pk=entry.pk).note, "x")
