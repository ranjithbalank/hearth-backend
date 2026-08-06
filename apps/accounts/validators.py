"""Shared field validators for person-name and numeric inputs.

Frontend input filters (lib/inputs.ts) keep bad characters out at the source;
these are the server-side backstop so a direct API call can't slip through
letters in a phone number or digits in a guest's name (BRD data-quality)."""
import re
import unicodedata
from decimal import Decimal

from django.core.validators import MaxValueValidator, MinValueValidator
from rest_framework import serializers

# Field-level bounds, declared on the model so DRF, the Django admin and CSV
# import all inherit the same rule instead of each re-deciding it. Before these,
# no models.py in the project carried a single MinValueValidator: a price or a
# quantity could be saved negative through any path and would then propagate
# into billing and stock with nothing downstream to catch it.
#
# Deliberately NOT applied to genuinely signed columns — inventory movement qty
# ("+ in, - out"), loyalty points (negative on redeem), till variance (short or
# over) and payslip adjustment (a deduction is negative). A floor there would
# break correct behaviour, which is why this is a classification and not a
# blanket sweep.
#
# Decimal bounds, not int: DRF warns ("min_value should be a Decimal instance")
# and compares across types when an int bound guards a DecimalField.
NON_NEGATIVE = [MinValueValidator(Decimal("0"))]
#: A percentage: 0-100 inclusive.
PERCENT = [MinValueValidator(Decimal("0")), MaxValueValidator(Decimal("100"))]

# The punctuation real names use, between the letters.
_NAME_SEPARATORS = " .'-"


def _is_name_letter(ch: str) -> bool:
    """A letter, or a combining mark belonging to one.

    The mark half is not an edge case: in Devanagari, Tamil and most Indic
    scripts the vowel signs ARE marks (Unicode category Mn/Mc), not letters. A
    letters-only rule therefore rejected "मीरा" outright — a guest whose name is
    written in the local script could not be saved at all, on a product built
    for Indian properties. It went unnoticed because Latin accents are single
    precomposed codepoints and sailed through.

    Asking unicodedata for the category, rather than listing ranges, keeps this
    correct for every script with no table to maintain.
    """
    return ch.isalpha() or unicodedata.category(ch).startswith("M")


def validate_person_name(value: str) -> str:
    """A human name: letters/spaces/hyphen/apostrophe/period, no digits.
    Blank passes (callers decide whether the field is required)."""
    v = (value or "").strip()
    if not v:
        return v
    bad = (
        any(ch.isdigit() for ch in v)
        or any(not (_is_name_letter(ch) or ch in _NAME_SEPARATORS) for ch in v)
        # No leading separator, and no two in a row ("Mary--Jane", "a  b").
        or v[0] in _NAME_SEPARATORS
        or any(a in _NAME_SEPARATORS and b in _NAME_SEPARATORS for a, b in zip(v, v[1:]))
        # A trailing "." or "'" is fine ("Jr.", "O'"), a space or hyphen is not.
        or v[-1] in " -"
    )
    if bad:
        raise serializers.ValidationError(
            "Name may contain only letters, spaces, hyphens and apostrophes.")
    return v


def validate_digits(value: str, field="value", min_len=0, max_len=20) -> str:
    """Digits only (passcode, PIN). Blank passes.

    NOT for phone numbers — see validate_phone. This rule used to guard the HR
    phone field, where it rejected the very format the onboarding screen shows
    as its placeholder ("+91 90000 00000").
    """
    v = (value or "").strip()
    if not v:
        return v
    if not v.isdigit():
        raise serializers.ValidationError(f"{field} may contain digits only.")
    if not (min_len <= len(v) <= max_len):
        raise serializers.ValidationError(
            f"{field} must be {min_len}-{max_len} digits.")
    return v


# Everything people put between the digits of a phone number: spaces, brackets,
# hyphens, dots. Stripped before validation so the number a guest reads off a
# card is accepted however they space it, and stored one way.
_PHONE_SEPARATORS_RE = re.compile(r"[\s()\-./]")


def normalize_phone(value: str) -> str:
    """Canonical form of a typed phone number: separators stripped, an ITU
    international prefix ("00") folded to "+". Blank passes.

    The country code is NOT invented. A number typed without one stays national
    — Hearth cannot know which country a bare 10-digit number belongs to, and
    guessing is how a guest record becomes undialable. New numbers get a country
    code from the picker (Property.default_country_code); existing ones keep
    whatever they were entered as until someone edits them.
    """
    v = _PHONE_SEPARATORS_RE.sub("", (value or "").strip())
    if not v:
        return ""
    if v.startswith("00"):
        v = "+" + v[2:]
    return v


def validate_phone(value: str, field="Phone") -> str:
    """A phone number, international (+<country><number>) or national. Blank passes.

    Bounds follow ITU-T E.164: at most 15 digits including the country code.
    This is the single phone rule for the whole product — before it, HR demanded
    digits only while User.phone, Property.phone and Customer.mobile accepted
    literally anything, so the same guest's number could be stored four ways and
    lookup by phone could not be trusted.
    """
    v = normalize_phone(value)
    if not v:
        return v
    international = v.startswith("+")
    digits = v[1:] if international else v
    if not digits.isdigit():
        raise serializers.ValidationError(
            f"{field} may contain only digits, with an optional leading + and country code.")
    low = 8 if international else 6
    if not (low <= len(digits) <= 15):
        raise serializers.ValidationError(
            f"{field} must be {low}-15 digits"
            + (" including the country code." if international else "."))
    return v


def phone_variants(value: str, default_code: str = "+91") -> list:
    """Every spelling one number might already be stored as, most canonical first.

    Hearth accumulated three conventions for the same field: bare national
    ("9876543210"), code-plus-space ("+44 7700123456", written by POS for
    non-Indian guests only), and now the canonical unspaced form. A guest whose
    record predates the canonical form must still be *found*, or the counter
    silently creates a second profile for them and their loyalty balance splits
    in two.

    Used for lookup, never for writing — new rows are always written canonical.
    """
    canonical = normalize_phone(value)
    if not canonical:
        return []
    out = [canonical]
    if canonical.startswith("+"):
        digits = canonical[1:]
        for code in (default_code.lstrip("+"), ):
            if code and digits.startswith(code):
                national = digits[len(code):]
                out += [national, f"+{code} {national}"]
        # A code we don't know: still try the spaced form on the trailing 10.
        if len(digits) > 10:
            out.append(f"+{digits[:-10]} {digits[-10:]}")
    else:
        out.append(f"{default_code}{canonical}")
        out.append(f"{default_code} {canonical}")
    seen = set()
    return [v for v in out if v and not (v in seen or seen.add(v))]


def normalize_email(value: str) -> str:
    """Lower-case the domain half, as Django's own user manager does. The local
    part is left alone (RFC 5321 makes it case-sensitive, even though almost no
    provider treats it so); uniqueness is compared case-insensitively anyway."""
    v = (value or "").strip()
    if "@" not in v:
        return v
    local, _, domain = v.rpartition("@")
    return f"{local}@{domain.lower()}"


def validate_date_order(start, end, start_label="check-in", end_label="check-out"):
    """`end` must fall strictly after `start`. Either being None passes — a
    required-field rule is the field's own job, not this one's.

    Reversed dates are not cosmetic: nights is derived from the pair, so a
    reversed stay posts negative room nights into revenue, occupancy and ADR
    with nothing downstream to catch it. Hearth already enforced this on channel
    ingest and on stay extension; this is that rule for everyone else.
    """
    if start is None or end is None:
        return
    if end <= start:
        raise serializers.ValidationError(
            f"The {end_label} date must be after the {start_label} date.")


class CaseInsensitiveUniqueMixin:
    """Serializer mixin: reject a value that duplicates an existing row except
    for capitalisation. Set `ci_unique_fields` on the serializer.

    `unique=True` is case-SENSITIVE in both SQLite and Postgres, so nothing
    stopped a second login called `ABISHEK1828` alongside `abishek1828`, or a
    second department called "kitchen" beside "Kitchen". Two rows that read as
    one thing are worse than a rejected save everywhere, and actively dangerous
    where the string itself is the key: indent and leave approvals route on the
    exact department name, so the lower-case twin silently has no approver.
    """

    ci_unique_fields: list = []
    #: Optional per-field override of the clash message, keyed by field name and
    #: formatted with {value}. The default message talks about capitals, which
    #: is right for a name and wrong for anything else (an email clash is about
    #: identity, not spelling).
    ci_unique_messages: dict = {}

    def validate(self, attrs):
        attrs = super().validate(attrs)
        model = self.Meta.model
        for field in self.ci_unique_fields:
            value = attrs.get(field)
            if not isinstance(value, str) or not value.strip():
                continue
            qs = model.objects.filter(**{f"{field}__iexact": value.strip()})
            if self.instance is not None:
                qs = qs.exclude(pk=self.instance.pk)
            clash = qs.first()
            if clash is not None:
                existing = getattr(clash, field)
                override = self.ci_unique_messages.get(field)
                raise serializers.ValidationError({field: (
                    override.format(value=existing) if override else
                    f"'{existing}' already exists. Names that differ only in "
                    f"capitals would be two separate records.")})
        return attrs


# Usernames: the frontend already filters to this set; this is the backstop.
_USERNAME_RE = re.compile(r"^[a-z0-9][a-z0-9._-]*$")


def validate_username(value: str) -> str:
    """Normalise a login name to its canonical (lower-case) form.

    Case-folding here rather than only comparing case-insensitively means there
    is exactly one spelling of any account, so a username can't be typed one way
    in Settings and another way at the sign-in box.
    """
    v = (value or "").strip().lower()
    # Two, not three: the roles Hearth itself ships sign in as `md`, `gm`, `hr`,
    # and a rule the product's own demo logins fail is the wrong rule.
    if len(v) < 2:
        raise serializers.ValidationError("Username must be at least 2 characters.")
    if not _USERNAME_RE.match(v):
        raise serializers.ValidationError(
            "Username may contain letters, numbers, dots, underscores and hyphens, "
            "and must start with a letter or number.")
    return v
