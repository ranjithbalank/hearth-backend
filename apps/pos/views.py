from decimal import Decimal

from django.db import models, transaction
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.constants import ROLE_CAPTAIN, ROLE_CASHIER, ROLE_BAR_CAPTAIN, ROLE_BAR_CASHIER
from apps.accounts.rbac import PROTECTED
from apps.accounts.models import log_action
from apps.accounts.permissions import (
    AnyModuleViewSetMixin,
    BranchScopedMixin,
    BranchUniqueFriendlyMixin,
    ModuleViewSetMixin,
    active_entitlements,
    resolve_active_branch,
    shared_or_visible,
    visible_branch_ids,
)
from apps.frontoffice import services as fo_services
from apps.frontoffice.models import Folio, FolioLine, Settlement

from .models import (
    AddOn,
    BarTable,
    Category,
    Coupon,
    Feedback,
    Kot,
    MenuItem,
    Order,
    OrderLine,
    Table,
    TableReservation,
    TillEntry,
    TillSession,
    Variant,
)
from .serializers import (
    BarTableSerializer,
    CategorySerializer,
    MenuItemSerializer,
    OrderSerializer,
    TableReservationSerializer,
    TableSerializer,
    TillSessionSerializer,
    captain_on_leave_today,
)


def _within_cap(user, subtotal, discount_amount):
    """Enforce the per-user discount cap (BRD FR-USR-004). Returns (ok, message)."""
    cap_type = getattr(user, "discount_cap_type", "none")
    cap_value = Decimal(str(getattr(user, "discount_cap_value", 0) or 0))
    if cap_type == "none" or subtotal <= 0:
        return True, ""
    if cap_type == "percent":
        eff = discount_amount / subtotal * Decimal("100")
        if eff > cap_value:
            return False, f"Discount {eff:.1f}% exceeds your {cap_value}% cap"
    elif cap_type == "fixed":
        if discount_amount > cap_value:
            return False, f"Discount {discount_amount} exceeds your fixed cap of {cap_value}"
    return True, ""


def _valid_override(passcode):
    """A manager (full-access role) with a matching passcode authorises the action."""
    from apps.accounts.models import User
    if not passcode:
        return None
    return User.objects.filter(
        role__in=["Managing Director", "General Manager"], passcode=passcode, is_active=True
    ).first()


# Statuses that end an order's life — no further edits, KOTs or payments.
CLOSED_STATUSES = (Order.SETTLED, Order.POSTED_TO_ROOM)


def _closed_error(order):
    """409 if the order is already settled/posted; None if it is still live."""
    if order.status in CLOSED_STATUSES:
        return Response(
            {"detail": f"Order is already {order.get_status_display().lower()} — no further changes allowed"},
            status=409,
        )
    return None


def _billed_error(order):
    """409 if the final bill is printed — the order is locked until reopened."""
    if order.status == Order.BILLED:
        return Response(
            {"detail": "Final bill already printed — reopen the order to make changes",
             "billed_locked": True},
            status=409,
        )
    return None




class TableViewSet(BranchScopedMixin, BranchUniqueFriendlyMixin, ModuleViewSetMixin, viewsets.ModelViewSet):
    module = "pos"
    queryset = Table.objects.all()
    serializer_class = TableSerializer
    duplicate_message = "A table with this name already exists there."

    def list(self, request, *args, **kwargs):
        refresh_reservation_holds()   # time-based holds without a scheduler
        return super().list(request, *args, **kwargs)

    def perform_destroy(self, instance):
        # Never delete a table mid-service — settle or void the running
        # order first, then remove the table. (Order.table is SET_NULL, so
        # deleting would otherwise silently orphan an active bill.)
        if instance.orders.exclude(status__in=CLOSED_STATUSES).exists():
            from rest_framework.exceptions import ValidationError
            raise ValidationError({"detail": f"table {instance.name} has a running order — settle or void it before deleting the table"})
        instance.delete()

    @action(detail=False, methods=["get"])
    def captains(self, request):
        """Captain logins for one branch — lets the F&B Cashier build the
        assignment list without needing Settings/Users access (that's gated
        to the 'settings' module, which Cashiers never get)."""
        from apps.accounts.models import User
        from apps.accounts.models import UserBranchAccess
        from datetime import date

        location = request.query_params.get("location")
        qs = User.objects.filter(role=ROLE_CAPTAIN, is_active=True)
        if location:
            today = date.today()
            ids = [a.user_id for a in UserBranchAccess.objects.filter(branch_id=location) if a.is_active_on(today)]
            qs = qs.filter(id__in=ids)
        qs = qs.distinct().order_by("first_name", "username")
        return Response([{"id": u.id, "name": u.get_full_name() or u.username} for u in qs])

    @action(detail=True, methods=["post"])
    def assign_captain(self, request, pk=None):
        """Hand a table to a captain for the shift — the F&B Cashier runs the
        floor and decides who's working which tables. Captains themselves
        can't self-assign (assigned_captain is read-only on the main
        serializer); send captain=null to clear an assignment."""
        role = getattr(request.user, "role", "")
        if role != ROLE_CASHIER and role not in PROTECTED:
            return Response({"detail": "Only the F&B Cashier can assign tables to captains"}, status=403)
        table = self.get_object()
        captain_id = request.data.get("captain")
        if captain_id is None:
            table.assigned_captain = None
        else:
            from apps.accounts.models import User
            captain = User.objects.filter(pk=captain_id, role=ROLE_CAPTAIN).first()
            if not captain:
                return Response({"detail": "Not a Captain — pick a valid captain login"}, status=400)
            table.assigned_captain = captain
        table.save(update_fields=["assigned_captain"])
        log_action(request.user, "table_assign_captain", entity="Table", entity_id=table.id,
                   after={"assigned_captain": captain_id})
        return Response(TableSerializer(table).data)


class BarTableViewSet(BranchScopedMixin, BranchUniqueFriendlyMixin, ModuleViewSetMixin, viewsets.ModelViewSet):
    """The bar's own floor plan — separate master from the restaurant's
    Table Master (spec: bar runs as its own operation)."""

    module = "barpos"
    queryset = BarTable.objects.all()
    serializer_class = BarTableSerializer
    duplicate_message = "A bar table with this name already exists there."


class CounterOnlyMixin:
    """Cash controls (till, reconciliation) belong to counter roles — captains
    take orders and digital payments tableside, never the cash drawer."""

    def initial(self, request, *args, **kwargs):
        super().initial(request, *args, **kwargs)
        if getattr(request.user, "role", "") == "Captain":
            from rest_framework.exceptions import PermissionDenied
            raise PermissionDenied("Till and reconciliation are handled at the cashier counter")


class TillViewSet(CounterOnlyMixin, ModuleViewSetMixin, viewsets.ViewSet):
    """Cash till sessions: open float → cash in/out → day-end close with variance."""

    module = "pos"

    def list(self, request):
        return Response(TillSessionSerializer(TillSession.objects.all()[:30], many=True).data)

    @action(detail=False, methods=["get"])
    def current(self, request):
        s = TillSession.objects.filter(status=TillSession.OPEN).first()
        return Response(TillSessionSerializer(s).data if s else None)

    @action(detail=False, methods=["post"])
    def open(self, request):
        if TillSession.objects.filter(status=TillSession.OPEN).exists():
            return Response({"detail": "A till session is already open — close it first"}, status=400)
        s = TillSession.objects.create(
            opened_by=request.user.username,
            opening_float=Decimal(str(request.data.get("opening_float", 0))),
        )
        log_action(request.user, "till_open", entity="TillSession", entity_id=s.id,
                   after={"float": str(s.opening_float)})
        return Response(TillSessionSerializer(s).data, status=201)

    @action(detail=True, methods=["post"])
    def entry(self, request, pk=None):
        """Cash paid in/out mid-shift (petty cash, change top-up, bank drop)."""
        s = TillSession.objects.filter(pk=pk, status=TillSession.OPEN).first()
        if not s:
            return Response({"detail": "open till session not found"}, status=404)
        kind = request.data.get("kind")
        amount = Decimal(str(request.data.get("amount", 0)))
        reason = request.data.get("reason", "").strip()
        if kind not in ("in", "out") or amount <= 0 or not reason:
            return Response({"detail": "kind in/out, positive amount and a reason are required"},
                            status=400)
        TillEntry.objects.create(session=s, kind=kind, amount=amount, reason=reason,
                                 created_by=request.user.username)
        log_action(request.user, f"till_cash_{kind}", entity="TillSession", entity_id=s.id,
                   after={"amount": str(amount), "reason": reason})
        return Response(TillSessionSerializer(s).data)

    @action(detail=True, methods=["post"])
    def close(self, request, pk=None):
        """Day-end close: count the drawer; variance = counted − expected."""
        from django.db.models import Sum
        from django.utils import timezone
        s = TillSession.objects.filter(pk=pk, status=TillSession.OPEN).first()
        if not s:
            return Response({"detail": "open till session not found"}, status=404)
        counted = Decimal(str(request.data.get("counted_cash", 0)))
        ins, outs = s.cash_in_out()
        # Every tender flagged counts_as_cash in the master lands in the
        # physical drawer, so all of them belong in the expected-cash math.
        from apps.masters.models import PaymentMethod
        cash_tenders = list(PaymentMethod.objects
                            .filter(counts_as_cash=True).values_list("name", flat=True)) or ["Cash"]
        cash_taken = (Settlement.objects
                      .filter(tender__in=cash_tenders, created_at__gte=s.opened_at)
                      .aggregate(t=Sum("amount"))["t"] or Decimal("0"))
        s.expected_cash = s.opening_float + ins - outs + cash_taken
        s.counted_cash = counted
        s.variance = counted - s.expected_cash
        s.denominations = request.data.get("denominations", {})
        s.note = request.data.get("note", "")
        s.status = TillSession.CLOSED
        s.closed_at = timezone.now()
        s.closed_by = request.user.username
        s.save()
        log_action(request.user, "till_close", entity="TillSession", entity_id=s.id,
                   after={"expected": str(s.expected_cash), "counted": str(counted),
                          "variance": str(s.variance)})
        return Response(TillSessionSerializer(s).data)


# A booking blocks its table only NEAR the slot: from HOLD_BEFORE ahead of the
# reserved time until NO_SHOW_GRACE past it (then it auto-no-shows and frees).
HOLD_BEFORE_MIN = 15
NO_SHOW_GRACE_MIN = 30


def refresh_reservation_holds():
    """Lazy time-based sweep (no cron): apply/release holds and expire
    no-shows. Called from the floor reads and the order/QR guards."""
    from datetime import timedelta

    from django.utils import timezone
    now = timezone.now()
    hold_from = now + timedelta(minutes=HOLD_BEFORE_MIN)
    grace = now - timedelta(minutes=NO_SHOW_GRACE_MIN)

    # Expire bookings whose slot passed the grace period — party never showed.
    for r in TableReservation.objects.filter(
            kind="reservation", status=TableReservation.BOOKED,
            reserved_for__isnull=False, reserved_for__lt=grace).select_related("table"):
        r.status = TableReservation.NO_SHOW
        r.save(update_fields=["status"])

    active = (TableReservation.objects
              .filter(kind="reservation", status=TableReservation.BOOKED,
                      table__isnull=False, reserved_for__isnull=False,
                      reserved_for__lte=hold_from, reserved_for__gte=grace)
              .select_related("table"))
    active_table_ids = set()
    for r in active:
        active_table_ids.add(r.table_id)
        if r.table.status == Table.FREE:
            r.table.status = Table.RESERVED
            r.table.save(update_fields=["status"])
    # Release held tables whose booking is not in its window anymore.
    for t in Table.objects.filter(status=Table.RESERVED).exclude(id__in=active_table_ids):
        t.status = Table.FREE
        t.save(update_fields=["status"])


class TableReservationViewSet(ModuleViewSetMixin, viewsets.ModelViewSet):
    """Restaurant table bookings + walk-in waitlist (competitor parity)."""

    module = "pos"
    queryset = TableReservation.objects.select_related("table").all()
    serializer_class = TableReservationSerializer

    def get_queryset(self):
        qs = super().get_queryset()
        if self.request.query_params.get("open"):
            qs = qs.filter(status=TableReservation.BOOKED)
        return qs

    def _hold(self, r):
        """A booked reservation holds its table only within the 15-minute
        pre-slot window — a booking for tomorrow doesn't block walk-ins today."""
        from datetime import timedelta

        from django.utils import timezone
        if (r.kind == "reservation" and r.status == TableReservation.BOOKED
                and r.table and r.table.status == Table.FREE
                and r.reserved_for
                and r.reserved_for <= timezone.now() + timedelta(minutes=HOLD_BEFORE_MIN)):
            r.table.status = Table.RESERVED
            r.table.save(update_fields=["status"])

    def _release(self, r):
        if (r.table and r.table.status == Table.RESERVED
                and not r.table.reservations.filter(status=TableReservation.BOOKED)
                                            .exclude(pk=r.pk).exists()):
            r.table.status = Table.FREE
            r.table.save(update_fields=["status"])

    def perform_create(self, serializer):
        # Clash guard: one table, one booking per ±90-minute window.
        data = serializer.validated_data
        tbl, when = data.get("table"), data.get("reserved_for")
        if data.get("kind", "reservation") == "reservation" and tbl and when:
            from datetime import timedelta

            from django.utils import timezone as tz
            window = timedelta(minutes=90)
            clash = (TableReservation.objects
                     .filter(table=tbl, kind="reservation", status=TableReservation.BOOKED,
                             reserved_for__gte=when - window, reserved_for__lte=when + window)
                     .first())
            if clash:
                from rest_framework.exceptions import ValidationError
                at = tz.localtime(clash.reserved_for).strftime("%H:%M")
                raise ValidationError(
                    {"detail": f"{tbl.name} is already booked for {clash.name} at {at} — pick another slot or table"})
        r = serializer.save()
        self._hold(r)
        log_action(self.request.user, "table_reserve", entity="TableReservation",
                   entity_id=r.id, after={"name": r.name, "kind": r.kind})

    @action(detail=True, methods=["post"])
    def seat(self, request, pk=None):
        """Guest arrives: release the hold and hand the table to the POS."""
        r = self.get_object()
        if r.status != TableReservation.BOOKED:
            return Response({"detail": "only a booked entry can be seated"}, status=400)
        table_id = request.data.get("table")  # waitlist picks a table at seat time
        if table_id:
            r.table = Table.objects.filter(pk=table_id).first()
        if not r.table:
            return Response({"detail": "choose a table to seat this party"}, status=400)
        r.status = TableReservation.SEATED
        r.save(update_fields=["status", "table"])
        # Seating always hands the table to the POS — even if a later booking
        # exists, the party sitting NOW must be able to order.
        if r.table.status == Table.RESERVED:
            r.table.status = Table.FREE
            r.table.save(update_fields=["status"])
        log_action(request.user, "reservation_seated", entity="TableReservation", entity_id=r.id)
        return Response(TableReservationSerializer(r).data)

    @action(detail=True, methods=["post"])
    def cancel(self, request, pk=None):
        r = self.get_object()
        r.status = TableReservation.CANCELLED
        r.save(update_fields=["status"])
        self._release(r)
        return Response(TableReservationSerializer(r).data)

    @action(detail=True, methods=["post"])
    def no_show(self, request, pk=None):
        r = self.get_object()
        r.status = TableReservation.NO_SHOW
        r.save(update_fields=["status"])
        self._release(r)
        return Response(TableReservationSerializer(r).data)


class QrOrderView(APIView):
    """Public QR table ordering: a guest scans the table QR and orders to the
    same kitchen queue (BRD FR-ONL-004). The table token is the credential."""

    permission_classes = [AllowAny]
    throttle_scope = "sensitive"

    def get(self, request):
        """Return the table's menu so the guest's QR page can render it."""
        table = Table.objects.filter(qr_token=request.query_params.get("token", "")).first()
        if not table:
            return Response({"detail": "invalid table"}, status=404)
        if not active_entitlements().get("restaurant"):
            return Response({"detail": "ordering unavailable"}, status=403)
        items = MenuItem.objects.filter(available=True).select_related("category")
        return Response({
            "table": table.name,
            "menu": MenuItemSerializer(items, many=True).data,
        })

    def post(self, request):
        table = Table.objects.filter(qr_token=request.data.get("token", "")).first()
        if not table:
            return Response({"detail": "invalid table"}, status=404)
        if not active_entitlements().get("restaurant"):
            return Response({"detail": "ordering unavailable"}, status=403)
        refresh_reservation_holds()
        table.refresh_from_db()
        if table.status == Table.RESERVED:
            return Response(
                {"detail": "this table is reserved — please ask the staff to seat you first"},
                status=403)
        with transaction.atomic():
            import uuid as uuid_lib
            order = Order.objects.create(mode=Order.DINEIN, table=table,
                                         source_platform="qr", online_status="received",
                                         status=Order.KOT_FIRED, kitchen_status="cooking",
                                         kot_no=f"QR-{table.name}",
                                         client_uuid=uuid_lib.uuid4().hex)
            kot = Kot.objects.create(order=order, number=f"QR-{table.name}-{order.id}")
            fired = []
            for ln in request.data.get("items", []):
                item = MenuItem.objects.filter(pk=ln.get("menu_item"), available=True).first()
                if not item:
                    continue
                fired.append(OrderLine.objects.create(
                    order=order, menu_item=item, qty=int(ln.get("qty", 1)),
                    unit_price=item.price, kot_fired=True, kot=kot))
            from apps.recipes.services import deduct_for_newly_fired
            deduct_for_newly_fired(order, fired)
            table.status = Table.RUNNING
            table.save(update_fields=["status"])
        return Response({"order": order.id, "kot": order.kot_no, "table": table.name,
                         "ref": order.client_uuid}, status=201)


class KdsViewSet(ModuleViewSetMixin, viewsets.ViewSet):
    """Kitchen Display System: live fired tickets with bump-to-ready (BRD 5.13)."""

    module = "kds"

    def list(self, request):
        # One ticket per KOT round: a table's second round is its own ticket,
        # so serving round 1 never hides (or re-shows) other rounds (FR-POS-004).
        kots = (Kot.objects.filter(status__in=["cooking", "ready"])
                .select_related("order__table", "order__bar_table")
                .prefetch_related("lines__menu_item").order_by("created_at"))
        out = []
        for k in kots:
            o = k.order
            # A bar tab's side dish still fires here — tag it "Bar: <table>" (or
            # "Bar takeaway" with no table) so the kitchen can tell it apart from
            # the restaurant's own tables/takeaways, not just "Takeaway" either way.
            out.append({
                "id": k.id, "type": "order", "kot_no": k.number, "kitchen_status": k.status,
                # Room-service tickets label the destination room, not "Delivery".
                "table": (o.table.name if o.table
                          else f"Bar: {o.bar_table.name}" if o.bar_table
                          else "Bar takeaway" if o.department == Order.BAR
                          else o.captain if o.source_platform == "roomservice"
                          else o.get_mode_display()),
                "created_at": k.created_at,
                "items": [{"name": l.display_name, "qty": l.qty,
                           "station": l.menu_item.station} for l in k.lines.all()],
            })
        # Banquet Event Order catering prep (FR-BQT-004).
        from apps.banquets.models import Event as BqEvent
        for e in BqEvent.objects.filter(beo_status__in=["pending", "ready"]).select_related("space"):
            out.append({
                "id": e.id, "type": "beo", "kot_no": f"BEO-{e.id}", "kitchen_status": e.beo_status,
                "table": f"{e.space.name} · {e.title}", "created_at": e.event_date,
                "items": [{"name": f"Catering ~{e.food_covers} plates ({e.food_pref or 'mixed'}) "
                                   f"— {e.event_date}", "qty": e.covers, "station": "kitchen"}],
            })
        return Response(out)

    @action(detail=True, methods=["post"])
    def bump(self, request, pk=None):
        """Advance one KOT round: cooking → ready → served.

        Only the kitchen (chef / managers) bumps — the counter watches the
        board but never marks food ready.
        """
        from apps.accounts.constants import KITCHEN_ROLES
        if getattr(request.user, "role", "") not in KITCHEN_ROLES:
            return Response({"detail": "only the kitchen marks food ready"}, status=403)
        kot = Kot.objects.filter(pk=pk).select_related("order").first()
        if not kot:
            return Response({"detail": "not found"}, status=404)
        from django.utils import timezone
        if kot.status == Kot.COOKING:
            kot.status = Kot.READY
            kot.ready_at = timezone.now()
        else:
            kot.status = Kot.SERVED
            kot.served_at = timezone.now()
        kot.save(update_fields=["status", "ready_at", "served_at"])
        # Order-level summary status: worst state across its rounds.
        order = kot.order
        statuses = set(order.kots.values_list("status", flat=True))
        order.kitchen_status = ("cooking" if Kot.COOKING in statuses
                                else "ready" if Kot.READY in statuses else "served")
        # The chef's bump is the ONLY source of "ready" for online orders —
        # it flows to the aggregator board so the counter can dispatch.
        if (order.source_platform and order.kitchen_status in ("ready", "served")
                and order.online_status in ("received", "accepted")):
            order.online_status = "ready"
        order.save(update_fields=["kitchen_status", "online_status"])
        return Response({"id": kot.id, "kitchen_status": kot.status})

    @action(detail=False, methods=["get"])
    def performance(self, request):
        """Kitchen performance: prep times from KOT fire → ready (spec P2.11)."""
        from datetime import timedelta

        from django.utils import timezone
        days = int(request.query_params.get("days", 7))
        kots = (Kot.objects.filter(created_at__gte=timezone.now() - timedelta(days=days),
                                   ready_at__isnull=False)
                .select_related("order__table"))
        if not kots:
            return Response({"days": days, "tickets": 0, "avg_prep_minutes": 0,
                             "by_hour": [], "slowest": []})
        durations = [(k, (k.ready_at - k.created_at).total_seconds() / 60) for k in kots]
        by_hour: dict = {}
        for k, mins in durations:
            h = timezone.localtime(k.created_at).hour
            by_hour.setdefault(h, []).append(mins)
        slowest = sorted(durations, key=lambda x: -x[1])[:5]
        return Response({
            "days": days,
            "tickets": len(durations),
            "avg_prep_minutes": round(sum(m for _, m in durations) / len(durations), 1),
            "by_hour": [{"hour": h, "avg_minutes": round(sum(v) / len(v), 1), "tickets": len(v)}
                        for h, v in sorted(by_hour.items())],
            "slowest": [{"kot_no": k.number,
                         "table": k.order.table.name if k.order.table else k.order.get_mode_display(),
                         "minutes": round(m, 1)} for k, m in slowest],
        })

    @action(detail=True, methods=["post"])
    def beo_bump(self, request, pk=None):
        """Advance a banquet BEO prep ticket from the kitchen display (FR-BQT-004)."""
        from apps.accounts.constants import KITCHEN_ROLES
        if getattr(request.user, "role", "") not in KITCHEN_ROLES:
            return Response({"detail": "only the kitchen marks food ready"}, status=403)
        from apps.accounts.permissions import shared_or_visible
        from apps.banquets.models import Event as BqEvent
        e = shared_or_visible(BqEvent.objects.all(), request, field="space__location").filter(pk=pk).first()
        if not e:
            return Response({"detail": "not found"}, status=404)
        e.beo_status = "ready" if e.beo_status == "pending" else "done"
        e.save(update_fields=["beo_status"])
        return Response({"id": e.id, "kitchen_status": e.beo_status})


class CategoryViewSet(AnyModuleViewSetMixin, viewsets.ModelViewSet):
    # Bar Captain reads/manages categories too (Beverages live in the same
    # catalogue as the restaurant menu — see MenuItem.station).
    modules = ["pos", "barpos"]
    queryset = Category.objects.all()
    serializer_class = CategorySerializer

    def get_queryset(self):
        qs = shared_or_visible(super().get_queryset(), self.request)
        # ?is_bar=1 → just the bar's own categories (Beer, Wine, Cocktails…);
        # ?is_bar=0 → just the restaurant's (Starters, Rice Bowls…). Keeps
        # the two pickers from mixing even though it's one shared table.
        is_bar = self.request.query_params.get("is_bar")
        if is_bar is not None:
            qs = qs.filter(is_bar=is_bar in ("1", "true", "True"))
        return qs


class MenuItemViewSet(AnyModuleViewSetMixin, viewsets.ModelViewSet):
    modules = ["pos", "barpos"]
    queryset = MenuItem.objects.select_related("category").all()
    serializer_class = MenuItemSerializer

    def get_queryset(self):
        qs = shared_or_visible(super().get_queryset(), self.request)
        cat = self.request.query_params.get("category")
        if cat:
            qs = qs.filter(category_id=cat)
        # Bar POS asks for ?bar_menu=1 — the bar's own dedicated menu, never
        # the full restaurant catalogue (a kitchen dish must be explicitly
        # added to the bar menu to show up here).
        if self.request.query_params.get("bar_menu"):
            qs = qs.filter(bar_menu=True)
        return qs

    def perform_destroy(self, instance):
        # OrderLine.menu_item is on_delete=PROTECT — any item with order
        # history (even fully settled, historical orders) can't be deleted
        # outright. Turn that into a clean 400 instead of a raw 500.
        from django.db.models import ProtectedError
        try:
            instance.delete()
        except ProtectedError:
            from rest_framework.exceptions import ValidationError
            raise ValidationError(
                {"detail": f"\"{instance.name}\" has order history and can't be deleted — mark it unavailable (86'd) instead"})


class OrderViewSet(AnyModuleViewSetMixin, viewsets.ModelViewSet):
    # Shared by the restaurant floor ("pos") and the bar ("barpos") — which
    # specific orders a role may see/touch is narrowed by department scoping
    # in get_queryset(), not by this module gate.
    modules = ["pos", "barpos"]
    queryset = (Order.objects.prefetch_related("lines__menu_item")
                .select_related("table", "bar_table").all())
    serializer_class = OrderSerializer
    # Manager-override passcode and coupon/loyalty codes are brute-forceable
    # if unthrottled (security review 2026-07, findings B3/B4) — scoped to
    # just these actions so ordinary order-taking during a busy service
    # never hits a rate limit.
    _sensitive_actions = {"set_qty", "void", "apply_discount", "apply_coupon", "redeem_loyalty"}

    def get_throttles(self):
        if self.action in self._sensitive_actions:
            self.throttle_scope = "sensitive"
        return super().get_throttles()

    def get_queryset(self):
        from apps.accounts.rbac import can_access
        qs = super().get_queryset()
        role = getattr(self.request.user, "role", "")
        can_pos = can_access(role, "pos")
        can_bar = can_access(role, "barpos")
        # Bar Captain only ever sees bar tabs; restaurant-only roles only ever
        # see food orders. Roles with both (managers, Super Admin/MD/GM) see everything.
        if can_bar and not can_pos:
            qs = qs.filter(department=Order.BAR)
        elif can_pos and not can_bar:
            qs = qs.filter(department=Order.FOOD)
        # An F&B Cashier assigned only to one branch never sees another
        # branch's till — same "mine + not-yet-branch-tagged" rule as the
        # menu, so pre-existing orders don't vanish for anyone.
        qs = shared_or_visible(qs, self.request)
        status_ = self.request.query_params.get("status")
        if status_:
            qs = qs.filter(status=status_)
        table = self.request.query_params.get("table")
        if table:
            qs = qs.filter(table_id=table)
        bar_table = self.request.query_params.get("bar_table")
        if bar_table:
            qs = qs.filter(bar_table_id=bar_table)
        # ?open=1 → orders still on the floor (so the POS resumes a running table).
        if self.request.query_params.get("open"):
            qs = qs.filter(status__in=[Order.OPEN, Order.KOT_FIRED, Order.BILLED])
        return qs

    def _reload(self, order):
        """Re-fetch after mutating `order.lines` in this request — the queryset
        that fetched `order` already prefetched (and cached) the pre-mutation
        lines, so serializing `order` directly would show stale data."""
        return (Order.objects.prefetch_related("lines__menu_item")
                .select_related("table", "bar_table").get(pk=order.pk))

    def perform_create(self, serializer):
        # Stamp who took the order — the captain owns delivery for their tables.
        # Every order gets a client_uuid so the public status page can reference it.
        import uuid
        from rest_framework.exceptions import ValidationError
        user = self.request.user
        role = getattr(user, "role", "")

        department = serializer.validated_data.get("department", Order.FOOD)
        bar_tbl = serializer.validated_data.get("bar_table")
        tbl = serializer.validated_data.get("table")
        # Bar Captain / Bar Cashier only ever run the bar — never the restaurant floor.
        if role in (ROLE_BAR_CAPTAIN, ROLE_BAR_CASHIER):
            department = Order.BAR

        if department == Order.BAR:
            mode = serializer.validated_data.get("mode", Order.DINEIN)
            if mode not in (Order.DINEIN, Order.TAKEAWAY):
                raise ValidationError({"detail": "the bar only takes table tabs or takeaway — not room/delivery"})
            # Bar Captain runs tabs on bar tables; walk-up counter orders
            # (no table) stay with the Bar Cashier, same split as the
            # restaurant's Captain vs F&B Cashier.
            if role == ROLE_BAR_CAPTAIN and mode != Order.DINEIN:
                raise ValidationError({"detail": "bar captains take table tabs — takeaway stays with the bar cashier"})
            if tbl:
                raise ValidationError({"detail": "a bar order can't have a restaurant table"})
            if mode == Order.DINEIN:
                if not bar_tbl:
                    raise ValidationError({"detail": "pick a bar table for a bar order"})
            elif bar_tbl:
                raise ValidationError({"detail": "a takeaway order doesn't have a bar table"})
        else:
            if bar_tbl:
                raise ValidationError({"detail": "a food order can't have a bar table"})
            # Captains work the tables — takeaway/delivery/room are counter flows.
            if role == "Captain" and serializer.validated_data.get("mode", Order.DINEIN) != Order.DINEIN:
                raise ValidationError({"detail": "captains take table orders — counter flows stay with the cashier"})
            # Hard-assigned tables: once the F&B Cashier hands a table to a
            # captain, only that captain can open orders on it — a table
            # nobody's been assigned yet (assigned_captain is null) stays open.
            # Exception: if that captain is on approved leave today, the
            # table doesn't stay locked to someone who isn't even in —
            # anyone can pick it up until the cashier reassigns it.
            if (role == "Captain" and tbl and tbl.assigned_captain_id and tbl.assigned_captain_id != user.id
                    and not captain_on_leave_today(tbl.assigned_captain)):
                raise ValidationError({"detail": f"table {tbl.name} is assigned to another captain"})
            # A reserved table belongs to its booking: seat the reservation (which
            # releases the hold) before anyone can order on it.
            if tbl:
                refresh_reservation_holds()
                tbl.refresh_from_db()
            if tbl and tbl.status == Table.RESERVED:
                raise ValidationError(
                    {"detail": f"table {tbl.name} is reserved — seat the booking from the floor, or pick another table"})

        # Which branch rang this up: the till's active branch if one was
        # sent; else whichever the table/bar table already belongs to
        # (covers counter flows with no table at all); else, if this login
        # is only ever assigned to the one branch, that one — same
        # single-assignment auto-scoping the reads already get, so a cashier
        # who only works Bhavani Road never has to pick it explicitly.
        order_location = resolve_active_branch(self.request)
        if order_location is None:
            order_location = getattr(tbl, "location_id", None) or getattr(bar_tbl, "location_id", None)
        if order_location is None:
            visible = visible_branch_ids(self.request)
            if isinstance(visible, set) and len(visible) == 1:
                order_location = next(iter(visible))

        extra = {"department": department,
                 "captain": user.get_full_name() or user.username,
                 "location_id": order_location,
                 "client_uuid": serializer.validated_data.get("client_uuid") or uuid.uuid4().hex}
        if serializer.validated_data.get("mode") == Order.ROOM:
            # Room channel: the guest's room is chosen up front, so the bill can
            # only ever land on that folio (no free folio-picking at the end).
            from rest_framework.exceptions import ValidationError

            from apps.frontoffice.models import Folio
            folio = (Folio.objects.filter(pk=self.request.data.get("folio"),
                                          status=Folio.OPEN, room__isnull=False)
                     .select_related("room").first())
            if not folio:
                raise ValidationError({"detail": "pick an in-house room (open folio) for a room order"})
            extra["folio"] = folio
            extra["captain"] = f"Room {folio.room.number}"   # kitchen sees the destination
            extra["source_platform"] = "roomservice"
        serializer.save(**extra)

    @action(detail=False, methods=["get"])
    def room_folios(self, request):
        """In-house rooms for the POS Room channel — open folios with a room."""
        if not active_entitlements().get("hms"):
            return Response([])
        from apps.frontoffice.models import Folio
        # Only this branch's in-house rooms — a cashier can't post a bill
        # onto another branch's folio.
        qs = shared_or_visible(
            Folio.objects.filter(status=Folio.OPEN, room__isnull=False), request)
        return Response([
            {"folio": f.id, "room": f.room.number, "guest": f.guest_name}
            for f in qs.select_related("room").order_by("room__number")
        ])

    def _assign_token(self, order):
        """Daily pickup token for takeaway/delivery — shown on the token board."""
        from django.utils import timezone
        if order.mode in (Order.DINEIN, Order.ROOM) or order.token_no:
            return
        today = timezone.localdate()
        last = (Order.objects.filter(created_at__date=today, token_no__isnull=False)
                .aggregate(m=models.Max("token_no"))["m"] or 0)
        order.token_no = last + 1

    @staticmethod
    def _ensure_feedback(order):
        """Pending feedback row whose token goes on the bill QR/link."""
        import uuid
        Feedback.objects.get_or_create(order=order, defaults={"token": uuid.uuid4().hex})

    @action(detail=False, methods=["get"])
    def tokens(self, request):
        """Live pickup-token board: today's takeaway/delivery tickets by status."""
        from django.utils import timezone
        qs = (Order.objects.filter(token_no__isnull=False,
                                   created_at__date=timezone.localdate())
              .filter(kitchen_status__in=["cooking", "ready"])
              .order_by("token_no"))
        return Response([{
            "token_no": o.token_no, "mode": o.mode, "kitchen_status": o.kitchen_status,
            "brand": o.brand, "source_platform": o.source_platform,
        } for o in qs])

    @action(detail=False, methods=["get"])
    def ready(self, request):
        """KOT rounds the kitchen marked ready, for the floor's serve board.
        ?mine=1 → only orders this captain took (their tables to run).
        Scoped by department like the order list itself — a bar role only
        sees bar tickets ready to collect, a restaurant role only sees theirs."""
        from apps.accounts.rbac import can_access
        kots = (Kot.objects.filter(status=Kot.READY)
                .select_related("order__table", "order__bar_table").order_by("created_at"))
        role = getattr(request.user, "role", "")
        can_pos = can_access(role, "pos")
        can_bar = can_access(role, "barpos")
        if can_bar and not can_pos:
            kots = kots.filter(order__department=Order.BAR)
        elif can_pos and not can_bar:
            kots = kots.filter(order__department=Order.FOOD)
        if request.query_params.get("mine"):
            me = request.user.get_full_name() or request.user.username
            kots = kots.filter(order__captain=me)
        return Response([{
            "kot": k.id, "kot_no": k.number, "order": k.order_id,
            "table": (k.order.table.name if k.order.table
                     else f"Bar: {k.order.bar_table.name}" if k.order.bar_table
                     else "Bar takeaway" if k.order.department == Order.BAR
                     else k.order.get_mode_display()),
            "captain": k.order.captain,
            # Online orders dispatch from the POS once the kitchen marks ready.
            "online": bool(k.order.source_platform),
            "platform": k.order.source_platform,
            "token_no": k.order.token_no,
        } for k in kots])

    @action(detail=False, methods=["post"])
    def serve(self, request):
        """Whoever's picking it up (captain, or bar staff collecting a side
        dish for a bar tab) confirms the ready round actually reached them —
        the kitchen→floor (or kitchen→bar) handoff loop."""
        from apps.accounts.rbac import can_access
        kot = Kot.objects.filter(pk=request.data.get("kot"), status=Kot.READY)\
                         .select_related("order").first()
        if not kot:
            return Response({"detail": "ready KOT not found"}, status=404)
        role = getattr(request.user, "role", "")
        needed = "barpos" if kot.order.department == Order.BAR else "pos"
        if not can_access(role, needed):
            return Response({"detail": "not your ticket to collect"}, status=403)
        from django.utils import timezone
        kot.status = Kot.SERVED
        kot.served_at = timezone.now()
        kot.save(update_fields=["status", "served_at"])
        order = kot.order
        statuses = set(order.kots.values_list("status", flat=True))
        order.kitchen_status = ("cooking" if Kot.COOKING in statuses
                                else "ready" if Kot.READY in statuses else "served")
        order.save(update_fields=["kitchen_status"])
        log_action(request.user, "kot_served", entity="Kot", entity_id=kot.id,
                   after={"kot": kot.number})
        return Response({"kot": kot.id, "status": kot.status})

    @action(detail=True, methods=["post"])
    def add_item(self, request, pk=None):
        order = self.get_object()
        err = _closed_error(order) or _billed_error(order)
        if err:
            return err
        item = MenuItem.objects.filter(pk=request.data.get("menu_item"), available=True).first()
        if not item:
            return Response({"detail": "menu_item not found"}, status=404)
        qty = int(request.data.get("qty", 1))

        # Channel pricing (FR-MNU-003): the item's price for this order's mode.
        variant = None
        base_price = item.price
        chan_price = item.channel_prices.filter(channel=order.mode).first()
        if chan_price:
            base_price = chan_price.price
        # Happy-hour / scheduled pricing (FR-MNU-011) overrides if active now.
        for sched in item.schedules.all():
            if sched.active_now():
                base_price = sched.price
                break
        # Variant pricing (FR-MNU-004) overrides the base for that size.
        variant_id = request.data.get("variant")
        if variant_id:
            variant = Variant.objects.filter(pk=variant_id, menu_item=item).first()
            if not variant:
                return Response({"detail": "invalid variant"}, status=400)
            base_price = variant.price

        # Add-ons + min/max validation (FR-MNU-005)
        addon_ids = request.data.get("addons", [])
        addons, addon_total = [], Decimal("0")
        if addon_ids:
            chosen = AddOn.objects.filter(id__in=addon_ids, group__menu_item=item).select_related("group")
            for a in chosen:
                addons.append({"name": a.name, "price": str(a.price)})
                addon_total += a.price
        for grp in item.addon_groups.all():
            picked = sum(1 for a in AddOn.objects.filter(id__in=addon_ids, group=grp))
            if picked < grp.min_select:
                return Response({"detail": f"'{grp.name}' requires at least {grp.min_select} choice(s)"}, status=400)
            if grp.max_select and picked > grp.max_select:
                return Response({"detail": f"'{grp.name}' allows at most {grp.max_select}"}, status=400)

        unit_price = base_price + addon_total
        # Only merge identical simple lines (no variant/addons) to keep modifiers distinct.
        if not variant and not addons:
            existing = order.lines.filter(menu_item=item, variant__isnull=True,
                                          kot_fired=False, note="").first()
            if existing and not existing.addons:
                existing.qty += qty
                existing.save(update_fields=["qty"])
                return Response(OrderSerializer(self._reload(order)).data)
        OrderLine.objects.create(
            order=order, menu_item=item, variant=variant, addons=addons,
            qty=qty, unit_price=unit_price, note=request.data.get("note", ""),
        )
        return Response(OrderSerializer(self._reload(order)).data)

    @action(detail=True, methods=["post"])
    def set_qty(self, request, pk=None):
        order = self.get_object()
        err = _closed_error(order) or _billed_error(order)
        if err:
            return err
        line = order.lines.filter(pk=request.data.get("line")).first()
        if not line:
            return Response({"detail": "line not found"}, status=404)
        qty = int(request.data.get("qty", 0))
        # Reducing/removing an item already fired to the kitchen is an item void —
        # the food was made, so it needs a manager override (FR-USR-006).
        if line.kot_fired and qty < line.qty:
            mgr = _valid_override(request.data.get("override"))
            if not mgr:
                return Response(
                    {"detail": "Item already sent to kitchen — manager override required to reduce it",
                     "override_required": True},
                    status=403,
                )
            log_action(request.user, "item_void", entity="OrderLine", entity_id=line.id,
                       after={"item": line.menu_item.name, "from_qty": line.qty, "to_qty": qty,
                              "override": mgr.username})
        if qty <= 0:
            line.delete()
        else:
            line.qty = qty
            line.save(update_fields=["qty"])
        return Response(OrderSerializer(self._reload(order)).data)

    def _free_table_if_idle(self, table):
        """Free the table (restaurant Table or BarTable — both use "free" as
        their idle status value) only when nothing on it is still unpaid
        (incl. printed bills)."""
        if table and not table.orders.filter(
                status__in=[Order.OPEN, Order.KOT_FIRED, Order.BILLED]).exists():
            table.status = "free"
            table.save(update_fields=["status"])

    @action(detail=False, methods=["post"])
    def aggregator(self, request):
        """Ingest a delivery-aggregator order (Zomato/Swiggy) into the POS (FR-ONL-001).

        Idempotent by (platform, external_id). Prepaid orders are marked paid and
        excluded from counter collection (FR-PAY-006). A KOT fires automatically.
        In production the webhook signature is verified (SR-030); here it's trusted.
        """
        platform = request.data.get("platform", "zomato")
        ext = str(request.data.get("external_id", "")).strip()
        if not ext:
            return Response({"detail": "external_id required"}, status=400)
        existing = Order.objects.filter(source_platform=platform, external_ref=ext).first()
        if existing:
            return Response(OrderSerializer(existing).data)  # idempotent
        from apps.crm.models import Customer
        cust = None
        mobile = (request.data.get("customer") or {}).get("mobile", "")
        if mobile:
            cust, _ = Customer.objects.get_or_create(
                mobile=mobile, defaults={"name": (request.data.get("customer") or {}).get("name", "Online")})
        with transaction.atomic():
            order = Order.objects.create(
                mode=Order.DELIVERY, customer=cust, source_platform=platform,
                external_ref=ext, online_status="received",
                prepaid=bool(request.data.get("prepaid", True)),
                brand=request.data.get("brand", ""),
                kot_no=f"AGG-{ext[:6].upper()}", status=Order.KOT_FIRED, kitchen_status="cooking",
            )
            self._assign_token(order)
            order.save(update_fields=["token_no"])
            kot = Kot.objects.create(order=order, number=f"AGG-{ext[:6].upper()}")
            fired = []
            for ln in request.data.get("items", []):
                item = MenuItem.objects.filter(pk=ln.get("menu_item")).first()
                if not item:
                    continue
                line = OrderLine.objects.create(order=order, menu_item=item,
                                                qty=int(ln.get("qty", 1)),
                                                unit_price=item.price, kot_fired=True, kot=kot)
                fired.append(line)
            from apps.recipes.services import deduct_for_newly_fired
            deduct_for_newly_fired(order, fired)
            if order.prepaid:
                t = order.totals()
                Settlement.objects.create(tender=f"{platform} (prepaid)", amount=t["total"],
                                          reference=f"{platform}:{ext}")
        log_action(request.user, "aggregator_order", entity="Order", entity_id=order.id,
                   after={"platform": platform, "ext": ext})
        return Response(OrderSerializer(order).data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=["post"])
    def online_status(self, request, pk=None):
        """Update the aggregator lifecycle from the counter (FR-ONL-003).

        Segregation on the line: the POS only ACCEPTS (pushes to kitchen) and
        DISPATCHES. "Ready" belongs to the kitchen — the chef bumps the ticket
        on the KDS, which flips the online status here automatically.
        """
        order = self.get_object()
        target = request.data.get("status")
        if target == "ready":
            return Response(
                {"detail": "only the kitchen marks food ready — bump the ticket on the KDS"},
                status=403)
        if target not in ("accepted", "dispatched"):
            return Response({"detail": "invalid status"}, status=400)
        if target == "dispatched" and order.kitchen_status not in ("ready", "served"):
            return Response(
                {"detail": "the kitchen hasn't marked this order ready yet"},
                status=400)
        order.online_status = target
        if target == "dispatched":
            from django.utils import timezone
            order.status = Order.SETTLED if order.prepaid else order.status
            if order.status == Order.SETTLED:
                order.assign_bill_no()
            # The food left the building — close the kitchen ticket too so the
            # ready strip and KDS drop it.
            order.kots.filter(status=Kot.READY).update(status=Kot.SERVED,
                                                       served_at=timezone.now())
            order.kitchen_status = "served"
        order.save(update_fields=["online_status", "status", "kitchen_status", "bill_no"])
        return Response(OrderSerializer(order).data)

    @action(detail=False, methods=["get"])
    def online(self, request):
        """Live online/aggregator order board (FR-ONL panels)."""
        qs = (Order.objects.exclude(source_platform="")
              .exclude(online_status="dispatched")
              .prefetch_related("lines__menu_item").order_by("created_at"))
        return Response(OrderSerializer(qs, many=True).data)

    @action(detail=False, methods=["post"])
    def sync(self, request):
        """Idempotently ingest bills raised offline (FR-POS-010 / NFR-002).

        Body: {orders: [{client_uuid, mode, table, lines:[{menu_item, qty, unit_price}],
                          tender, settled}]}. Replays are safe — dedupe is by client_uuid,
        so re-sending the same batch never creates duplicates.
        """
        results = []
        for payload in request.data.get("orders", []):
            uuid = payload.get("client_uuid")
            if not uuid:
                results.append({"client_uuid": None, "error": "missing client_uuid"})
                continue
            existing = Order.objects.filter(client_uuid=uuid).first()
            if existing:
                results.append({"client_uuid": uuid, "id": existing.id, "created": False})
                continue
            with transaction.atomic():
                order = Order.objects.create(
                    mode=payload.get("mode", Order.DINEIN),
                    table_id=payload.get("table"),
                    client_uuid=uuid, offline_origin=True,
                    kot_no=f"KOT-{uuid[:6].upper()}",
                )
                kot = Kot.objects.create(order=order, number=f"KOT-{uuid[:6].upper()}",
                                         status=Kot.SERVED)  # offline bill: already made
                for ln in payload.get("lines", []):
                    item = MenuItem.objects.filter(pk=ln.get("menu_item")).first()
                    if not item:
                        continue
                    OrderLine.objects.create(
                        order=order, menu_item=item, qty=int(ln.get("qty", 1)),
                        unit_price=Decimal(str(ln.get("unit_price", item.price))),
                        kot_fired=True, kot=kot,
                    )
                if payload.get("settled"):
                    t = order.totals()
                    Settlement.objects.create(
                        tender=payload.get("tender", "Cash"), amount=t["total"],
                        reference=f"offline {uuid[:8]}",
                    )
                    order.status = Order.SETTLED
                    order.assign_bill_no()
                    order.save(update_fields=["status", "bill_no"])
            log_action(request.user, "offline_sync", entity="Order", entity_id=order.id,
                       after={"client_uuid": uuid})
            results.append({"client_uuid": uuid, "id": order.id, "created": True})
        return Response({"results": results})

    @action(detail=True, methods=["post"])
    def move(self, request, pk=None):
        """Move an order to another table, preserving items + KOT history (FR-TBL-004)."""
        order = self.get_object()
        err = _closed_error(order)
        if err:
            return err
        dest = Table.objects.filter(pk=request.data.get("table")).first()
        if not dest:
            return Response({"detail": "destination table not found"}, status=400)
        old = order.table
        order.table = dest
        order.save(update_fields=["table"])
        if order.status in (Order.OPEN, Order.KOT_FIRED):
            dest.status = Table.RUNNING
            dest.save(update_fields=["status"])
        self._free_table_if_idle(old)
        log_action(request.user, "table_move", entity="Order", entity_id=order.id,
                   after={"from": old.name if old else None, "to": dest.name})
        return Response(OrderSerializer(order).data)

    @action(detail=True, methods=["post"])
    def merge(self, request, pk=None):
        """Merge another open order's lines into this one (FR-TBL-004)."""
        order = self.get_object()
        err = _closed_error(order) or _billed_error(order)
        if err:
            return err
        src = Order.objects.filter(pk=request.data.get("source")).first()
        if not src or src.id == order.id:
            return Response({"detail": "invalid source order"}, status=400)
        if src.status in CLOSED_STATUSES:
            return Response({"detail": "source order is already closed"}, status=400)
        with transaction.atomic():
            src.lines.update(order=order)
            src_table = src.table
            src.status = Order.SETTLED  # consumed by the merge
            src.table = None
            src.save(update_fields=["status", "table"])
            self._free_table_if_idle(src_table)
        log_action(request.user, "order_merge", entity="Order", entity_id=order.id,
                   after={"merged_from": src.id})
        return Response(OrderSerializer(order).data)

    @action(detail=True, methods=["post"])
    def split(self, request, pk=None):
        """Split selected lines into a new order/bill (FR-TBL-004)."""
        order = self.get_object()
        err = _closed_error(order)
        if err:
            return err
        line_ids = request.data.get("lines", [])
        lines = order.lines.filter(id__in=line_ids)
        if not lines.exists() or lines.count() == order.lines.count():
            return Response({"detail": "select a subset of lines to split"}, status=400)
        with transaction.atomic():
            new = Order.objects.create(mode=order.mode, table=order.table, customer=order.customer,
                                       status=order.status, kot_no=order.kot_no)
            lines.update(order=new)
        log_action(request.user, "order_split", entity="Order", entity_id=order.id,
                   after={"new_order": new.id, "lines": list(line_ids)})
        return Response({"original": OrderSerializer(order).data, "new": OrderSerializer(new).data})

    @action(detail=True, methods=["post"])
    def void(self, request, pk=None):
        """Void an order — always requires a manager override passcode (FR-USR-006)."""
        order = self.get_object()
        if order.status in CLOSED_STATUSES:
            return Response({"detail": "Order is already settled — process a refund instead of a void"},
                            status=409)
        mgr = _valid_override(request.data.get("override"))
        if not mgr:
            return Response({"detail": "Manager override required to void", "override_required": True},
                            status=403)
        order.status = Order.SETTLED  # closed as void
        order.discount_reason = f"VOID by {request.user.username} (override {mgr.username})"
        order.save(update_fields=["status", "discount_reason"])
        self._free_table_if_idle(order.table)
        self._free_table_if_idle(order.bar_table)
        log_action(request.user, "order_void", entity="Order", entity_id=order.id,
                   after={"override": mgr.username, "reason": request.data.get("reason", "")})
        return Response(OrderSerializer(order).data)

    @action(detail=True, methods=["get"])
    def bill_pdf(self, request, pk=None):
        """Download the POS bill/receipt as a PDF (FR-POS-007)."""
        from django.http import HttpResponse

        from apps.accounts.views import get_property
        from .bill_pdf import build_bill_pdf
        order = self.get_object()
        prop = get_property()
        pdf = build_bill_pdf(order, prop.name, doc_footer=prop.doc_footer)
        resp = HttpResponse(pdf.read(), content_type="application/pdf")
        resp["Content-Disposition"] = f'attachment; filename="bill-{order.id}.pdf"'
        return resp

    @action(detail=True, methods=["post"])
    def apply_discount(self, request, pk=None):
        """Order-level discount within the user's cap; over-cap needs manager override
        (BRD FR-POS-012, FR-USR-004/006)."""
        order = self.get_object()
        err = _closed_error(order) or _billed_error(order)
        if err:
            return err
        kind = request.data.get("kind", "percent")
        value = Decimal(str(request.data.get("value", 0)))
        reason = request.data.get("reason", "")
        if kind not in (Order.DISC_PERCENT, Order.DISC_FIXED) or value <= 0:
            return Response({"detail": "invalid discount"}, status=400)
        if not reason:
            return Response({"detail": "a reason is required for discounts"}, status=400)
        subtotal = order._subtotal()
        amount = (subtotal * value / Decimal("100")) if kind == Order.DISC_PERCENT else min(value, subtotal)
        ok, msg = _within_cap(request.user, subtotal, amount)
        override_user = None
        if not ok:
            override_user = _valid_override(request.data.get("override"))
            if not override_user:
                return Response({"detail": msg + " — manager override required", "cap_exceeded": True},
                                status=403)
        order.discount_kind = kind
        order.discount_value = value
        order.discount_reason = reason + (f" (override: {override_user.username})" if override_user else "")
        order.save(update_fields=["discount_kind", "discount_value", "discount_reason"])
        log_action(request.user, "discount_apply", entity="Order", entity_id=order.id,
                   after={"kind": kind, "value": str(value), "reason": reason,
                          "override": override_user.username if override_user else None})
        return Response(OrderSerializer(order).data)

    @action(detail=True, methods=["post"])
    def apply_coupon(self, request, pk=None):
        order = self.get_object()
        err = _closed_error(order) or _billed_error(order)
        if err:
            return err
        coupon = Coupon.objects.filter(code__iexact=request.data.get("code", "")).first()
        if not coupon:
            return Response({"detail": "Unknown coupon code"}, status=400)
        ok, msg = coupon.is_valid(order._subtotal())
        if not ok:
            return Response({"detail": msg}, status=400)
        order.coupon = coupon
        order.save(update_fields=["coupon"])
        return Response(OrderSerializer(order).data)

    @action(detail=True, methods=["post"])
    def redeem_loyalty(self, request, pk=None):
        """Redeem a customer's loyalty points against the bill (1 pt = ₹1, FR-PRO-004)."""
        order = self.get_object()
        err = _closed_error(order) or _billed_error(order)
        if err:
            return err
        if not order.customer:
            return Response({"detail": "attach a customer first"}, status=400)
        points = int(request.data.get("points", 0))
        cap = min(points, order.customer.loyalty_points, int(order._subtotal()))
        if cap <= 0:
            return Response({"detail": "no redeemable points"}, status=400)
        order.loyalty_redeemed = cap
        order.save(update_fields=["loyalty_redeemed"])
        return Response(OrderSerializer(order).data)

    @action(detail=True, methods=["post"])
    def fire_kot(self, request, pk=None):
        """Fire a KOT for un-fired lines only (incremental KOT, FR-POS-004)."""
        order = self.get_object()
        err = _closed_error(order) or _billed_error(order)
        if err:
            return err
        pending = order.lines.filter(kot_fired=False)
        if not pending.exists():
            return Response({"detail": "nothing new to fire"}, status=400)
        # Cross-module seam: deduct recipe ingredients for the newly fired lines
        # before flipping kot_fired (so the deduction sees only this round).
        from apps.recipes.services import deduct_for_newly_fired
        deduct_for_newly_fired(order, list(pending))
        # Each fire is its own KOT round: the kitchen gets a fresh ticket with
        # only the new items — earlier (possibly served) rounds are untouched.
        seq = order.kots.count() + 1
        kot = Kot.objects.create(order=order, number=f"KOT-{order.id:05d}/{seq}")
        pending.update(kot_fired=True, kot=kot)
        order.kot_no = kot.number  # latest round shown on the order
        order.status = Order.KOT_FIRED
        order.kitchen_status = "cooking"  # appears on the KDS
        self._assign_token(order)  # pickup token for takeaway/delivery
        order.save(update_fields=["kot_no", "status", "kitchen_status", "token_no"])
        if order.table:
            order.table.status = Table.RUNNING
            order.table.save(update_fields=["status"])
        elif order.bar_table:
            order.bar_table.status = BarTable.RUNNING
            order.bar_table.save(update_fields=["status"])
        log_action(request.user, "kot_fire", entity="Order", entity_id=order.id,
                   after={"kot": kot.number, "round": seq})
        return Response(OrderSerializer(order).data)

    @action(detail=True, methods=["post"])
    def bill(self, request, pk=None):
        """Print the final bill: locks the order against further edits (FR-POS-006).

        Every line must be KOT-fired first — nothing reaches the final bill that
        was never sent to the kitchen (and never deducted from stock).
        """
        order = self.get_object()
        err = _closed_error(order)
        if err:
            return err
        if order.status == Order.BILLED:
            return Response(OrderSerializer(order).data)  # reprint is fine
        if not order.lines.exists():
            return Response({"detail": "nothing to bill"}, status=400)
        if order.lines.filter(kot_fired=False).exists():
            return Response({"detail": "Un-fired items on the order — fire the KOT before billing"},
                            status=400)
        order.status = Order.BILLED
        order.save(update_fields=["status"])
        if order.table:
            order.table.status = Table.PRINTED
            order.table.save(update_fields=["status"])
        self._ensure_feedback(order)  # QR/link on the printed bill
        log_action(request.user, "bill_print", entity="Order", entity_id=order.id,
                   after={"total": str(order.totals()["total"])})
        return Response(OrderSerializer(order).data)

    @action(detail=True, methods=["post"])
    def reopen(self, request, pk=None):
        """Unlock a printed bill to make changes; the bill must be reprinted after."""
        order = self.get_object()
        if order.status != Order.BILLED:
            return Response({"detail": "only a billed order can be reopened"}, status=400)
        order.status = Order.KOT_FIRED if order.lines.filter(kot_fired=True).exists() else Order.OPEN
        order.save(update_fields=["status"])
        if order.table and order.table.status == Table.PRINTED:
            order.table.status = Table.RUNNING
            order.table.save(update_fields=["status"])
        log_action(request.user, "bill_reopen", entity="Order", entity_id=order.id)
        return Response(OrderSerializer(order).data)

    def _finalize_promotions(self, order, total):
        """Commit coupon usage + loyalty redemption/accrual on close (FR-PRO-002/004)."""
        if order.coupon:
            Coupon.objects.filter(pk=order.coupon_id).update(used_count=models.F("used_count") + 1)
        cust = order.customer
        if cust:
            if order.loyalty_redeemed:
                cust.loyalty_points = max(0, cust.loyalty_points - order.loyalty_redeemed)
            cust.loyalty_points += int(total // Decimal("100"))  # 1 pt per ₹100 spent
            cust.save(update_fields=["loyalty_points"])

    @action(detail=True, methods=["post"])
    def settle(self, request, pk=None):
        """Settle by tender at the outlet (FR-PAY-001).

        tender 'Gateway' routes through the payment provider with a token
        (PCI-safe — no card data touches us; FR-PAY-008 / SR-060).
        """
        order = self.get_object()
        err = _closed_error(order)
        if err:
            return err
        if not order.lines.exists():
            return Response({"detail": "nothing to settle"}, status=400)
        if order.lines.filter(kot_fired=False).exists():
            return Response({"detail": "Un-fired items on the order — fire the KOT before settling"},
                            status=400)
        t = order.totals()
        tender = request.data.get("tender", "Cash")
        # Tender must be an active row in the payment-methods master
        # (Settings > Masters) — a disabled or unknown tender can't take money.
        from apps.masters.models import PaymentMethod
        if not PaymentMethod.objects.filter(name=tender, active=True).exists():
            return Response({"detail": f"'{tender}' is not an active payment method"}, status=400)
        # Role↔tender mapping: e.g. captains settle digital tenders tableside,
        # but drawer-cash tenders belong at the cashier counter (BRD 5.10) —
        # per-tender behavior comes from the master's captain_allowed flag.
        from apps.accounts.constants import role_can_tender
        if not role_can_tender(getattr(request.user, "role", ""), tender):
            return Response(
                {"detail": f"Your role can't accept {tender} — collect it at the cashier counter"},
                status=403,
            )
        reference = request.data.get("reference", f"POS order {order.id}")
        if tender == "Gateway":
            from apps.integrations import services as integ
            result = integ.charge_card(t["total"], request.data.get("token", ""), reference)
            if result.get("status") != "approved":
                return Response({"detail": result.get("reason", "payment declined")}, status=402)
            reference = result["ref"]
        Settlement.objects.create(tender=tender, amount=t["total"], reference=reference)
        self._finalize_promotions(order, t["total"])
        self._ensure_feedback(order)
        order.status = Order.SETTLED
        order.assign_bill_no()
        order.save(update_fields=["status", "bill_no"])
        # Free the table only if no other unpaid order (e.g. a split bill) sits on it.
        self._free_table_if_idle(order.table)
        self._free_table_if_idle(order.bar_table)
        # Receipt notification to the customer (FR-NOT-001).
        if order.customer:
            from apps.accounts.constants import currency_symbol
            from apps.integrations import services as integ
            integ.notify("sms", order.customer.mobile,
                         f"Thanks for dining with Hearth! Bill {reference}: {currency_symbol()}{t['total']}.")
        log_action(request.user, "pos_settle", entity="Order", entity_id=order.id,
                   after={"total": str(t["total"]), "discount": str(t["discount"]), "tender": tender})
        return Response(OrderSerializer(order).data)

    @action(detail=True, methods=["post"])
    def post_to_room(self, request, pk=None):
        """Cross-module seam: post an in-house guest's F&B bill to their folio (FR-PAY-009).

        Only Room-channel orders can post, and only to the folio chosen when
        the order was opened — a table bill can never land on a guest's room.
        """
        if not active_entitlements().get("hms"):
            return Response(
                {"detail": "post-to-room is unavailable in the Restaurant edition"},
                status=403,
            )
        order = self.get_object()
        err = _closed_error(order)
        if err:
            return err
        if order.mode != Order.ROOM or not order.folio_id:
            return Response(
                {"detail": "table orders settle at the table — take in-house guest orders via the Room channel"},
                status=400)
        if not order.lines.exists():
            return Response({"detail": "nothing to post"}, status=400)
        if order.lines.filter(kot_fired=False).exists():
            return Response({"detail": "Un-fired items on the order — fire the KOT before posting"},
                            status=400)
        folio = Folio.objects.filter(pk=order.folio_id, status=Folio.OPEN).first()
        if not folio:
            return Response({"detail": "the guest's folio is no longer open — collect payment at the counter"},
                            status=400)
        sub = order._subtotal()
        disc = order.discount_amount(sub)
        factor = (sub - disc) / sub if sub else Decimal("1")
        with transaction.atomic():
            for line in order.lines.select_related("menu_item"):
                fo_services.post_charge(
                    folio, kind=FolioLine.KIND_FNB,
                    description=f"{line.qty}× {line.menu_item.name}",
                    amount=line.unit_price * line.qty * factor,
                    gst_rate=line.menu_item.gst_rate,
                    source=f"POS order {order.id}", user=request.user,
                )
            self._finalize_promotions(order, order.totals()["total"])
            order.status = Order.POSTED_TO_ROOM
            order.folio = folio
            order.save(update_fields=["status", "folio"])
            self._free_table_if_idle(order.table)
        log_action(request.user, "post_to_room", entity="Order", entity_id=order.id,
                   after={"folio": folio.id})
        return Response(OrderSerializer(order).data)


class FeedbackPublicView(APIView):
    """Guest-facing feedback form endpoint — the bill's QR/link lands here.

    Token-credentialed like QR ordering; no auth, throttled.
    """

    permission_classes = [AllowAny]
    throttle_scope = "sensitive"

    def get(self, request):
        fb = (Feedback.objects.select_related("order__table")
              .filter(token=request.query_params.get("t", "")).first())
        if not fb:
            return Response({"detail": "invalid feedback link"}, status=404)
        from apps.accounts.views import get_property
        o = fb.order
        return Response({
            "property": get_property().name,
            "submitted": fb.submitted_at is not None,
            "order": o.id if o else None,
            "where": (f"Table {o.table.name}" if o and o.table
                      else o.get_mode_display() if o else ""),
            "total": str(o.totals()["total"]) if o else None,
        })

    def post(self, request):
        from django.utils import timezone
        fb = Feedback.objects.filter(token=request.data.get("t", "")).first()
        if not fb:
            return Response({"detail": "invalid feedback link"}, status=404)
        if fb.submitted_at:
            return Response({"detail": "feedback already submitted — thank you!"}, status=400)
        try:
            rating = int(request.data.get("rating", 0))
            nps = request.data.get("nps")
            nps = int(nps) if nps is not None and str(nps) != "" else None
        except (TypeError, ValueError):
            return Response({"detail": "invalid rating"}, status=400)
        if not 1 <= rating <= 5 or (nps is not None and not 0 <= nps <= 10):
            return Response({"detail": "rating must be 1–5 (NPS 0–10)"}, status=400)
        fb.rating = rating
        fb.nps = nps
        fb.comment = str(request.data.get("comment", ""))[:400]
        fb.submitted_at = timezone.now()
        fb.save(update_fields=["rating", "nps", "comment", "submitted_at"])
        return Response({"detail": "thanks"}, status=201)


class OrderStatusPublicView(APIView):
    """Public 'where's my order' page for takeaway/delivery (token board companion)."""

    permission_classes = [AllowAny]
    throttle_scope = "sensitive"

    def get(self, request):
        ref = request.query_params.get("ref", "")
        if not ref:
            return Response({"detail": "ref required"}, status=400)
        o = Order.objects.filter(client_uuid=ref).select_related("table").first()
        if not o:
            return Response({"detail": "order not found"}, status=404)
        return Response({
            "token_no": o.token_no, "mode": o.mode,
            "kitchen_status": o.kitchen_status or "received",
            "online_status": o.online_status,
            "status": o.status,
            "table": o.table.name if o.table else None,
        })


class FeedbackViewSet(ModuleViewSetMixin, viewsets.ViewSet):
    """Feedback dashboard for the CRM screen: average rating, NPS, recent comments."""

    module = "crm"

    def list(self, request):
        subs = Feedback.objects.filter(submitted_at__isnull=False)
        n = subs.count()
        avg = (sum(f.rating for f in subs if f.rating) / n) if n else 0
        nps_answers = [f.nps for f in subs if f.nps is not None]
        nps = 0
        if nps_answers:
            promoters = sum(1 for x in nps_answers if x >= 9)
            detractors = sum(1 for x in nps_answers if x <= 6)
            nps = round((promoters - detractors) / len(nps_answers) * 100)
        recent = subs.select_related("order__table").order_by("-submitted_at")[:20]
        return Response({
            "count": n, "avg_rating": round(avg, 2), "nps": nps,
            "pending": Feedback.objects.filter(submitted_at__isnull=True).count(),
            "recent": [{
                "id": f.id, "rating": f.rating, "nps": f.nps, "comment": f.comment,
                "order": f.order_id,
                "where": (f"Table {f.order.table.name}" if f.order and f.order.table
                          else f.order.get_mode_display() if f.order else ""),
                "submitted_at": f.submitted_at,
            } for f in recent],
        })


class ReconViewSet(CounterOnlyMixin, ModuleViewSetMixin, viewsets.ViewSet):
    """Payment reconciliation: POS-recorded settlements vs external payouts.

    Flags variance per day×tender and per aggregator platform so pilferage or
    missed orders surface at day-end instead of month-end (Restroworks parity).
    """

    module = "pos"

    def list(self, request):
        from datetime import timedelta

        from django.db.models import Count, Sum
        from django.db.models.functions import TruncDate
        from django.utils import timezone

        from .models import AggregatorPayout
        days = int(request.query_params.get("days", 7))
        since = timezone.now() - timedelta(days=days)

        def money(v):
            return str(Decimal(v).quantize(Decimal("0.01")))

        # POS/PMS settlements grouped by day × tender.
        rows = (Settlement.objects.filter(created_at__gte=since)
                .annotate(day=TruncDate("created_at"))
                .values("day", "tender").annotate(amount=Sum("amount"), count=Count("id"))
                .order_by("-day", "tender"))
        by_day: dict = {}
        for r in rows:
            d = str(r["day"])
            by_day.setdefault(d, {"date": d, "tenders": [], "aggregators": []})
            by_day[d]["tenders"].append({"tender": r["tender"],
                                         "amount": money(r["amount"]), "count": r["count"]})

        # Aggregator prepaid settlements ("zomato (prepaid)") vs imported payouts.
        payouts = AggregatorPayout.objects.filter(date__gte=since.date())
        payout_map: dict = {}
        for p in payouts:
            payout_map.setdefault((str(p.date), p.platform), Decimal("0"))
            payout_map[(str(p.date), p.platform)] += p.amount
        for d, data in by_day.items():
            for t in data["tenders"]:
                if "(prepaid)" in t["tender"]:
                    platform = t["tender"].split(" ")[0]
                    paid_out = payout_map.pop((d, platform), None)
                    pos_amt = Decimal(t["amount"])
                    data["aggregators"].append({
                        "platform": platform, "pos_amount": money(pos_amt),
                        "payout_amount": money(paid_out) if paid_out is not None else None,
                        "variance": money(paid_out - pos_amt) if paid_out is not None else None,
                    })
        # Payouts with no matching POS settlement (orders missing in POS!).
        orphans = [{"date": d, "platform": pf, "payout_amount": money(amt),
                    "pos_amount": None, "variance": None}
                   for (d, pf), amt in payout_map.items()]
        return Response({"days": days, "rows": sorted(by_day.values(),
                                                      key=lambda x: x["date"], reverse=True),
                         "unmatched_payouts": orphans})

    @action(detail=False, methods=["post"])
    def import_payouts(self, request):
        """Import aggregator payout lines: {rows: [{platform, date, amount, reference}]}.
        Idempotent on (platform, date, reference)."""
        from .models import AggregatorPayout
        created = skipped = 0
        for row in request.data.get("rows", []):
            try:
                _, was_created = AggregatorPayout.objects.get_or_create(
                    platform=str(row.get("platform", "")).lower().strip(),
                    date=row.get("date"),
                    reference=str(row.get("reference", "")),
                    defaults={"amount": Decimal(str(row.get("amount", 0)))},
                )
            except Exception:
                skipped += 1
                continue
            created += was_created
            skipped += not was_created
        log_action(request.user, "payout_import", entity="AggregatorPayout",
                   after={"created": created, "skipped": skipped})
        return Response({"created": created, "skipped": skipped})
