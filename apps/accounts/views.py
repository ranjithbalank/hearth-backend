import re

from django.db import models
from django.utils import timezone
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.views import TokenObtainPairView

from .constants import ALL_MODULES, LICENSED_FLAGS, ROLE_ALLOW, ROLE_SUPER_ADMIN, edition_entitlements
from .rbac import (
    allowed_modules_for,
    assignable_roles,
    base_role,
    can_assign_role,
    role_names_for,
    role_rank,
)
from .models import (
    Branch,
    Entitlement,
    PasswordReset,
    Property,
    Role,
    User,
    UserBranchAccess,
    log_action,
)
from .permissions import ModulePermission, ModuleViewSetMixin
from .serializers import (
    BranchSerializer,
    EntitlementSerializer,
    HearthTokenSerializer,
    PropertySerializer,
    RoleSerializer,
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

    # A document number is PREFIX-YYYYMM-NNNNN (numbering.py) = len(prefix)+13.
    # Rule 46(b) caps a tax-invoice number at 16 characters, so the prefix on
    # the two statutory series can be at most 3. The field itself allows 12,
    # which silently produced 25-character numbers that breach the rule; the
    # internal series (PO/GRN/BEO) aren't tax documents and stay unrestricted.
    STATUTORY_PREFIXES = {"invoice_prefix": "guest invoice", "bill_prefix": "POS bill"}
    MAX_PREFIX = 3

    def patch(self, request):
        prop = get_property()
        for field, label in self.STATUTORY_PREFIXES.items():
            value = (request.data.get(field) or "").strip()
            if value and len(value) > self.MAX_PREFIX:
                return Response(
                    {"detail": f"The {label} prefix can be at most {self.MAX_PREFIX} characters "
                               f"— a GST invoice number may not exceed 16 in total "
                               f"(PREFIX-YYYYMM-NNNNN).",
                     "field": field},
                    status=status.HTTP_400_BAD_REQUEST,
                )
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


class FeatureModelView(APIView):
    """The static feature model — labels, groups, prerequisites, which are
    toggleable — for the Settings > Features admin screen. AllowAny GET (it's
    structural metadata, no property data), same as the property read."""

    permission_classes = [AllowAny]

    def get(self, request):
        from .features import feature_model
        return Response({"features": feature_model()})


def _branch_code(name: str) -> str:
    """A short unique code for a branch, derived from its name (SEA, SEA2…).

    `Branch.code` is unique and lands in the invoice series, so two locations can
    never collide on a number — it has to be generated, not guessed.
    """
    base = re.sub(r"[^A-Za-z0-9]", "", name).upper()[:3] or "MAIN"
    code, n = base, 1
    while Branch.objects.filter(code=code).exists():
        n += 1
        code = f"{base}{n}"[:10]
    return code


class SetupView(APIView):
    """First-run setup, run once by the owner on their own property.

    The EDITION is deliberately not settable here. It is what the customer
    bought, so Hearth provisions it before handover (`manage.py provision`) and
    the wizard shows it read-only. This endpoint only records the business
    details the owner fills in, opens their first branch, and flips setup_done.

    It was AllowAny with no completion guard, which meant anyone — logged out,
    from anywhere — could POST {"edition": "both"} and unlock the full hotel
    suite on a restaurant-only licence. Now guarded three ways: owner account
    only, refuses once setup is complete, and ignores any edition/entitlement
    keys in the payload.
    """

    permission_classes = [IsAuthenticated]

    # What the first-run wizard is allowed to write. `edition` and the four
    # LICENSED_FLAGS are absent on purpose — see the docstring.
    FIELDS = ["name", "address", "phone", "gstin", "currency", "gst_billing_mode",
              "default_country_code"]

    def post(self, request):
        if not request.user.is_superuser:
            return Response({"detail": "Only the owner account can run first-time setup."},
                            status=status.HTTP_403_FORBIDDEN)
        prop = get_property()
        if prop.setup_done:
            return Response({"detail": "Setup is already complete."},
                            status=status.HTTP_409_CONFLICT)
        if prop.edition not in {"hotel", "restaurant", "both"}:
            return Response(
                {"detail": "This install has not been provisioned with an edition yet — "
                           "contact Hearth to activate it."},
                status=status.HTTP_409_CONFLICT,
            )
        # Route the payload through the serializer's own field rules rather than
        # writing it raw — setup was the one path that could seed a property
        # with a phone number nothing had validated.
        submitted = {k: request.data[k] for k in self.FIELDS if request.data.get(k)}
        checked = PropertySerializer(prop, data=submitted, partial=True)
        checked.is_valid(raise_exception=True)
        for field, value in checked.validated_data.items():
            setattr(prop, field, value)
        prop.setup_done = True
        if not prop.business_date:
            prop.business_date = timezone.localdate()
        prop.save()
        branch = self._first_branch(prop, request.data)
        log_action(request.user, "property_setup", entity="Property", entity_id=prop.id,
                   after={"edition": prop.edition, "name": prop.name, "branch": branch.code})
        return Response(PropertySerializer(prop).data)

    def _first_branch(self, prop, data):
        """Open the property's first location. Setup used to leave the customer
        with zero branches, so every branch-scoped screen came up empty on the
        very first login."""
        existing = prop.branches.first()
        if existing:
            return existing
        name = (data.get("branch_name") or prop.name or "Main").strip()
        code = (data.get("branch_code") or "").strip().upper() or _branch_code(name)
        return Branch.objects.create(
            property=prop, name=name, code=code,
            address=prop.address, gstin=prop.gstin,
            city=(data.get("city") or "").strip(),
            state=(data.get("state") or "").strip(),
            edition=prop.edition, status=Branch.STATUS_ACTIVE,
            **edition_entitlements(prop.edition),
        )


class SetupChecklistView(APIView):
    """What setup still needs, and the starter packs on offer for this licence.

    Computed from live rows rather than a stored step counter, so it stays
    honest when the owner imports a spreadsheet, does things out of order, or
    deletes something later. `blocking` is the subset that genuinely stops them
    operating — everything else can wait, which is what keeps this a checklist
    rather than a twenty-step wall.
    """

    permission_classes = [IsAuthenticated]

    def get(self, request):
        from .onboarding import checklist, pack_catalog

        prop = get_property()
        return Response({"checklist": checklist(prop), "packs": pack_catalog(prop)})


class StarterPackView(APIView):
    """Apply starter data packs. Writes master data, so it is settings-gated the
    same way the masters screens are — and each pack is skipped unless the
    licence covers it, so a restaurant install can't seed itself room types."""

    module = "settings"

    def get_permissions(self):
        return [IsAuthenticated(), ModulePermission()]

    def post(self, request):
        from .onboarding import apply_packs, checklist

        keys = request.data.get("packs") or []
        if not isinstance(keys, list):
            return Response({"detail": "packs must be a list of pack keys"},
                            status=status.HTTP_400_BAD_REQUEST)
        prop = get_property()
        created = apply_packs(prop, keys, request.data.get("options") or {})
        log_action(request.user, "starter_packs", entity="Property", entity_id=prop.id,
                   after={"packs": keys, "created": created})
        return Response({"created": created, "checklist": checklist(prop)})


class BootstrapAdminView(APIView):
    """First-run only: create the very first owner / Super Admin on a fresh
    install (no accounts yet). Guarded — once any super admin exists this 403s,
    so it can never be used to escalate on a live system. The client then signs
    in with the credentials it just set, reusing the normal token flow."""

    permission_classes = [AllowAny]

    def post(self, request):
        from django.contrib.auth.password_validation import validate_password
        from django.core.exceptions import ValidationError as DjangoValidationError

        from .constants import ROLE_SUPER_ADMIN

        if User.objects.filter(is_superuser=True).exists():
            return Response({"detail": "Setup is already complete — sign in instead."},
                            status=status.HTTP_403_FORBIDDEN)
        from rest_framework.serializers import ValidationError as DRFValidationError

        from .validators import validate_username as clean_username
        email = (request.data.get("email") or "").strip()
        password = request.data.get("password") or ""
        name = (request.data.get("name") or "").strip()
        first, _, last = name.partition(" ")
        try:
            username = clean_username(request.data.get("username") or "")
        except DRFValidationError as e:
            detail = e.detail[0] if isinstance(e.detail, list) else str(e.detail)
            return Response({"detail": detail}, status=status.HTTP_400_BAD_REQUEST)
        if User.objects.filter(username__iexact=username).exists():
            return Response({"detail": "That username is already taken"}, status=status.HTTP_400_BAD_REQUEST)
        if not email:
            return Response({"detail": "An email is required"}, status=status.HTTP_400_BAD_REQUEST)
        from .validators import normalize_email
        email = normalize_email(email)
        # Same rule the Users screen enforces: password reset is delivered to
        # this address, so it has to identify exactly one account.
        if User.objects.filter(email__iexact=email).exists():
            return Response({"detail": "That email is already on another account"},
                            status=status.HTTP_400_BAD_REQUEST)
        try:
            validate_password(password)
        except DjangoValidationError as exc:
            return Response({"detail": " ".join(exc.messages)}, status=status.HTTP_400_BAD_REQUEST)
        user = User.objects.create_user(
            username=username, email=email, password=password,
            first_name=first, last_name=last, role=ROLE_SUPER_ADMIN,
            is_superuser=True, is_staff=True,
        )
        log_action(None, "bootstrap_admin", entity="User", entity_id=user.id,
                   after={"username": username, "role": ROLE_SUPER_ADMIN})
        return Response({"username": user.username, "role": user.role},
                        status=status.HTTP_201_CREATED)


class EntitlementView(APIView):
    """Reconfigure entitlements from Settings. Was IsAuthenticated only —
    the docstring claimed server-side enforcement but the permission class
    never actually checked role/module, so any logged-in user (Housekeeping,
    a cashier, a captain) could flip HMS/restaurant/banquets/RMS for the
    whole property (go-live QA finding CX-RBAC-02: PATCH /auth/entitlements/).
    Now gated the same way as /auth/property/: settings-capable roles only.

    Role is only half of it, though: the four LICENSED_FLAGS are what the
    customer BOUGHT, so nobody on the customer side may write them regardless of
    role — the owner included. Settings still owns everything else on this model
    (bar mode, KDS behaviour, the per-feature toggles), which is configuration
    within the licence. See constants.LICENSED_FLAGS.
    """

    module = "settings"

    def get_permissions(self):
        return [IsAuthenticated(), ModulePermission()]

    def patch(self, request):
        prop = get_property()
        ent = prop.entitlement
        licensed = [f for f in LICENSED_FLAGS if f in request.data]
        if licensed:
            return Response(
                {"detail": f"{', '.join(licensed)} is part of your Hearth licence and can't be "
                           f"changed here. Contact Hearth to change your plan.",
                 "licensed_flags": licensed},
                status=status.HTTP_403_FORBIDDEN,
            )
        # A per-feature toggle (Settings > Features): {feature, enabled}. Runs
        # the dependency engine so the choice always leaves a valid combination
        # — enabling pulls prerequisites on, disabling cascades dependents off.
        feat = request.data.get("feature")
        if feat is not None:
            from .features import FEATURES, apply_toggle
            spec = FEATURES.get(feat)
            if spec is None or not spec.get("toggleable", True):
                return Response({"detail": f"'{feat}' is not a configurable feature"}, status=400)
            enabled = bool(request.data.get("enabled", True))
            before = ent.features or {}
            ent.features = apply_toggle(before, feat, enabled, ent.as_dict())
            ent.save()
            log_action(request.user, "feature_toggle", entity="Entitlement",
                       entity_id=ent.id, before=before, after=ent.features,
                       note=f"{feat}={'on' if enabled else 'off'}")
            return Response(PropertySerializer(get_property()).data)
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
    """Users & Roles. Gated on "users" rather than "settings" so HR can run the
    staff roster end to end (hire on the Employees master, hand out the login
    here) without seeing property configuration, entitlements or numbering.

    Every write also passes the seniority ladder in constants.ROLE_RANK — a
    module gate alone would let an Admin PATCH themselves to Super Admin.
    """

    module = "users"
    queryset = User.objects.all().order_by("role", "username")
    serializer_class = UserSerializer

    def _deny(self, detail):
        return Response({"detail": detail}, status=status.HTTP_403_FORBIDDEN)

    @action(detail=False, methods=["get"], url_path="assignable-roles")
    def role_options(self, request):
        """The role picker's options: every role the caller may hand out — the
        property's own roles included — each with the modules it unlocks, so
        whoever creates the login sees the access they're granting instead of
        guessing from the role name."""
        mine = getattr(request.user, "role", "")
        rows = {r.name: r for r in Role.objects.all()}
        out = []
        for name in assignable_roles(mine):
            row = rows.get(name)
            out.append({
                "role": name,
                "rank": role_rank(name),
                "modules": allowed_modules_for(name),
                "custom": bool(row and not row.is_system),
                "behaves_as": row.behaves_as if row else name,
                "description": row.description if row else "",
            })
        return Response(out)

    def create(self, request, *args, **kwargs):
        mine = getattr(request.user, "role", "")
        target = request.data.get("role")
        if not can_assign_role(mine, target):
            return self._deny(
                f"As {mine} you can't create a {target or 'user with no role'}. "
                f"You may only assign roles at or below your own."
            )
        return super().create(request, *args, **kwargs)

    def update(self, request, *args, **kwargs):
        instance = self.get_object()
        mine = getattr(request.user, "role", "")
        # Editing someone more senior than you — even to flip is_active — is
        # not yours to do, so check the CURRENT role before the requested one.
        if role_rank(instance.role) > role_rank(mine):
            return self._deny(f"{instance.username} is a {instance.role} — only "
                              f"{instance.role} or above can change that account.")
        target = request.data.get("role", instance.role)
        if target != instance.role:
            if instance.pk == request.user.pk:
                return self._deny("You can't change your own role — ask someone "
                                  "at or above your level.")
            if not can_assign_role(mine, target):
                return self._deny(f"As {mine} you can't move {instance.username} to {target}.")
            if base_role(instance.role) == ROLE_SUPER_ADMIN and self._last_owner(instance):
                return self._deny("This is the only Super Admin — appoint another "
                                  "one before changing this account's role.")
        # Form-encoded requests send "false" as a string, JSON sends a bool —
        # read both, or the guard below silently never fires.
        deactivating = str(request.data.get("is_active", "")).lower() in ("false", "0")
        if deactivating and self._last_owner(instance):
            return self._deny("This is the only Super Admin — the property would "
                              "be left with no owner.")
        return super().update(request, *args, **kwargs)

    def destroy(self, request, *args, **kwargs):
        instance = self.get_object()
        mine = getattr(request.user, "role", "")
        if instance.pk == request.user.pk:
            return self._deny("You can't delete your own account.")
        if role_rank(instance.role) > role_rank(mine):
            return self._deny(f"{instance.username} is a {instance.role} — that's "
                              f"above your level.")
        if self._last_owner(instance):
            return self._deny("This is the only Super Admin — the property would "
                              "be left with no owner.")
        return super().destroy(request, *args, **kwargs)

    @staticmethod
    def _last_owner(user):
        """True when deactivating/deleting/demoting `user` would leave the
        property with no active Super Admin at all."""
        if base_role(user.role) != ROLE_SUPER_ADMIN or not user.is_active:
            return False
        return not User.objects.filter(
            role__in=role_names_for(ROLE_SUPER_ADMIN), is_active=True).exclude(pk=user.pk).exists()


class RoleViewSet(ModuleViewSetMixin, viewsets.ModelViewSet):
    """Role Master — the roles this property hands out.

    The seventeen built-ins arrive seeded and stay `is_system`: their name,
    base and rank are fixed because the rest of the backend reasons in those
    names, but their module mapping is editable like any other. Anything else
    is a role the property created, based on a built-in whose behaviour it
    inherits (see models.Role).

    Guards are the same two ideas as everywhere else in Users & Roles — you
    can't create seniority above your own, and you can't hand out access you
    don't hold yourself. Without the second one, "new role" would be a way to
    build yourself a Super Admin one module at a time.
    """

    module = "roles"
    queryset = Role.objects.all()
    serializer_class = RoleSerializer

    def _check(self, request, data, instance=None):
        """Returns an error string, or None when the write is allowed."""
        mine = getattr(request.user, "role", "")
        my_modules = allowed_modules_for(mine)

        if instance is not None and instance.is_system:
            for field, label in (("name", "name"), ("base_role", "base role"), ("rank", "rank")):
                if field in data and str(data[field]) != str(getattr(instance, field)):
                    return (f"{instance.name} is a built-in role — its {label} is fixed. "
                            f"You can still change which screens it opens.")

        rank = data.get("rank", getattr(instance, "rank", 1))
        try:
            rank = int(rank)
        except (TypeError, ValueError):
            return "Rank must be a whole number."
        if rank > role_rank(mine):
            return (f"As {mine} you can't create a role more senior than yourself "
                    f"(rank {rank} vs your {role_rank(mine)}).")

        base = data.get("base_role", getattr(instance, "base_role", "")) or ""
        if base and not can_assign_role(mine, base):
            return f"As {mine} you can't base a role on {base}."
        if base and base not in ROLE_ALLOW:
            return f"'{base}' isn't a built-in role — pick one to base this on."
        if instance is None and not base:
            return "Pick the built-in role this one should behave as."

        modules = data.get("modules")
        if modules is not None and my_modules != "*":
            if modules == "*":
                return "Only a full-access role can grant full access."
            unknown = [m for m in modules if m not in ALL_MODULES]
            if unknown:
                return f"Unknown screen(s): {', '.join(unknown)}."
            over = [m for m in modules if m not in my_modules]
            if over:
                return (f"You don't have {', '.join(sorted(over))} yourself, so you can't "
                        f"grant it. Ask a Super Admin, Managing Director or General Manager.")
        return None

    def create(self, request, *args, **kwargs):
        error = self._check(request, request.data)
        if error:
            return Response({"detail": error}, status=status.HTTP_403_FORBIDDEN)
        return super().create(request, *args, **kwargs)

    def update(self, request, *args, **kwargs):
        error = self._check(request, request.data, self.get_object())
        if error:
            return Response({"detail": error}, status=status.HTTP_403_FORBIDDEN)
        return super().update(request, *args, **kwargs)

    def destroy(self, request, *args, **kwargs):
        role = self.get_object()
        if role.is_system:
            return Response({"detail": f"{role.name} is a built-in role and can't be deleted. "
                                       f"Set it inactive if you don't use it."},
                            status=status.HTTP_403_FORBIDDEN)
        holders = User.objects.filter(role=role.name).count()
        if holders:
            return Response({"detail": f"{holders} account(s) still hold {role.name} — move them "
                                       f"to another role first, or set this one inactive."},
                            status=status.HTTP_400_BAD_REQUEST)
        if role_rank(role.name) > role_rank(getattr(request.user, "role", "")):
            return Response({"detail": f"{role.name} is above your level."},
                            status=status.HTTP_403_FORBIDDEN)
        return super().destroy(request, *args, **kwargs)

    def perform_create(self, serializer):
        role = serializer.save(is_system=False)
        log_action(self.request.user, "role_create", entity="Role", entity_id=role.id,
                   after={"name": role.name, "base": role.base_role, "rank": role.rank,
                          "modules": role.modules})

    def perform_update(self, serializer):
        before = {"modules": serializer.instance.modules, "rank": serializer.instance.rank,
                  "active": serializer.instance.active}
        role = serializer.save()
        log_action(self.request.user, "role_update", entity="Role", entity_id=role.id,
                   before=before,
                   after={"modules": role.modules, "rank": role.rank, "active": role.active})

    def perform_destroy(self, instance):
        log_action(self.request.user, "role_delete", entity="Role", entity_id=instance.id,
                   before={"name": instance.name, "base": instance.base_role})
        instance.delete()


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

    @action(detail=False, methods=["get", "post"], url_path="import")
    def import_branches(self, request):
        """Bulk branch onboarding: GET the CSV template, POST it filled.
        `edition` drives the four entitlement flags exactly like the manual
        Add-branch form does client-side — the model itself just defaults
        every flag to True, so this replicates that derivation explicitly."""
        from django.db import IntegrityError

        from apps.accounts.constants import role_can_access
        from apps.accounts.csv_import import parse_upload, template_response
        if not role_can_access(getattr(request.user, "role", ""), "branchmaster"):
            return Response({"detail": "branch import needs the Branch Master screen (admin)"},
                            status=403)
        columns = ["name", "code", "city", "state", "gstin", "edition", "invoice_prefix"]
        if request.method == "GET":
            return template_response("branches-template.csv", columns, [
                ["Hearth Grand — Downtown", "DTN", "Chennai", "Tamil Nadu", "", "both", "DTN-"],
                ["Hearth Bistro — Airport", "APT", "Chennai", "Tamil Nadu", "", "restaurant", "APT-"],
            ])
        try:
            rows = parse_upload(request)
        except ValueError as e:
            return Response({"detail": str(e)}, status=400)

        prop = get_property()
        created, skipped, errors = [], [], []
        for lineno, row in rows:
            name = row.get("name", "")
            code = row.get("code", "").upper()
            if not name and not code:
                continue
            if not name or not code:
                errors.append({"row": lineno, "name": name or code, "reason": "name and code are both required"})
                continue
            if Branch.objects.filter(code__iexact=code).exists():
                skipped.append(name)
                continue
            edition = (row.get("edition") or "both").lower()
            if edition not in ("hotel", "restaurant", "both"):
                errors.append({"row": lineno, "name": name, "reason": "edition must be hotel / restaurant / both"})
                continue
            try:
                Branch.objects.create(
                    property=prop, name=name, code=code,
                    city=row.get("city", ""), state=row.get("state", ""),
                    gstin=row.get("gstin", "").upper()[:20],
                    edition=edition,
                    hms=edition != "restaurant", restaurant=edition != "hotel",
                    banquets=edition != "restaurant", rms=edition != "restaurant",
                    invoice_prefix=row.get("invoice_prefix", "").upper()[:10],
                )
                created.append(name)
            except (TypeError, ValueError, IntegrityError) as e:
                errors.append({"row": lineno, "name": name, "reason": str(e)[:120]})
        log_action(request.user, "branch_import", entity="Branch",
                   after={"created": len(created), "errors": len(errors)})
        return Response({"created": len(created), "skipped_existing": skipped, "errors": errors})


class UserBranchAccessViewSet(ModuleViewSetMixin, viewsets.ModelViewSet):
    """Staff assignment: which branch a person operates in, and as what role
    there. `?user=<id>` narrows to one person's assignments (the Users screen
    calls it this way); unfiltered lists everyone's, for the Branch Master
    screen's roster view."""

    # Part of the Users screen, so it follows "users" — and it grants a role at
    # a branch, so it obeys the same ladder (perform_create below).
    module = "users"
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

    def create(self, request, *args, **kwargs):
        mine = getattr(request.user, "role", "")
        target = request.data.get("role")
        if target and not can_assign_role(mine, target):
            return Response(
                {"detail": f"As {mine} you can't assign someone as {target} at a branch."},
                status=status.HTTP_403_FORBIDDEN)
        return super().create(request, *args, **kwargs)

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

    The grid half of Role Master: GET returns every role in the table — the
    property's own included — against every module; POST toggles one cell
    ({role, module, allowed}) straight onto that role's mapping. Super
    Admin/MD/GM are protected (full access). Only roles holding the 'roles'
    module may view or edit, and never their own row (see post()).
    """

    permission_classes = [IsAuthenticated, ModulePermission]
    module = "roles"

    def get(self, request):
        from .rbac import PROTECTED
        rows = list(Role.objects.all())
        if not rows:            # unseeded database — fall back to the constants
            names = list(ROLE_ALLOW.keys())
            meta = [{"role": n, "is_system": True, "rank": role_rank(n),
                     "behaves_as": n, "users": 0, "active": True} for n in names]
        else:
            counts = {r["role"]: r["n"] for r in
                      User.objects.values("role").annotate(n=models.Count("id"))}
            names = [r.name for r in rows]
            meta = [{"role": r.name, "is_system": r.is_system, "rank": r.rank,
                     "behaves_as": r.behaves_as, "users": counts.get(r.name, 0),
                     "active": r.active} for r in rows]
        allow_by_role = {n: allowed_modules_for(n) for n in names}
        matrix = []
        for module in ALL_MODULES:
            cells = []
            for role in names:
                allow = allow_by_role[role]
                cells.append(allow == "*" or module in allow)
            matrix.append({"module": module, "cells": cells})
        return Response({"roles": names, "matrix": matrix, "meta": meta,
                         "protected": list(PROTECTED),
                         "my_role": getattr(request.user, "role", "")})

    def post(self, request):
        from .rbac import PROTECTED
        role = request.data.get("role")
        module = request.data.get("module")
        allowed = bool(request.data.get("allowed"))
        row = Role.objects.filter(name=role).first()
        if row is None and role not in ROLE_ALLOW:
            return Response({"detail": "unknown role"}, status=status.HTTP_400_BAD_REQUEST)
        if base_role(role) in PROTECTED:
            return Response({"detail": f"{role} always has full access and can't be edited"},
                            status=status.HTTP_400_BAD_REQUEST)
        if module not in ALL_MODULES:
            return Response({"detail": "unknown module"}, status=status.HTTP_400_BAD_REQUEST)
        # The two ways this screen could be used to escalate, now closed (the
        # docstring above always claimed this; nothing actually enforced it):
        #   1. editing your own row — the direct route to giving yourself more;
        #   2. granting a module you don't hold — the indirect route, since
        #      whoever edits the matrix can also create a user in that role and
        #      set its password. Revoking is always allowed, and the "*" roles
        #      (Super Admin / MD / GM) are unaffected by either rule.
        mine = getattr(request.user, "role", "")
        my_modules = allowed_modules_for(mine)
        if role == mine:
            return Response({"detail": "You can't edit your own role's permissions."},
                            status=status.HTTP_403_FORBIDDEN)
        if allowed and my_modules != "*" and module not in my_modules:
            return Response(
                {"detail": f"You don't have '{module}' yourself, so you can't grant it "
                           f"to {role}. Ask a Super Admin, Managing Director or "
                           f"General Manager."},
                status=status.HTTP_403_FORBIDDEN)
        # Start from the role's current mapping, toggle the one cell, save.
        current = allowed_modules_for(role)
        mods = list(current) if isinstance(current, list) else []
        if allowed and module not in mods:
            mods.append(module)
        elif not allowed and module in mods:
            mods.remove(module)
        if row is None:         # unseeded database: materialise the row now
            row = Role(name=role, rank=role_rank(role), is_system=True)
        row.modules = mods
        row.save()
        log_action(request.user, "role_permission", entity="Role", entity_id=row.id,
                   after={"role": role, "module": module, "allowed": allowed})
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
