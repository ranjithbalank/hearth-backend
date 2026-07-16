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
