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

    def send(self, channel: str, to: str, body: str, subject: str | None = None) -> dict:
        raise NotImplementedError


class MockMessagingProvider(BaseMessagingProvider):
    name = "mock"

    def send(self, channel, to, body, subject=None):
        return {"status": "sent", "id": f"MSG-{abs(hash((to, body))) % 10_000_000:07d}"}


class EmailMessagingProvider(BaseMessagingProvider):
    """Sends the `email` channel for real, through Django's configured mail
    backend (SMTP in prod, console in dev — see EMAIL_BACKEND).

    The default MockMessagingProvider returns a fabricated id and transmits
    nothing, which is right for tests and demos and wrong the moment a real
    person clicks "forgot password" — the reset link is generated, logged and
    then dropped on the floor.

    SMS and WhatsApp are deliberately NOT faked here. A mail backend cannot
    send them, and returning "sent" for a message that was never transmitted is
    the failure this class exists to remove — so they are recorded as
    `unsupported` and the SentMessage row tells the truth. Point
    MESSAGING_PROVIDER at a gateway adapter when you need those channels.
    """

    name = "email"

    def send(self, channel, to, body, subject=None):
        if channel != "email":
            return {"status": "unsupported", "id": ""}
        from django.conf import settings
        from django.core.mail import EmailMessage

        msg = EmailMessage(
            subject=subject or "Hearth notification",
            body=body,
            from_email=getattr(settings, "DEFAULT_FROM_EMAIL", None) or None,
            to=[to],
        )
        # fail_silently=False: a bounced SMTP connection must surface, not be
        # swallowed into a SentMessage row that claims success.
        sent = msg.send(fail_silently=False)
        return {"status": "sent" if sent else "failed", "id": ""}


def _load(path, default):
    target = getattr(settings, path, "") or default
    module, _, cls = target.rpartition(".")
    return getattr(import_module(module), cls)()


def payment_provider() -> BasePaymentProvider:
    return _load("PAYMENT_PROVIDER", "apps.integrations.providers.MockPaymentProvider")


def messaging_provider() -> BaseMessagingProvider:
    return _load("MESSAGING_PROVIDER", "apps.integrations.providers.MockMessagingProvider")
