"""Pluggable external-provider adapters (BRD Section 9 integrations).

Real providers (Razorpay/Stripe, MSG91/Twilio, etc.) drop in by implementing
these interfaces and pointing the settings at them. The default 'mock' providers
let the full flows run end-to-end without credentials — and keep PCI scope nil
by only ever handling gateway tokens, never card numbers (SR-060).
"""
from decimal import Decimal
from importlib import import_module

from django.conf import settings


# --- Payment gateway -------------------------------------------------------
class BasePaymentProvider:
    name = "base"

    def charge(self, amount: Decimal, token: str, reference: str = "") -> dict:
        raise NotImplementedError


class MockPaymentProvider(BasePaymentProvider):
    name = "mock"

    def charge(self, amount, token, reference=""):
        if not token:
            return {"status": "failed", "reason": "missing payment token"}
        # A real gateway authorises the token; the mock approves and returns a ref.
        ref = f"MOCK-{abs(hash((token, reference))) % 10_000_000:07d}"
        return {"status": "approved", "ref": ref, "amount": str(amount)}


# --- Messaging (SMS / WhatsApp / email) ------------------------------------
class BaseMessagingProvider:
    name = "base"

    def send(self, channel: str, to: str, body: str) -> dict:
        raise NotImplementedError


class MockMessagingProvider(BaseMessagingProvider):
    name = "mock"

    def send(self, channel, to, body):
        return {"status": "sent", "id": f"MSG-{abs(hash((to, body))) % 10_000_000:07d}"}


def _load(path, default):
    target = getattr(settings, path, "") or default
    module, _, cls = target.rpartition(".")
    return getattr(import_module(module), cls)()


def payment_provider() -> BasePaymentProvider:
    return _load("PAYMENT_PROVIDER", "apps.integrations.providers.MockPaymentProvider")


def messaging_provider() -> BaseMessagingProvider:
    return _load("MESSAGING_PROVIDER", "apps.integrations.providers.MockMessagingProvider")
