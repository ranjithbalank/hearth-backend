from rest_framework import serializers
from rest_framework_simplejwt.serializers import TokenObtainPairSerializer

from . import mfa
from .constants import ROLE_ALLOW
from .models import Entitlement, Property, User


class UserSerializer(serializers.ModelSerializer):
    name = serializers.SerializerMethodField()
    allowed_modules = serializers.SerializerMethodField()
    password = serializers.CharField(write_only=True, required=False, allow_blank=True)

    class Meta:
        model = User
        fields = [
            "id", "username", "name", "first_name", "last_name", "email",
            "role", "user_code", "phone", "passcode", "discount_cap_type",
            "discount_cap_value", "rights", "is_active", "allowed_modules",
            "mfa_enabled", "password",
        ]

    def get_name(self, obj):
        return obj.get_full_name() or obj.username

    def get_allowed_modules(self, obj):
        from .rbac import allowed_modules_for
        return allowed_modules_for(obj.role)

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
