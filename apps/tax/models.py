from django.db import models


class GstSlab(models.Model):
    """Configurable GST rate slab applied per revenue category (BRD 5.23 / GST Master)."""

    name = models.CharField(max_length=80, unique=True)
    rate = models.DecimalField(max_digits=4, decimal_places=1)
    hsn_sac = models.CharField(max_length=12, blank=True)
    applies_to = models.CharField(max_length=40, blank=True, help_text="rooms | fnb | banquet | service")

    class Meta:
        ordering = ["rate"]

    def __str__(self):
        return f"{self.name} ({self.rate}%)"


# A folio line records what KIND of revenue it is (room / fnb / incidental);
# the SAC code that has to appear against it on a tax invoice lives on the
# matching GST slab. This is the join between the two.
_KIND_TO_APPLIES = {"room": "rooms", "fnb": "fnb", "incidental": "service",
                    "banquet": "banquet"}

# Statutory fallbacks for a property that hasn't filled in its own slabs yet —
# accommodation and restaurant services both sit under SAC heading 9963.
_DEFAULT_SAC = {"rooms": "996311", "fnb": "996331", "banquet": "996334",
                "service": "9963"}


def hsn_for_kinds(kinds):
    """{folio-line kind: SAC code} for the kinds actually on a document.

    Rule 46(f) requires the HSN/SAC against each line of a tax invoice. The
    codes are already configured per slab in GST Master, so this reads them
    rather than hardcoding — falling back to the standard 9963 headings only
    where the property hasn't set one.
    """
    wanted = {_KIND_TO_APPLIES.get(k) for k in kinds} - {None}
    configured = {s.applies_to: s.hsn_sac for s in
                  GstSlab.objects.filter(applies_to__in=wanted).exclude(hsn_sac="")}
    out = {}
    for kind in kinds:
        applies = _KIND_TO_APPLIES.get(kind)
        if applies:
            out[kind] = configured.get(applies) or _DEFAULT_SAC.get(applies, "")
    return out
