"""Shared field validators for person-name and numeric inputs.

Frontend input filters (lib/inputs.ts) keep bad characters out at the source;
these are the server-side backstop so a direct API call can't slip through
letters in a phone number or digits in a guest's name (BRD data-quality)."""
import re

from rest_framework import serializers

# Letters (any script), spaces, and the punctuation real names use.
_NAME_RE = re.compile(r"^[^\W\d_]+(?:[\s.'-][^\W\d_]+)*[.']?$", re.UNICODE)


def validate_person_name(value: str) -> str:
    """A human name: letters/spaces/hyphen/apostrophe/period, no digits.
    Blank passes (callers decide whether the field is required)."""
    v = (value or "").strip()
    if not v:
        return v
    if any(ch.isdigit() for ch in v) or not _NAME_RE.match(v):
        raise serializers.ValidationError(
            "Name may contain only letters, spaces, hyphens and apostrophes.")
    return v


def validate_digits(value: str, field="value", min_len=0, max_len=20) -> str:
    """Digits only (phone, passcode, PIN). Blank passes."""
    v = (value or "").strip()
    if not v:
        return v
    if not v.isdigit():
        raise serializers.ValidationError(f"{field} may contain digits only.")
    if not (min_len <= len(v) <= max_len):
        raise serializers.ValidationError(
            f"{field} must be {min_len}-{max_len} digits.")
    return v
