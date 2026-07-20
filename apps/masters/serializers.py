from rest_framework import serializers

from .models import Department, Designation, KitchenStation, PaymentMethod


class DepartmentSerializer(serializers.ModelSerializer):
    class Meta:
        model = Department
        fields = ["id", "name", "active"]


class DesignationSerializer(serializers.ModelSerializer):
    class Meta:
        model = Designation
        fields = ["id", "name", "active"]


class KitchenStationSerializer(serializers.ModelSerializer):
    class Meta:
        model = KitchenStation
        fields = ["id", "name", "mode", "is_bar", "active"]
        read_only_fields = ["is_bar"]

    def validate_name(self, value):
        # The bar station's name is what MenuItem.save() looks up to decide
        # bar_menu auto-assignment — renaming it would silently orphan that.
        if self.instance and self.instance.is_bar and value != self.instance.name:
            raise serializers.ValidationError(
                f"'{self.instance.name}' is the bar station and cannot be renamed."
            )
        return value


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
