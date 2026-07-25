"""Encrypt-at-rest for aggregator (Swiggy/Zomato) webhook secrets — the first
secret this app stores encrypted rather than plaintext (contrast with
apps.accounts.User.mfa_secret). Key comes from settings.AGGREGATOR_SECRET_KEY.
"""
from cryptography.fernet import Fernet
from django.conf import settings


def _fernet() -> Fernet:
    return Fernet(settings.AGGREGATOR_SECRET_KEY.encode())


def encrypt(raw: str) -> str:
    return _fernet().encrypt(raw.encode()).decode()


def decrypt(ciphertext: str) -> str:
    return _fernet().decrypt(ciphertext.encode()).decode()
