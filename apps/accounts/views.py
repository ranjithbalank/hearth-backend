from django.utils import timezone
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.views import TokenObtainPairView

from .constants import ALL_MODULES, ROLE_ALLOW, edition_entitlements
from .models import Branch, Entitlement, PasswordReset, Property, User, UserBranchAccess, log_action
from .permissions import ModulePermission, ModuleViewSetMixin
from .serializers import (
    BranchSerializer,
    EntitlementSerializer,
    HearthTokenSerializer,
    PropertySerializer,
    UserBranchAccessSerializer,
    UserSerializer,
)


def get_property():
    prop = Property.objects.select_related("entitlement").first()
    if prop is None:
        prop = Property.objects.create(name="Hearth Property")
        Entitlement.objects.create(property=prop)
    elif not hasattr(prop, "entitlement"):
        Entitlement.objects.create(property=prop)
    return prop


class HearthTokenView(TokenObtainPairView):
    serializer_class = HearthTokenSerializer
    permission_classes = [AllowAny]
    throttle_scope = "auth"  # anti-brute-force (BRD SR-045)

    def post(self, request, *args, **kwargs):
        resp = super().post(request, *args, **kwargs)
        username = request.data.get("username")
        user = User.objects.filter(username=username).first()
        if user:
            log_action(user, "login", entity="User", entity_id=user.id, note="JWT issued")
        return resp


class MeView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        return Response(UserSerializer(request.user).data)


class PropertyView(APIView):
    """Property + entitlement state. Setup is open (AllowAny) for the first-run screen;
    reads require auth otherwise. PATCH edits business/branding details (name, GSTIN,
    currency, document prefixes, aggregator commission) — configuration that belongs to
    settings-capable roles only. It was previously IsAuthenticated, letting any logged-in
    user (a cashier, a captain) rewrite the property's GSTIN/currency/prefixes
    (go-live QA finding CX-RBAC: PATCH /auth/property/)."""

    module = "settings"

    def get_permissions(self):
        if self.request.method == "GET":
            return [AllowAny()]
        return [IsAuthenticated(), ModulePermission()]

    def get(self, request):
        return Response(PropertySerializer(get_property()).data)

    def patch(self, request):
        prop = get_property()
        for f in ["name", "gstin", "address", "phone", "logo", "currency",
                  "doc_header", "doc_footer", "doc_header_align", "doc_footer_align",
                  "pos_doc_header", "pos_doc_footer", "pos_doc_header_align", "pos_doc_footer_align",
                  "invoice_columns", "pos_bill_columns",
                  "zomato_commission_pct", "swiggy_commission_pct",
                  "invoice_prefix", "bill_prefix", "po_prefix", "grn_prefix", "beo_prefix"]:
            if f in request.data:
                setattr(prop, f, request.data[f])
        prop.save()
        log_action(request.user, "property_update", entity="Property", entity_id=prop.id,
                   after={"name": prop.name, "gstin": prop.gstin})
        return Response(PropertySerializer(prop).data)


class SetupView(APIView):
    """One-time property setup: choose edition -> write entitlement record."""

    permission_classes = [AllowAny]

    def post(self, request):
        edition = request.data.get("edition")
        if edition not in {"hotel", "restaurant", "both"}:
            return Response(
                {"detail": "edition must be hotel, restaurant or both"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        prop = get_property()
        prop.edition = edition
        prop.setup_done = True
        if request.data.get("name"):
            prop.name = request.data["name"]
        prop.save()
        flags = edition_entitlements(edition)
        ent = prop.entitlement
        for k, v in flags.items():
            setattr(ent, k, v)
        ent.save()
        log_action(getattr(request, "user", None), "property_setup",
                   entity="Property", entity_id=prop.id, after={"edition": edition})
        return Response(PropertySerializer(prop).data)


class EntitlementView(APIView):
    """Reconfigure entitlements from Settings. Was IsAuthenticated only —
    the docstring claimed server-side enforcement but the permission class
    never actually checked role/module, so any logged-in user (Housekeeping,
    a cashier, a captain) could flip HMS/restaurant/banquets/RMS for the
    whole property (go-live QA finding CX-RBAC-02: PATCH /auth/entitlements/).
    Now gated the same way as /auth/property/: settings-capable roles only."""

    module = "settings"

    def get_permissions(self):
        return [IsAuthenticated(), ModulePermission()]

    def patch(self, request):
        prop = get_property()
        ent = prop.entitlement
        # Switching to Combined hides Bar POS from the nav entirely — any
        # still-open bar tab would become unreachable (and unsettled) the
        # moment this switch lands, so block it until they're cleared.
        if request.data.get("bar_mode") == Entitlement.BAR_COMBINED and ent.bar_mode != Entitlement.BAR_COMBINED:
            from apps.pos.models import Order
            open_bar = Order.objects.filter(
                department=Order.BAR,
                status__in=[Order.OPEN, Order.KOT_FIRED, Order.BILLED],
            ).count()
            if open_bar:
                return Response(
                    {"detail": f"{open_bar} open bar tab(s) — settle them before switching to Combined mode"},
                    status=400,
                )
        before = ent.as_dict()
        ser = EntitlementSerializer(ent, data=request.data, partial=True)
        ser.is_valid(raise_exception=True)
        ser.save()
        log_action(request.user, "entitlement_update", entity="Entitlement",
                   entity_id=ent.id, before=before, after=ent.as_dict())
        return Response(PropertySerializer(get_property()).data)


class PasswordResetRequestView(APIView):
    """Self-service "forgot password?" — step 1. Never reveals whether the
    username exists (username enumeration): always the same generic 200,
    whether or not a matching account (with an email on file) was found.
    Token-credentialed like Invite/Feedback/QR-order, no login, throttled."""

    permission_classes = [AllowAny]
    throttle_scope = "sensitive"

    GENERIC_MESSAGE = "If that account exists, we've sent a reset link to its email."

    def post(self, request):
        from apps.integrations.services import notify

        username = (request.data.get("username") or "").strip()
        user = User.objects.filter(username=username).first() if username else None
        if user and user.email:
            reset = PasswordReset.issue(user)
            link = f"/reset-password?t={reset.token}"
            notify("email", user.email,
                   f"Reset your Hearth password: {link} (expires in 30 minutes)")
            log_action(user, "password_reset_requested", entity="User", entity_id=user.id)
        return Response({"detail": self.GENERIC_MESSAGE})


class PasswordResetConfirmView(APIView):
    """Self-service "forgot password?" — step 2. GET previews link validity
    (no PII in the response — not even the username); POST sets the new
    password and consumes the token."""

    permission_classes = [AllowAny]
    throttle_scope = "sensitive"

    def get(self, request):
        reset = PasswordReset.objects.filter(token=request.query_params.get("t", "")).first()
        if not reset or not reset.is_valid():
            return Response({"detail": "This reset link is invalid or has expired"}, status=404)
        return Response({"valid": True})

    def post(self, request):
        reset = PasswordReset.objects.select_related("user").filter(
            token=request.data.get("t", "")).first()
        if not reset or not reset.is_valid():
            return Response({"detail": "This reset link is invalid or has expired"}, status=404)
        password = request.data.get("password") or ""
        from django.contrib.auth.password_validation import validate_password
        from django.core.exceptions import ValidationError as DjangoValidationError
        try:
            validate_password(password, user=reset.user)
        except DjangoValidationError as e:
            return Response({"detail": " ".join(e.messages)}, status=400)

        user = reset.user
        user.set_password(password)
        user.save(update_fields=["password"])
        reset.used_at = timezone.now()
        reset.save(update_fields=["used_at"])
        log_action(user, "password_reset_completed", entity="User", entity_id=user.id)
        return Response({"detail": "Password updated"})


class UserViewSet(ModuleViewSetMixin, viewsets.ModelViewSet):
    module = "settings"
    queryset = User.objects.all().order_by("role", "username")
    serializer_class = UserSerializer


class BranchViewSet(ModuleViewSetMixin, viewsets.ModelViewSet):
    """Branch Master (BRD 5.1): the group's locations. Each carries its own
    address/GSTIN and edition/entitlement flags — a branch can be
    restaurant-only while another is hotel+restaurant."""

    module = "branchmaster"
    queryset = Branch.objects.select_related("property").all()
    serializer_class = BranchSerializer

    def perform_create(self, serializer):
        branch = serializer.save(property=get_property())
        log_action(self.request.user, "branch_create", entity="Branch", entity_id=branch.id,
                   after={"name": branch.name, "code": branch.code})

    def perform_destroy(self, instance):
        from django.db.models import ProtectedError
        from rest_framework.exceptions import ValidationError
        try:
            instance.delete()
        except ProtectedError:
            raise ValidationError({
                "detail": f"{instance.name} still has rooms or tables assigned to it — "
                          "move or remove those before deleting the branch.",
            })


class UserBranchAccessViewSet(ModuleViewSetMixin, viewsets.ModelViewSet):
    """Staff assignment: which branch a person operates in, and as what role
    there. `?user=<id>` narrows to one person's assignments (the Users screen
    calls it this way); unfiltered lists everyone's, for the Branch Master
    screen's roster view."""

    module = "settings"
    serializer_class = UserBranchAccessSerializer

    def get_queryset(self):
        qs = UserBranchAccess.objects.select_related("branch", "user")
        user_id = self.request.query_params.get("user")
        if user_id:
            qs = qs.filter(user_id=user_id)
        branch_id = self.request.query_params.get("branch")
        if branch_id:
            qs = qs.filter(branch_id=branch_id)
        return qs

    def perform_create(self, serializer):
        access = serializer.save()
        log_action(self.request.user, "branch_access_grant", entity="UserBranchAccess",
                   entity_id=access.id,
                   after={"user": access.user.username, "branch": access.branch.code,
                          "role": access.role})

    def perform_destroy(self, instance):
        log_action(self.request.user, "branch_access_revoke", entity="UserBranchAccess",
                   entity_id=instance.id,
                   before={"user": instance.user.username, "branch": instance.branch.code,
                           "role": instance.role})
        instance.delete()


class MfaSetupView(APIView):
    """Begin TOTP enrolment: returns a secret + otpauth URI for the authenticator app."""

    permission_classes = [IsAuthenticated]

    def post(self, request):
        from . import mfa
        secret = mfa.new_secret()
        request.user.mfa_secret = secret
        request.user.save(update_fields=["mfa_secret"])
        return Response({
            "secret": secret,
            "otpauth_uri": mfa.provisioning_uri(request.user, secret),
        })


class MfaVerifyView(APIView):
    """Confirm a TOTP code to enable MFA on the account."""

    permission_classes = [IsAuthenticated]
    # A 6-digit TOTP is brute-forceable without a rate limit
    # (security review 2026-07, finding B5).
    throttle_scope = "sensitive"

    def post(self, request):
        from . import mfa
        if mfa.verify(request.user.mfa_secret, request.data.get("otp")):
            request.user.mfa_enabled = True
            request.user.save(update_fields=["mfa_enabled"])
            log_action(request.user, "mfa_enabled", entity="User", entity_id=request.user.id)
            return Response({"mfa_enabled": True})
        return Response({"detail": "Invalid code"}, status=status.HTTP_400_BAD_REQUEST)


class MfaDisableView(APIView):
    permission_classes = [IsAuthenticated]
    throttle_scope = "sensitive"

    def post(self, request):
        request.user.mfa_enabled = False
        request.user.mfa_secret = ""
        request.user.save(update_fields=["mfa_enabled", "mfa_secret"])
        log_action(request.user, "mfa_disabled", entity="User", entity_id=request.user.id)
        return Response({"mfa_enabled": False})


class RoleMatrixView(APIView):
    """Editable role × module permission matrix (BRD FR-USR-002 / 5.10).

    GET returns the live matrix (honours RoleConfig overrides). POST toggles a
    module for a role: {role, module, allowed}. Super Admin/MD/GM are protected
    (full access). Only roles with the 'roles' module may view or edit —
    segregation of duties: nobody grants themselves access.
    """

    permission_classes = [IsAuthenticated, ModulePermission]
    module = "roles"

    def get(self, request):
        from .rbac import allowed_modules_for
        roles = list(ROLE_ALLOW.keys())
        allow_by_role = {r: allowed_modules_for(r) for r in roles}
        matrix = []
        for module in ALL_MODULES:
            cells = []
            for role in roles:
                allow = allow_by_role[role]
                cells.append(allow == "*" or module in allow)
            matrix.append({"module": module, "cells": cells})
        from .rbac import PROTECTED
        return Response({"roles": roles, "matrix": matrix, "protected": list(PROTECTED)})

    def post(self, request):
        from .models import RoleConfig
        from .rbac import PROTECTED, allowed_modules_for
        role = request.data.get("role")
        module = request.data.get("module")
        allowed = bool(request.data.get("allowed"))
        if role not in ROLE_ALLOW:
            return Response({"detail": "unknown role"}, status=status.HTTP_400_BAD_REQUEST)
        if role in PROTECTED:
            return Response({"detail": f"{role} always has full access and can't be edited"},
                            status=status.HTTP_400_BAD_REQUEST)
        if module not in ALL_MODULES:
            return Response({"detail": "unknown module"}, status=status.HTTP_400_BAD_REQUEST)
        # Seed the config from the current effective allow-list, then toggle.
        current = allowed_modules_for(role)
        mods = list(current) if isinstance(current, list) else []
        if allowed and module not in mods:
            mods.append(module)
        elif not allowed and module in mods:
            mods.remove(module)
        RoleConfig.objects.update_or_create(role=role, defaults={"modules": mods})
        log_action(request.user, "role_permission", entity="RoleConfig", entity_id=role,
                   after={"module": module, "allowed": allowed})
        return Response({"role": role, "modules": mods})


class AuditLogView(APIView):
    """Read-only trail of security-relevant actions (BRD FR-USR-007 / SR-090):
    who did what, when, with before → after values. The table itself is
    append-only and immutable — this endpoint only ever reads it.

    Filters: ?entity=Department  ?action=master_updated  ?q=<username substring>
    Newest first, capped at `limit` rows (default 200, max 1000)."""

    permission_classes = [IsAuthenticated, ModulePermission]
    module = "settings"

    def get(self, request):
        from .models import AuditLog
        qs = AuditLog.objects.select_related("user")
        entity = request.query_params.get("entity")
        action_ = request.query_params.get("action")
        q = request.query_params.get("q")
        if entity:
            qs = qs.filter(entity=entity)
        if action_:
            qs = qs.filter(action=action_)
        if q:
            qs = qs.filter(user__username__icontains=q)
        try:
            limit = min(max(int(request.query_params.get("limit", 200)), 1), 1000)
        except ValueError:
            limit = 200
        return Response([
            {"id": a.id, "created_at": a.created_at,
             "user": a.user.username if a.user else "system",
             "user_name": (a.user.get_full_name() or a.user.username) if a.user else "System",
             "action": a.action, "entity": a.entity, "entity_id": a.entity_id,
             "before": a.before, "after": a.after, "note": a.note}
            for a in qs[:limit]
        ])
