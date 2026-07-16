from rest_framework import serializers

from .models import Department, Designation, PaymentMethod


class DepartmentSerializer(serializers.ModelSerializer):
    class Meta:
        model = Department
        fields = ["id", "name", "active"]


class DesignationSerializer(serializers.ModelSerializer):
    class Meta:
        model = Designation
        fields = ["id", "name", "active"]


class PaymentMethodSerializer(serializers.ModelSerializer):
    class Meta:
        model = PaymentMethod
        fields = ["id", "name", "active", "counts_as_cash", "captain_allowed", "builtin"]
        read_only_fields = ["builtin"]

    def validate_name(self, value):
        # Builtin tenders (Cash/UPI/Gateway) have behavior wired to their
        # names across POS settle, the till count and the card gateway —
        # renaming one would silently orphan that wiring.
        if self.instance and self.instance.builtin and value != self.instance.name:
            raise serializers.ValidationError(
                f"'{self.instance.name}' is a built-in tender and cannot be renamed."
            )
        return value
