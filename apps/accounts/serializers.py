from rest_framework import serializers
from rest_framework_simplejwt.serializers import TokenObtainPairSerializer

from . import mfa
from .constants import ROLE_ALLOW
from .models import Branch, Entitlement, Property, User, UserBranchAccess


class BranchSerializer(serializers.ModelSerializer):
    class Meta:
        model = Branch
        fields = [
            "id", "name", "code", "address", "city", "state", "gstin",
            "edition", "hms", "restaurant", "banquets", "rms",
            "invoice_prefix", "status", "logo", "created_at",
        ]


class UserBranchAccessSerializer(serializers.ModelSerializer):
    branch_name = serializers.CharField(source="branch.name", read_only=True)
    branch_code = serializers.CharField(source="branch.code", read_only=True)

    class Meta:
        model = UserBranchAccess
        fields = ["id", "user", "branch", "branch_name", "branch_code", "role",
                  "start_date", "end_date", "created_at"]
        read_only_fields = ["created_at"]


class UserSerializer(serializers.ModelSerializer):
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
    class Meta:
        model = Entitlement
        fields = ["hms", "restaurant", "banquets", "rms", "bar_mode"]


class PropertySerializer(serializers.ModelSerializer):
    entitlement = EntitlementSerializer(read_only=True)

    class Meta:
        model = Property
        fields = [
            "id", "name", "edition", "setup_done", "business_date",
            "gstin", "address", "phone", "logo", "doc_header", "doc_footer",
            "doc_header_align", "doc_footer_align",
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
