from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.views import TokenObtainPairView

from .constants import ALL_MODULES, ROLE_ALLOW, edition_entitlements
from .models import Entitlement, Property, User, log_action
from .permissions import ModulePermission, ModuleViewSetMixin
from .serializers import (
    EntitlementSerializer,
    HearthTokenSerializer,
    PropertySerializer,
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
    reads require auth otherwise. PATCH edits the business details (name/GSTIN/address)."""

    def get_permissions(self):
        return [AllowAny()] if self.request.method == "GET" else [IsAuthenticated()]

    def get(self, request):
        return Response(PropertySerializer(get_property()).data)

    def patch(self, request):
        prop = get_property()
        for f in ["name", "gstin", "address", "phone", "logo", "currency"]:
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
    """Reconfigure entitlements from Settings (MD/GM only enforced client+server)."""

    permission_classes = [IsAuthenticated]

    def patch(self, request):
        prop = get_property()
        ent = prop.entitlement
        before = ent.as_dict()
        ser = EntitlementSerializer(ent, data=request.data, partial=True)
        ser.is_valid(raise_exception=True)
        ser.save()
        log_action(request.user, "entitlement_update", entity="Entitlement",
                   entity_id=ent.id, before=before, after=ent.as_dict())
        return Response(PropertySerializer(get_property()).data)


class UserViewSet(ModuleViewSetMixin, viewsets.ModelViewSet):
    module = "settings"
    queryset = User.objects.all().order_by("role", "username")
    serializer_class = UserSerializer


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
