"""TOTP multi-factor authentication (BRD SR-040)."""
import pyotp
from django.conf import settings

ISSUER = "Hearth OS"


def new_secret() -> str:
    return pyotp.random_base32()


def provisioning_uri(user, secret: str) -> str:
    label = user.email or user.username
    return pyotp.TOTP(secret).provisioning_uri(name=label, issuer_name=ISSUER)


def verify(secret: str, code: str) -> bool:
    if not secret or not code:
        return False
    return pyotp.TOTP(secret).verify(str(code).strip(), valid_window=1)


def role_requires_mfa(role: str) -> bool:
    """Roles for which MFA is mandatory by policy. Empty in dev so demo logins work;
    set MFA_ENFORCED_ROLES in production (e.g. the privileged roles)."""
    return role in getattr(settings, "MFA_ENFORCED_ROLES", [])
