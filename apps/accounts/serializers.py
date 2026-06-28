from rest_framework import serializers
from rest_framework_simplejwt.serializers import TokenObtainPairSerializer

from .constants import ROLE_ALLOW
from .models import Entitlement, Property, User


class UserSerializer(serializers.ModelSerializer):
    name = serializers.SerializerMethodField()
    allowed_modules = serializers.SerializerMethodField()

    class Meta:
        model = User
        fields = [
            "id", "username", "name", "first_name", "last_name", "email",
            "role", "user_code", "phone", "discount_cap_type",
            "discount_cap_value", "rights", "is_active", "allowed_modules",
        ]

    def get_name(self, obj):
        return obj.get_full_name() or obj.username

    def get_allowed_modules(self, obj):
        return ROLE_ALLOW.get(obj.role, [])


class EntitlementSerializer(serializers.ModelSerializer):
    class Meta:
        model = Entitlement
        fields = ["hms", "restaurant", "banquets", "rms"]


class PropertySerializer(serializers.ModelSerializer):
    entitlement = EntitlementSerializer(read_only=True)

    class Meta:
        model = Property
        fields = [
            "id", "name", "edition", "setup_done", "business_date",
            "gstin", "currency", "entitlement",
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
        data["user"] = UserSerializer(self.user).data
        return data
