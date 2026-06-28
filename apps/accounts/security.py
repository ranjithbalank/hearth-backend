"""Cross-cutting security helpers (BRD Section 6).

- SecurityHeadersMiddleware: applies hardening headers in every environment.
- BreachedPasswordValidator: rejects obviously-weak/known-breached passwords.
- mask_pii: redacts emails/phones before they reach logs (data minimisation).
"""
import re

from django.core.exceptions import ValidationError

# A tiny embedded sample of the most-common breached passwords. In production
# this is replaced by a k-anonymity check against HaveIBeenPwned (SR-042).
_BREACHED = {
    "password", "123456", "123456789", "qwerty", "12345678", "111111",
    "1234567890", "admin", "letmein", "welcome", "abc123", "password1",
    "hearth123",
}

_EMAIL_RE = re.compile(r"([A-Za-z0-9._%+-])[A-Za-z0-9._%+-]*(@[A-Za-z0-9.-]+)")
_PHONE_RE = re.compile(r"\b(\d{2})\d{4,8}(\d{2})\b")


class BreachedPasswordValidator:
    """Screens passwords against a known-breached list (NIST SP 800-63, SR-042)."""

    def validate(self, password, user=None):
        if password.lower() in _BREACHED:
            raise ValidationError(
                "This password has appeared in a known data breach. Choose another.",
                code="password_breached",
            )

    def get_help_text(self):
        return "Your password must not appear in any known data breach."


def mask_pii(text: str) -> str:
    """Redact emails and long digit runs (phones) for safe logging (SR-053)."""
    if not text:
        return text
    text = _EMAIL_RE.sub(r"\1***\2", text)
    text = _PHONE_RE.sub(r"\1****\2", text)
    return text


class SecurityHeadersMiddleware:
    """Adds defence-in-depth response headers (OWASP A05, SR-101-adjacent)."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        response.setdefault("X-Content-Type-Options", "nosniff")
        response.setdefault("X-Frame-Options", "DENY")
        response.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        response.setdefault("Permissions-Policy", "geolocation=(), microphone=(), camera=()")
        response.setdefault(
            "Content-Security-Policy",
            "default-src 'self'; img-src 'self' data: blob:; "
            "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
            "font-src 'self' https://fonts.gstatic.com; connect-src 'self'",
        )
        return response
