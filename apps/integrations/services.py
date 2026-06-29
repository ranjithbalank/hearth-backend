"""Thin service helpers over the provider adapters."""
from .models import Otp, SentMessage
from .providers import messaging_provider, payment_provider


def charge_card(amount, token, reference=""):
    """Charge via the configured gateway. Only a token is accepted (PCI-safe)."""
    return payment_provider().charge(amount, token, reference)


def notify(channel, to, body):
    """Send a notification and log it."""
    if not to:
        return None
    result = messaging_provider().send(channel, to, body)
    return SentMessage.objects.create(
        channel=channel, to=to, body=body,
        status=result.get("status", "sent"), provider_id=result.get("id", ""),
    )


def send_otp(mobile):
    otp = Otp.issue(mobile)
    notify("sms", mobile, f"Your Hearth verification code is {otp.code}")
    return otp


def verify_otp(mobile, code):
    otp = Otp.objects.filter(mobile=mobile, verified=False).order_by("-created_at").first()
    if otp and otp.is_valid(code):
        otp.verified = True
        otp.save(update_fields=["verified"])
        return True
    return False
