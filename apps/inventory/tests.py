from decimal import Decimal

from django.test import TestCase
from django.urls import reverse
from rest_framework.test import APIClient

from apps.accounts.models import User

from .models import Ingredient


class InventoryApiTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        # Inventory needs a role with the "inventory" module (cashier is POS-only).
        self.client.force_authenticate(User.objects.create_user(
            username="c", password="Tk9$mZ2pQw!7", role="General Manager"))
        Ingredient.objects.create(name="Butter", unit="kg", current_stock=Decimal("2"),
                                  reorder_level=Decimal("5"))
        Ingredient.objects.create(name="Rice", unit="kg", current_stock=Decimal("40"),
                                  reorder_level=Decimal("10"))

    def test_low_stock_flags_below_par(self):
        r = self.client.get(reverse("ingredient-low-stock"))
        names = [i["name"] for i in r.data["items"]]
        self.assertIn("Butter", names)
        self.assertNotIn("Rice", names)

    def test_adjust_updates_stock(self):
        ing = Ingredient.objects.get(name="Rice")
        r = self.client.post(reverse("ingredient-adjust", args=[ing.id]),
                             {"qty": "-5", "reason": "spoilage"}, format="json")
        self.assertEqual(r.status_code, 200)
        ing.refresh_from_db()
        self.assertEqual(ing.current_stock, Decimal("35.000"))
        move = ing.movements.first()
        self.assertEqual(move.balance, Decimal("35.000"))  # ledger snapshots balance
        self.assertEqual(move.created_by, "c")

    def test_material_code_auto_assigned(self):
        ing = Ingredient.objects.get(name="Rice")
        self.assertEqual(ing.code, f"RM-{ing.pk:04d}")

    def test_wastage_entry_requires_reason_and_deducts(self):
        ing = Ingredient.objects.get(name="Rice")
        r = self.client.post(reverse("ingredient-waste", args=[ing.id]),
                             {"qty": "2"}, format="json")
        self.assertEqual(r.status_code, 400)  # reason required
        r = self.client.post(reverse("ingredient-waste", args=[ing.id]),
                             {"qty": "2", "reason": "burnt batch"}, format="json")
        self.assertEqual(r.status_code, 200)
        ing.refresh_from_db()
        self.assertEqual(ing.current_stock, Decimal("38.000"))
        self.assertEqual(ing.movements.first().kind, "wastage")

    def test_physical_count_posts_difference(self):
        ing = Ingredient.objects.get(name="Rice")  # book 40
        r = self.client.post(reverse("ingredient-count", args=[ing.id]),
                             {"counted": "37.5"}, format="json")
        self.assertEqual(r.status_code, 200)
        ing.refresh_from_db()
        self.assertEqual(ing.current_stock, Decimal("37.500"))
        move = ing.movements.first()
        self.assertEqual(move.kind, "count")
        self.assertEqual(move.qty, Decimal("-2.500"))

    def test_expiring_list(self):
        from datetime import timedelta
        from django.utils import timezone
        Ingredient.objects.create(name="Cream", unit="l", current_stock=Decimal("3"),
                                  expiry_date=timezone.localdate() + timedelta(days=2))
        Ingredient.objects.create(name="Salt", unit="kg", current_stock=Decimal("9"),
                                  expiry_date=timezone.localdate() + timedelta(days=90))
        r = self.client.get(reverse("ingredient-expiring"))
        names = [i["name"] for i in r.data]
        self.assertIn("Cream", names)
        self.assertNotIn("Salt", names)

    def test_movements_register_filters(self):
        ing = Ingredient.objects.get(name="Rice")
        self.client.post(reverse("ingredient-adjust", args=[ing.id]),
                         {"qty": "5", "reason": "opening"}, format="json")
        self.client.post(reverse("ingredient-waste", args=[ing.id]),
                         {"qty": "1", "reason": "spill"}, format="json")
        r = self.client.get(reverse("ingredient-movements") + "?kind=wastage")
        self.assertEqual(len(r.data), 1)
        self.assertEqual(r.data[0]["kind"], "wastage")

    def test_uom_master_seeded_and_crud(self):
        r = self.client.get(reverse("uom-list"))
        codes = [u["code"] for u in r.data]
        for code in ["kg", "g", "l", "ml", "pc", "pkt"]:
            self.assertIn(code, codes)
        r = self.client.post(reverse("uom-list"), {"code": "case", "name": "Case of 24"},
                             format="json")
        self.assertEqual(r.status_code, 201)
        # a material can now use the new unit…
        r = self.client.post(reverse("ingredient-list"),
                             {"name": "Cola", "unit": "case"}, format="json")
        self.assertEqual(r.status_code, 201)
        # …and a unit in use can't be deleted
        case = next(u for u in self.client.get(reverse("uom-list")).data if u["code"] == "case")
        r = self.client.delete(reverse("uom-detail", args=[case["id"]]))
        self.assertEqual(r.status_code, 400)

    def test_unknown_unit_or_category_rejected(self):
        r = self.client.post(reverse("ingredient-list"),
                             {"name": "Saffron", "unit": "tola"}, format="json")
        self.assertEqual(r.status_code, 400)
        r = self.client.post(reverse("ingredient-list"),
                             {"name": "Saffron", "unit": "g", "category": "Exotic"},
                             format="json")
        self.assertEqual(r.status_code, 400)

    def test_category_master_crud_and_delete_protection(self):
        r = self.client.post(reverse("ingredient-category-list"), {"name": "Exotic"},
                             format="json")
        self.assertEqual(r.status_code, 201)
        cat_id = r.data["id"]
        r = self.client.post(reverse("ingredient-list"),
                             {"name": "Saffron", "unit": "g", "category": "Exotic"},
                             format="json")
        self.assertEqual(r.status_code, 201)
        r = self.client.delete(reverse("ingredient-category-detail", args=[cat_id]))
        self.assertEqual(r.status_code, 400)  # in use

    def test_movements_register_date_range_and_csv(self):
        from django.utils import timezone
        ing = Ingredient.objects.get(name="Rice")
        self.client.post(reverse("ingredient-adjust", args=[ing.id]),
                         {"qty": "5", "reason": "opening"}, format="json")
        today = timezone.localdate().isoformat()
        r = self.client.get(reverse("ingredient-movements") + f"?from={today}&to={today}")
        self.assertEqual(len(r.data), 1)
        r = self.client.get(reverse("ingredient-movements") + "?from=2000-01-01&to=2000-01-02")
        self.assertEqual(len(r.data), 0)
        r = self.client.get(reverse("ingredient-movements") + "?fmt=csv")
        self.assertEqual(r["Content-Type"], "text/csv")
        self.assertIn("Rice", r.content.decode())

    def test_consumption_report_purchase_vs_consumption(self):
        from apps.inventory.models import apply_movement
        ing = Ingredient.objects.get(name="Rice")
        ing.unit_cost = Decimal("60")
        ing.save()
        apply_movement(ing, "receipt", Decimal("10"), source="PO:1")
        apply_movement(ing, "consumption", Decimal("-4"), source="order:1")
        r = self.client.get(reverse("ingredient-consumption-report"))
        row = next(x for x in r.data["rows"] if x["ingredient"] == "Rice")
        self.assertEqual(row["purchased"], Decimal("10"))
        self.assertEqual(row["consumed"], Decimal("4"))
        self.assertEqual(row["consumption_cost"], Decimal("240"))
