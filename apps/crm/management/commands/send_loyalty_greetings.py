"""Birthday/anniversary loyalty automation.

There is no in-app scheduler (no Celery/cron) — this command is meant to
be invoked once a day by an external OS-level scheduler (cron / Windows
Task Scheduler / the hosting platform's scheduled-job feature):

    python manage.py send_loyalty_greetings

Consent-gated (marketing_consent), deduped per customer/occasion/day via
LoyaltyGreetingLog so a re-run on the same day never double-pays the bonus.
"""
from django.core.management.base import BaseCommand
from django.utils import timezone

from apps.crm.models import Customer, LoyaltyGreetingLog, LoyaltyLedger

BONUS_POINTS = 50

OCCASIONS = [
    ("birthday", "date_of_birth", "Happy birthday"),
    ("anniversary", "anniversary_date", "Happy anniversary"),
]


class Command(BaseCommand):
    help = "Send birthday/anniversary greetings with a bonus-points top-up to consented customers."

    def handle(self, *args, **options):
        from apps.integrations import services as integ

        today = timezone.localdate()
        sent = 0
        for occasion, date_field, greeting in OCCASIONS:
            qs = Customer.objects.filter(
                marketing_consent=True,
                **{f"{date_field}__month": today.month, f"{date_field}__day": today.day},
            ).exclude(**{f"{date_field}__isnull": True})
            for cust in qs:
                if not cust.mobile or LoyaltyGreetingLog.objects.filter(
                        customer=cust, occasion=occasion, sent_on=today).exists():
                    continue
                cust.loyalty_points += BONUS_POINTS
                cust.lifetime_points += BONUS_POINTS
                cust.save(update_fields=["loyalty_points", "lifetime_points"])
                LoyaltyLedger.objects.create(
                    customer=cust, kind=LoyaltyLedger.BONUS, points=BONUS_POINTS,
                    balance_after=cust.loyalty_points, note=f"{occasion.capitalize()} bonus")
                integ.notify("sms", cust.mobile,
                             f"{greeting}, {cust.name}! Enjoy {BONUS_POINTS} bonus loyalty points on us.")
                LoyaltyGreetingLog.objects.create(customer=cust, occasion=occasion, sent_on=today)
                sent += 1
        self.stdout.write(f"Sent {sent} greeting(s) for {today}.")
