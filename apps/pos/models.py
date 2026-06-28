from decimal import Decimal

from django.db import models

from apps.crm.models import Customer
from apps.frontoffice.models import Folio


class Table(models.Model):
    FREE = "free"
    RUNNING = "running"
    PRINTED = "printed"
    PAID = "paid"
    RESERVED = "reserved"
    STATUS_CHOICES = [
        (FREE, "Free"),
        (RUNNING, "Running"),
        (PRINTED, "Printed"),
        (PAID, "Paid"),
        (RESERVED, "Reserved"),
    ]

    name = models.CharField(max_length=20)
    section = models.CharField(max_length=40, default="Main")
    seats = models.PositiveSmallIntegerField(default=4)
    shape = models.CharField(max_length=20, default="square")
    status = models.CharField(max_length=12, choices=STATUS_CHOICES, default=FREE)

    class Meta:
        ordering = ["section", "name"]

    def __str__(self):
        return self.name


class Category(models.Model):
    name = models.CharField(max_length=80)
    sort_order = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ["sort_order", "name"]
        verbose_name_plural = "categories"

    def __str__(self):
        return self.name


class MenuItem(models.Model):
    VEG = "veg"
    NONVEG = "nonveg"
    EGG = "egg"
    DIET_CHOICES = [(VEG, "Veg"), (NONVEG, "Non-veg"), (EGG, "Egg")]

    name = models.CharField(max_length=120)
    short_code = models.CharField(max_length=20, blank=True)
    category = models.ForeignKey(Category, on_delete=models.PROTECT, related_name="items")
    price = models.DecimalField(max_digits=10, decimal_places=2)
    gst_rate = models.DecimalField(max_digits=4, decimal_places=1, default=5)
    diet = models.CharField(max_length=10, choices=DIET_CHOICES, default=VEG)
    station = models.CharField(max_length=20, default="kitchen", help_text="kitchen | bar")
    available = models.BooleanField(default=True)

    class Meta:
        ordering = ["category__sort_order", "name"]

    def __str__(self):
        return self.name


class Order(models.Model):
    DINEIN = "dinein"
    TAKEAWAY = "takeaway"
    DELIVERY = "delivery"
    MODE_CHOICES = [(DINEIN, "Dine-in"), (TAKEAWAY, "Takeaway"), (DELIVERY, "Delivery")]

    OPEN = "open"
    KOT_FIRED = "kot_fired"
    BILLED = "billed"
    SETTLED = "settled"
    POSTED_TO_ROOM = "posted_to_room"
    STATUS_CHOICES = [
        (OPEN, "Open"),
        (KOT_FIRED, "KOT Fired"),
        (BILLED, "Billed"),
        (SETTLED, "Settled"),
        (POSTED_TO_ROOM, "Posted to Room"),
    ]

    mode = models.CharField(max_length=12, choices=MODE_CHOICES, default=DINEIN)
    table = models.ForeignKey(
        Table, on_delete=models.SET_NULL, null=True, blank=True, related_name="orders"
    )
    customer = models.ForeignKey(
        Customer, on_delete=models.SET_NULL, null=True, blank=True, related_name="orders"
    )
    covers = models.PositiveSmallIntegerField(default=1)
    captain = models.CharField(max_length=80, blank=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=OPEN)
    folio = models.ForeignKey(
        Folio, on_delete=models.SET_NULL, null=True, blank=True, related_name="pos_orders"
    )
    kot_no = models.CharField(max_length=20, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"Order #{self.id} ({self.mode})"

    def totals(self):
        from apps.tax import service as tax
        taxable = cgst = sgst = total = Decimal("0")
        for line in self.lines.all():
            b = tax.compute(line.unit_price * line.qty, line.menu_item.gst_rate)
            taxable += b["taxable"]
            cgst += b["cgst"]
            sgst += b["sgst"]
            total += b["total"]
        return {"taxable": taxable, "cgst": cgst, "sgst": sgst,
                "tax": cgst + sgst, "total": total}


class OrderLine(models.Model):
    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name="lines")
    menu_item = models.ForeignKey(MenuItem, on_delete=models.PROTECT)
    qty = models.PositiveSmallIntegerField(default=1)
    unit_price = models.DecimalField(max_digits=10, decimal_places=2)
    note = models.CharField(max_length=120, blank=True)
    kot_fired = models.BooleanField(default=False)

    def __str__(self):
        return f"{self.qty}× {self.menu_item.name}"
