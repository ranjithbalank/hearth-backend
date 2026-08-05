"""Provision an install for a customer — the vendor-side control point.

Hearth sells three editions and *we* decide which one a customer gets, so that
decision has to live somewhere the customer cannot reach. It lives here. Run
this before handover and the app comes up already licensed; the first-run wizard
then shows the edition as a read-only fact and never offers a choice.

    manage.py provision --edition restaurant --name "Anna's Kitchen"
    manage.py provision --edition both --name "Seaside Grand" --gstin 29ABCDE1234F1Z5
    manage.py provision --show

Re-running it changes the licence — an upgrade from restaurant to both, say —
and leaves the customer's own data and configuration untouched. Because
`Branch` carries its own copy of the four flags (the group-vs-location
duplication that the post-demo model split resolves), an upgrade has to push
the new licence down to every existing branch or they stay on the old one.
"""
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from apps.accounts.constants import LICENSED_FLAGS, edition_entitlements
from apps.accounts.models import Branch, Entitlement, Property, log_action

EDITION_LABEL = {
    "hotel": "Hotel — rooms, front office, banquets, revenue. No restaurant POS.",
    "restaurant": "Restaurant — POS, menu, tables, KDS. No rooms.",
    "both": "Hotel + Restaurant — everything, with F&B posting to the room folio.",
}


class Command(BaseCommand):
    help = "Set the edition this install is licensed for (vendor-side; not customer-reachable)."

    def add_arguments(self, parser):
        parser.add_argument("--edition", choices=sorted(EDITION_LABEL),
                            help="What the customer bought.")
        parser.add_argument("--name", help="Property / brand name.")
        parser.add_argument("--gstin", help="GSTIN, if known at provisioning time.")
        parser.add_argument("--currency", help="Currency code (default INR).")
        parser.add_argument("--show", action="store_true",
                            help="Print the current licence and exit.")

    @transaction.atomic
    def handle(self, *args, **opts):
        prop = Property.objects.select_related("entitlement").first()
        if prop is None:
            prop = Property.objects.create(name=opts.get("name") or "Hearth Property")
        ent, _ = Entitlement.objects.get_or_create(property=prop)

        if opts["show"]:
            return self._show(prop, ent)

        edition = opts["edition"]
        if not edition:
            raise CommandError(
                "--edition is required (hotel | restaurant | both). Use --show to inspect "
                "what this install is currently licensed for."
            )

        was = prop.edition or "unprovisioned"
        flags = edition_entitlements(edition)
        prop.edition = edition
        for field in ("name", "gstin", "currency"):
            if opts.get(field):
                setattr(prop, field, opts[field])
        prop.save()

        for flag, value in flags.items():
            setattr(ent, flag, value)
        ent.save()

        # Push the licence down to existing locations (see module docstring).
        branches = Branch.objects.filter(property=prop)
        branches.update(edition=edition, **flags)

        log_action(None, "provision", entity="Property", entity_id=prop.id,
                   before={"edition": was}, after={"edition": edition},
                   note=f"licensed via manage.py provision ({branches.count()} branch(es) updated)")

        self.stdout.write(self.style.SUCCESS(
            f"Provisioned '{prop.name}' as {edition} (was {was})."))
        self.stdout.write(f"  {EDITION_LABEL[edition]}")
        if branches:
            self.stdout.write(f"  {branches.count()} branch(es) moved onto the new licence.")
        if not prop.setup_done:
            self.stdout.write(
                "  First run will create the owner account, then guided setup.")

    def _show(self, prop, ent):
        self.stdout.write(f"Property : {prop.name}")
        self.stdout.write(f"Edition  : {prop.edition or '(not provisioned)'}")
        self.stdout.write(f"Setup    : {'complete' if prop.setup_done else 'not finished'}")
        self.stdout.write("Licensed :")
        for flag in LICENSED_FLAGS:
            self.stdout.write(f"  {'on ' if getattr(ent, flag) else 'off'}  {flag}")
        branches = Branch.objects.filter(property=prop)
        if branches:
            self.stdout.write("Branches :")
            for b in branches:
                self.stdout.write(f"  {b.code:<10} {b.name} [{b.edition}] {b.status}")
