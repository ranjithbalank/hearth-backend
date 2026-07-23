from django.test import TestCase
from django.urls import reverse
from rest_framework.test import APIClient

from apps.accounts.models import User, UserBranchAccess

from .models import Employee, Invite


class InviteIssueTests(TestCase):
    """POST /hr/{id}/invite/ — HR generates a self-onboarding link."""

    def setUp(self):
        self.client = APIClient()
        self.client.force_authenticate(User.objects.create_user(
            username="hrinv", password="Tk9$mZ2pQw!7", role="HR Manager"))
        self.emp = Employee.objects.create(name="Nithya Raman", department="Kitchen", role="Cook")

    def test_invite_generates_token_for_employee_without_login(self):
        r = self.client.post(reverse("hr-invite", args=[self.emp.id]), {"role": "Captain"}, format="json")
        self.assertEqual(r.status_code, 201, r.data)
        self.assertIn("token", r.data)
        self.assertIn("expires_at", r.data)
        self.assertTrue(Invite.objects.filter(employee=self.emp, role="Captain").exists())

    def test_invite_refuses_if_employee_already_has_login(self):
        self.emp.user = User.objects.create_user(username="nithya", password="Tk9$mZ2pQw!7", role="Captain")
        self.emp.save(update_fields=["user"])
        r = self.client.post(reverse("hr-invite", args=[self.emp.id]), {"role": "Captain"}, format="json")
        self.assertEqual(r.status_code, 400)
        self.assertIn("already has a login", r.data["detail"])

    def test_invite_refuses_invalid_role(self):
        r = self.client.post(reverse("hr-invite", args=[self.emp.id]), {"role": "Wizard"}, format="json")
        self.assertEqual(r.status_code, 400)

    def test_invite_refuses_protected_role(self):
        r = self.client.post(reverse("hr-invite", args=[self.emp.id]), {"role": "General Manager"}, format="json")
        self.assertEqual(r.status_code, 400)
        self.assertIn("can't be self-onboarded", r.data["detail"])

    def test_invite_404_for_unknown_employee(self):
        r = self.client.post(reverse("hr-invite", args=[999999]), {"role": "Captain"}, format="json")
        self.assertEqual(r.status_code, 404)

    def test_reissuing_invite_retires_the_prior_one(self):
        first = self.client.post(reverse("hr-invite", args=[self.emp.id]), {"role": "Captain"}, format="json").data
        second = self.client.post(reverse("hr-invite", args=[self.emp.id]), {"role": "Captain"}, format="json").data
        self.assertNotEqual(first["token"], second["token"])
        self.assertEqual(Invite.objects.filter(employee=self.emp, used_at__isnull=True).count(), 1)


class InvitePublicTests(TestCase):
    """GET/POST /public/invite/ — the new hire's self-onboarding form."""

    def setUp(self):
        self.emp = Employee.objects.create(name="Nithya Raman", department="Kitchen", role="Cook")
        self.inv = Invite.issue(employee=self.emp, role="Captain", created_by=None)

    def test_get_returns_employee_and_role_for_valid_token(self):
        r = self.client.get(reverse("invite-public") + f"?t={self.inv.token}")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.data["employee_name"], "Nithya Raman")
        self.assertEqual(r.data["role"], "Captain")

    def test_get_404_for_invalid_token(self):
        r = self.client.get(reverse("invite-public") + "?t=not-a-real-token")
        self.assertEqual(r.status_code, 404)

    def test_get_404_for_expired_token(self):
        from django.utils import timezone
        self.inv.expires_at = timezone.now() - timezone.timedelta(days=1)
        self.inv.save(update_fields=["expires_at"])
        r = self.client.get(reverse("invite-public") + f"?t={self.inv.token}")
        self.assertEqual(r.status_code, 404)

    def test_post_creates_user_and_links_employee(self):
        r = self.client.post(reverse("invite-public"),
                             {"t": self.inv.token, "username": "nithya", "password": "Str0ngPass!9x"},
                             format="json")
        self.assertEqual(r.status_code, 201, r.data)
        self.assertTrue(User.objects.filter(username="nithya").exists())
        self.emp.refresh_from_db()
        self.assertIsNotNone(self.emp.user_id)
        self.inv.refresh_from_db()
        self.assertIsNotNone(self.inv.used_at)

    def test_post_creates_branch_access_when_employee_has_a_branch(self):
        from apps.accounts.models import Branch
        from apps.accounts.views import get_property
        branch = Branch.objects.create(property=get_property(), name="Downtown", code="DTN")
        self.emp.branch = branch
        self.emp.save(update_fields=["branch"])
        self.client.post(reverse("invite-public"),
                         {"t": self.inv.token, "username": "nithya2", "password": "Str0ngPass!9x"},
                         format="json")
        user = User.objects.get(username="nithya2")
        self.assertTrue(UserBranchAccess.objects.filter(user=user, branch=branch).exists())

    def test_post_rejects_duplicate_username(self):
        User.objects.create_user(username="taken", password="Tk9$mZ2pQw!7", role="Captain")
        r = self.client.post(reverse("invite-public"),
                             {"t": self.inv.token, "username": "taken", "password": "Str0ngPass!9x"},
                             format="json")
        self.assertEqual(r.status_code, 400)

    def test_post_rejects_weak_password(self):
        r = self.client.post(reverse("invite-public"),
                             {"t": self.inv.token, "username": "nithya3", "password": "123"},
                             format="json")
        self.assertEqual(r.status_code, 400)

    def test_post_token_is_single_use(self):
        body = {"t": self.inv.token, "username": "nithya4", "password": "Str0ngPass!9x"}
        r1 = self.client.post(reverse("invite-public"), body, format="json")
        self.assertEqual(r1.status_code, 201)
        r2 = self.client.post(reverse("invite-public"),
                             {"t": self.inv.token, "username": "someoneelse", "password": "Str0ngPass!9x"},
                             format="json")
        self.assertEqual(r2.status_code, 404)
