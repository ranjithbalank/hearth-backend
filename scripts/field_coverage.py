"""Field-coverage matrix — the "every mm" sweep.

Walks every registered DRF serializer field in the project and reports what
guards it: a type, a bound (min/max/length/choices), whether it is required,
and whether the value is normalised. The point is that this is an *inventory*,
not a hunt — the Aug-2026 audit found defects that were all the same shape (a
rule the codebase already had, not applied on every path), and no amount of
looking harder prevents the next one. A list of every field and what protects
it does.

Run:  python manage.py shell < scripts/field_coverage.py
  or: DJANGO_SETTINGS_MODULE=hearth.settings.dev python scripts/field_coverage.py
Writes scripts/field_coverage.csv and prints the gaps.
"""
import csv
import os
import sys

import django

if not os.environ.get("DJANGO_SETTINGS_MODULE"):
    os.environ["DJANGO_SETTINGS_MODULE"] = "hearth.settings.dev"
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
django.setup()

from rest_framework import serializers  # noqa: E402
from rest_framework.routers import DefaultRouter  # noqa: E402

# Field types where "no bound" is a finding rather than a non-sequitur. A
# BooleanField needs no floor; a DecimalField holding money does.
NEEDS_BOUND = (
    serializers.DecimalField, serializers.IntegerField, serializers.FloatField,
    serializers.CharField, serializers.EmailField,
)
# Free-text fields where a length cap is the only sane bound and the model
# already carries it — flagged only if even that is missing.
TEXTISH = (serializers.CharField, serializers.EmailField)


def bounds_of(field):
    out = []
    for attr, label in (("min_value", "min"), ("max_value", "max"),
                        ("min_length", "minlen"), ("max_length", "maxlen")):
        v = getattr(field, attr, None)
        if v is not None:
            out.append(f"{label}={v}")
    if getattr(field, "choices", None):
        out.append("choices")
    # Validators attached on the model (MinValueValidator et al.) show up here.
    for v in getattr(field, "validators", []):
        name = type(v).__name__
        if name not in {"UniqueValidator", "ProhibitSurrogateCharactersValidator"}:
            out.append(name.replace("Validator", "").lower())
    return ",".join(dict.fromkeys(out))


def walk():
    from hearth import urls as root_urls

    seen, rows = set(), []
    for pattern in _viewsets(root_urls.urlpatterns):
        vs, prefix = pattern
        ser = _serializer_of(vs)
        if ser is None or type(ser) in seen:
            continue
        seen.add(type(ser))
        name = type(ser).__name__
        for fname, field in ser.fields.items():
            if field.read_only:
                continue
            has_validate = hasattr(ser, f"validate_{fname}")
            rows.append({
                "serializer": name,
                "endpoint": prefix,
                "field": fname,
                "type": type(field).__name__,
                "required": "yes" if field.required else "no",
                "bounds": bounds_of(field) or "-",
                "normalised": "yes" if has_validate else "no",
            })
    return rows


def _viewsets(patterns, prefix=""):
    """Every ViewSet the router knows about, with its URL prefix."""
    found = []
    for p in patterns:
        if hasattr(p, "url_patterns"):
            found += _viewsets(p.url_patterns, prefix + str(p.pattern))
            continue
        cb = getattr(p, "callback", None)
        cls = getattr(cb, "cls", None)
        if cls is not None and hasattr(cls, "serializer_class"):
            found.append((cls, prefix + str(p.pattern)))
    return found


def _serializer_of(viewset):
    try:
        ser = viewset.serializer_class
        return ser() if ser is not None else None
    except Exception:
        return None


rows = walk()
out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "field_coverage.csv")
with open(out, "w", newline="", encoding="utf-8") as fh:
    w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
    w.writeheader()
    w.writerows(rows)

gaps = [r for r in rows
        if r["bounds"] == "-" and r["normalised"] == "no"
        and r["type"] in {f.__name__ for f in NEEDS_BOUND}]
print(f"{len(rows)} writable fields across {len({r['serializer'] for r in rows})} serializers")
print(f"{len(gaps)} with neither a bound nor a normaliser\n")
for g in sorted(gaps, key=lambda r: (r["serializer"], r["field"]))[:60]:
    print(f"  {g['serializer']:<34} {g['field']:<26} {g['type']:<18} required={g['required']}")
print(f"\nfull matrix -> {out}")
