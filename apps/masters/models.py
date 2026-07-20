from django.db import models


class Department(models.Model):
    """Department master (Settings > Masters). Seeds from the classic six;
    extendable at runtime. HR employees and material-request indents store
    the department NAME (no FK) so history survives edits; indents for a
    department without a dedicated approver route to GM/MD/Super Admin
    (see apps.accounts.constants.indent_approvers_for)."""

    name = models.CharField(max_length=80, unique=True)
    active = models.BooleanField(default=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class Designation(models.Model):
    """Job-designation master for HR staff records (Chef de Partie, Steward…).
    Distinct from system ROLES (login permissions) — a designation is what's
    printed on the duty roster, not what the person may click."""

    name = models.CharField(max_length=80, unique=True)
    active = models.BooleanField(default=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class KitchenStation(models.Model):
    """Kitchen department master (Settings > Masters) — Grill, Chinese, Indian,
    Tandoor, Bar, etc. Each menu item is mapped to one station; firing a KOT
    splits the order's new lines into one ticket per station represented.

    A station's `mode` decides what firing produces: `kds` puts the ticket on
    the live Kitchen Display same as today; `print` is for owners who don't
    run a screen at all for that section — the ticket auto-prints once and
    never appears on any live board (it's created already "served").

    The bar/drinks station is just another row here (`is_bar=True`), replacing
    the old hardcoded `MenuItem.station == "bar"` check — that flag is what
    still auto-adds an item to Bar POS's own menu (`MenuItem.bar_menu`).
    """

    KDS = "kds"
    PRINT = "print"
    MODE_CHOICES = [(KDS, "Kitchen Display"), (PRINT, "Print only")]

    name = models.CharField(max_length=60, unique=True)
    mode = models.CharField(max_length=6, choices=MODE_CHOICES, default=KDS)
    is_bar = models.BooleanField(default=False, help_text="drives MenuItem.bar_menu auto-assignment")
    active = models.BooleanField(default=True)

    class Meta:
        ordering = ["-is_bar", "name"]

    def __str__(self):
        return self.name


class PaymentMethod(models.Model):
    """Settlement tender master. The three builtins (Cash / UPI / Gateway)
    carry hardwired behavior — Gateway routes through the card provider,
    Cash feeds the drawer count — so they can be deactivated but never
    renamed or deleted. Custom tenders (e.g. "Sodexo", "Room Charge") get
    their behavior from the two flags instead:

      counts_as_cash  — settled amounts are expected in the physical drawer
                        at till close (variance math includes them).
      captain_allowed — captains/bar captains may settle this tableside;
                        off means cashier-counter roles only (BRD 5.10).
    """

    name = models.CharField(max_length=40, unique=True)
    active = models.BooleanField(default=True)
    counts_as_cash = models.BooleanField(default=False)
    captain_allowed = models.BooleanField(default=True)
    builtin = models.BooleanField(default=False)

    class Meta:
        ordering = ["-builtin", "id"]

    def __str__(self):
        return self.name
