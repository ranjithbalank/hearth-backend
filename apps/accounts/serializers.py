from rest_framework import serializers
from rest_framework_simplejwt.serializers import TokenObtainPairSerializer

from . import mfa
from .constants import LICENSED_FLAGS, ROLE_ALLOW
from .validators import CaseInsensitiveUniqueMixin
from .models import Branch, Entitlement, Property, Role, User, UserBranchAccess


class BranchSerializer(CaseInsensitiveUniqueMixin, serializers.ModelSerializer):
    ci_unique_fields = ["name", "code"]

    class Meta:
        model = Branch
        fields = [
            "id", "name", "code", "address", "city", "state", "gstin",
            "edition", "hms", "restaurant", "banquets", "rms",
            "invoice_prefix", "status", "logo", "created_at",
        ]


class RoleSerializer(CaseInsensitiveUniqueMixin, serializers.ModelSerializer):
    """Role Master rows. `users` and `behaves_as` are what the screen needs to
    show a role honestly: how many people hold it (so you know what a change
    touches, and whether it can be deleted) and which built-in's rules it
    plays by."""

    users = serializers.SerializerMethodField()
    behaves_as = serializers.CharField(read_only=True)
    # "Night Manager" and "night manager" would be two roles nobody could tell
    # apart, each with its own mapping.
    ci_unique_fields = ["name"]

    class Meta:
        model = Role
        fields = ["id", "name", "base_role", "behaves_as", "rank", "modules",
                  "is_system", "active", "description", "users"]

    def get_users(self, obj):
        from .models import User
        return User.objects.filter(role=obj.name).count()


def validate_assignable_role(value):
    """Shared by both places a role name is written: it must be a live row in
    the Role Master. Retired roles stay on the accounts holding them but are
    never handed to anyone new."""
    from .models import Role
    row = Role.objects.filter(name=value).first()
    if row is None:
        raise serializers.ValidationError(
            f"'{value}' is not a role on this property — create it in Role Master first.")
    if not row.active:
        raise serializers.ValidationError(
            f"'{value}' has been retired and can't be assigned to anyone new.")
    return value


class UserBranchAccessSerializer(serializers.ModelSerializer):
    branch_name = serializers.CharField(source="branch.name", read_only=True)
    branch_code = serializers.CharField(source="branch.code", read_only=True)

    class Meta:
        model = UserBranchAccess
        fields = ["id", "user", "branch", "branch_name", "branch_code", "role",
                  "start_date", "end_date", "created_at"]
        read_only_fields = ["created_at"]

    def validate_role(self, value):
        return validate_assignable_role(value)


class UserSerializer(CaseInsensitiveUniqueMixin, serializers.ModelSerializer):
    name = serializers.SerializerMethodField()
    allowed_modules = serializers.SerializerMethodField()
    branches = serializers.SerializerMethodField()
    password = serializers.CharField(write_only=True, required=False, allow_blank=True)
    # A quick-login PIN has no business being readable over the API — same
    # write-only treatment as password (security review 2026-07, finding B8).
    # Settings' user list never displayed it (create-form value only), so
    # this changes nothing visible.
    passcode = serializers.CharField(write_only=True, required=False, allow_blank=True)

    class Meta:
        model = User
        fields = [
            "id", "username", "name", "first_name", "last_name", "email",
            "role", "user_code", "phone", "passcode", "discount_cap_type",
            "discount_cap_value", "rights", "is_active", "allowed_modules",
            "mfa_enabled", "password", "branches",
        ]

    def get_name(self, obj):
        return obj.get_full_name() or obj.username

    def get_allowed_modules(self, obj):
        from .rbac import allowed_modules_for
        return allowed_modules_for(obj.role)

    def get_branches(self, obj):
        from .rbac import PROTECTED
        if obj.role in PROTECTED:
            return "*"
        return UserBranchAccessSerializer(
            obj.branch_access.select_related("branch").all(), many=True
        ).data

    # Two accounts whose names differ only in capitals are indistinguishable to
    # everyone reading a screen, and only one of them owns any given permission.
    # Email is here for a sharper reason: password reset is delivered to it, so
    # an address held by two accounts makes recovery ambiguous at best and sends
    # someone else's reset link at worst. Blank emails don't collide (the mixin
    # skips empty values), so staff without an address are unaffected.
    ci_unique_fields = ["username", "email"]
    ci_unique_messages = {
        "email": "{value} is already on another account. Password reset is sent to this "
                 "address, so it has to identify exactly one person.",
    }

    def validate_username(self, value):
        from .validators import validate_username
        return validate_username(value)

    def validate_email(self, value):
        from .validators import normalize_email
        return normalize_email(value)

    def validate_phone(self, value):
        from .validators import validate_phone
        return validate_phone(value)

    def validate_role(self, value):
        # The valid set is the Role Master, not a static choices list — that's
        # what lets a property assign a role it created itself.
        return validate_assignable_role(value)

    def validate_first_name(self, value):
        from .validators import validate_person_name
        return validate_person_name(value)

    def validate_last_name(self, value):
        from .validators import validate_person_name
        return validate_person_name(value)

    def validate_passcode(self, value):
        from .validators import validate_digits
        return validate_digits(value, field="POS passcode", max_len=12)

    def validate_password(self, value):
        # set_password() alone skips AUTH_PASSWORD_VALIDATORS — without this,
        # Settings > Users happily accepted "123" (QA finding TC-007).
        if value:
            from django.contrib.auth.password_validation import validate_password
            from django.core.exceptions import ValidationError as DjangoValidationError
            try:
                validate_password(value)
            except DjangoValidationError as e:
                raise serializers.ValidationError(list(e.messages))
        return value

    def create(self, validated_data):
        password = validated_data.pop("password", "") or ""
        user = User(**validated_data)
        if password:
            user.set_password(password)
        user.save()
        return user

    def update(self, instance, validated_data):
        password = validated_data.pop("password", "")
        for k, v in validated_data.items():
            setattr(instance, k, v)
        if password:
            instance.set_password(password)
        instance.save()
        return instance


class EntitlementSerializer(serializers.ModelSerializer):
    # The raw per-feature overrides ({module: bool}) plus the RESOLVED effective
    # on/off for every feature (entitlement flag ∧ owner toggle ∧ prerequisites).
    # The frontend gates nav/routes on `features_effective` — one authoritative
    # map, computed server-side, so the client stops hand-mirroring the rules.
    features_effective = serializers.SerializerMethodField()

    class Meta:
        model = Entitlement
        fields = ["hms", "restaurant", "banquets", "rms", "bar_mode",
                  "kds_partial_ready", "features", "features_effective"]
        # The licensed flags still SERIALIZE (the client needs them to gate nav)
        # but can never be written through this serializer — they say what the
        # customer bought and only provisioning sets them. EntitlementView 403s
        # on them explicitly; this is the backstop for any future write path
        # that forgets to ask. `features` is written via the dependency engine
        # (apply_toggle), never assigned directly.
        read_only_fields = ["features", *LICENSED_FLAGS]

    def get_features_effective(self, obj):
        from .features import resolve
        return resolve(obj.as_dict(), obj.features or {})


class PropertySerializer(serializers.ModelSerializer):
    entitlement = EntitlementSerializer(read_only=True)
    # First-run flag for the (AllowAny) property read: true when no owner account
    # exists yet, so the client shows the create-Super-Admin onboarding step.
    needs_admin = serializers.SerializerMethodField()
    # True only on a demo install (the full seed_demo personas exist), so the
    # Login screen shows the "tap to sign in" chips there but not on a real
    # property where those accounts don't exist.
    demo_logins = serializers.SerializerMethodField()

    def get_needs_admin(self, obj):
        from .models import User
        return not User.objects.filter(is_superuser=True).exists()

    def get_demo_logins(self, obj):
        from .models import User
        return User.objects.filter(username__in=["gm", "superadmin", "cashier"]).exists()

    def validate_phone(self, value):
        from .validators import validate_phone
        return validate_phone(value)

    def validate_default_country_code(self, value):
        v = (value or "").strip()
        if not v:
            return "+91"
        if not v.startswith("+"):
            v = "+" + v
        if not v[1:].isdigit() or not (1 <= len(v[1:]) <= 4):
            raise serializers.ValidationError(
                "A dialling code is + followed by 1-4 digits, e.g. +91.")
        return v

    class Meta:
        model = Property
        fields = [
            "id", "name", "edition", "setup_done", "needs_admin", "demo_logins", "business_date",
            "gstin", "address", "phone", "default_country_code",
            "logo", "doc_header", "doc_footer",
            "doc_header_align", "doc_footer_align",
            "pos_doc_header", "pos_doc_footer", "pos_doc_header_align", "pos_doc_footer_align",
            "invoice_columns", "pos_bill_columns",
            "currency", "entitlement", "gst_billing_mode",
            "zomato_commission_pct", "swiggy_commission_pct",
            "invoice_prefix", "bill_prefix", "po_prefix", "grn_prefix", "beo_prefix",
        ]


class HearthTokenSerializer(TokenObtainPairSerializer):
    """Adds the role + profile claim to the JWT response."""

    @classmethod
    def get_token(cls, user):
        token = super().get_token(user)
        token["role"] = user.role
        return token

    def validate(self, attrs):
        data = super().validate(attrs)
        user = self.user
        # Enforce MFA when the user has it enabled, or policy requires it.
        if user.mfa_enabled or mfa.role_requires_mfa(user.role):
            if not user.mfa_enabled:
                raise serializers.ValidationError(
                    {"mfa_required": True,
                     "detail": "MFA is required for your role. Enrol a TOTP authenticator."}
                )
            otp = self.initial_data.get("otp")
            if not mfa.verify(user.mfa_secret, otp):
                raise serializers.ValidationError(
                    {"mfa_required": True, "detail": "A valid authenticator code is required."}
                )
        data["user"] = UserSerializer(self.user).data
        return data
