from decimal import Decimal

from django.db import models, transaction
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from apps.accounts.models import log_action
from apps.accounts.permissions import ModuleViewSetMixin, active_entitlements
from apps.frontoffice import services as fo_services
from apps.frontoffice.models import Folio, FolioLine, Settlement

from .models import AddOn, Category, Coupon, MenuItem, Order, OrderLine, Table, Variant
from .serializers import (
    CategorySerializer,
    MenuItemSerializer,
    OrderSerializer,
    TableSerializer,
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


class TableViewSet(ModuleViewSetMixin, viewsets.ModelViewSet):
    module = "pos"
    queryset = Table.objects.all()
    serializer_class = TableSerializer


class KdsViewSet(ModuleViewSetMixin, viewsets.ViewSet):
    """Kitchen Display System: live fired tickets with bump-to-ready (BRD 5.13)."""

    module = "kds"

    def list(self, request):
        orders = (Order.objects.filter(kitchen_status__in=["cooking", "ready"])
                  .prefetch_related("lines__menu_item").order_by("created_at"))
        out = []
        for o in orders:
            out.append({
                "id": o.id, "kot_no": o.kot_no, "kitchen_status": o.kitchen_status,
                "table": o.table.name if o.table else o.get_mode_display(),
                "created_at": o.created_at,
                "items": [{"name": l.display_name, "qty": l.qty,
                           "station": l.menu_item.station} for l in o.lines.all()],
            })
        return Response(out)

    @action(detail=True, methods=["post"])
    def bump(self, request, pk=None):
        order = Order.objects.filter(pk=pk).first()
        if not order:
            return Response({"detail": "not found"}, status=404)
        order.kitchen_status = "ready" if order.kitchen_status == "cooking" else "served"
        order.save(update_fields=["kitchen_status"])
        return Response({"id": order.id, "kitchen_status": order.kitchen_status})


class CategoryViewSet(ModuleViewSetMixin, viewsets.ModelViewSet):
    module = "pos"
    queryset = Category.objects.all()
    serializer_class = CategorySerializer


class MenuItemViewSet(ModuleViewSetMixin, viewsets.ModelViewSet):
    module = "pos"
    queryset = MenuItem.objects.select_related("category").all()
    serializer_class = MenuItemSerializer

    def get_queryset(self):
        qs = super().get_queryset()
        cat = self.request.query_params.get("category")
        if cat:
            qs = qs.filter(category_id=cat)
        return qs


class OrderViewSet(ModuleViewSetMixin, viewsets.ModelViewSet):
    module = "pos"
    queryset = Order.objects.prefetch_related("lines__menu_item").select_related("table").all()
    serializer_class = OrderSerializer

    def get_queryset(self):
        qs = super().get_queryset()
        status_ = self.request.query_params.get("status")
        if status_:
            qs = qs.filter(status=status_)
        return qs

    @action(detail=True, methods=["post"])
    def add_item(self, request, pk=None):
        order = self.get_object()
        item = MenuItem.objects.filter(pk=request.data.get("menu_item")).first()
        if not item:
            return Response({"detail": "menu_item not found"}, status=404)
        qty = int(request.data.get("qty", 1))

        # Variant pricing (FR-MNU-004)
        variant = None
        base_price = item.price
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
                return Response(OrderSerializer(order).data)
        OrderLine.objects.create(
            order=order, menu_item=item, variant=variant, addons=addons,
            qty=qty, unit_price=unit_price, note=request.data.get("note", ""),
        )
        return Response(OrderSerializer(order).data)

    @action(detail=True, methods=["post"])
    def set_qty(self, request, pk=None):
        order = self.get_object()
        line = order.lines.filter(pk=request.data.get("line")).first()
        if not line:
            return Response({"detail": "line not found"}, status=404)
        qty = int(request.data.get("qty", 0))
        if qty <= 0:
            line.delete()
        else:
            line.qty = qty
            line.save(update_fields=["qty"])
        return Response(OrderSerializer(order).data)

    def _free_table_if_idle(self, table):
        if table and not table.orders.filter(status__in=[Order.OPEN, Order.KOT_FIRED]).exists():
            table.status = Table.FREE
            table.save(update_fields=["status"])

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
                for ln in payload.get("lines", []):
                    item = MenuItem.objects.filter(pk=ln.get("menu_item")).first()
                    if not item:
                        continue
                    OrderLine.objects.create(
                        order=order, menu_item=item, qty=int(ln.get("qty", 1)),
                        unit_price=Decimal(str(ln.get("unit_price", item.price))),
                        kot_fired=True,
                    )
                if payload.get("settled"):
                    t = order.totals()
                    Settlement.objects.create(
                        tender=payload.get("tender", "Cash"), amount=t["total"],
                        reference=f"offline {uuid[:8]}",
                    )
                    order.status = Order.SETTLED
                    order.save(update_fields=["status"])
            log_action(request.user, "offline_sync", entity="Order", entity_id=order.id,
                       after={"client_uuid": uuid})
            results.append({"client_uuid": uuid, "id": order.id, "created": True})
        return Response({"results": results})

    @action(detail=True, methods=["post"])
    def move(self, request, pk=None):
        """Move an order to another table, preserving items + KOT history (FR-TBL-004)."""
        order = self.get_object()
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
        src = Order.objects.filter(pk=request.data.get("source")).first()
        if not src or src.id == order.id:
            return Response({"detail": "invalid source order"}, status=400)
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
        mgr = _valid_override(request.data.get("override"))
        if not mgr:
            return Response({"detail": "Manager override required to void", "override_required": True},
                            status=403)
        order.status = Order.SETTLED  # closed as void
        order.discount_reason = f"VOID by {request.user.username} (override {mgr.username})"
        order.save(update_fields=["status", "discount_reason"])
        self._free_table_if_idle(order.table)
        log_action(request.user, "order_void", entity="Order", entity_id=order.id,
                   after={"override": mgr.username, "reason": request.data.get("reason", "")})
        return Response(OrderSerializer(order).data)

    @action(detail=True, methods=["post"])
    def apply_discount(self, request, pk=None):
        """Order-level discount within the user's cap; over-cap needs manager override
        (BRD FR-POS-012, FR-USR-004/006)."""
        order = self.get_object()
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
        pending = order.lines.filter(kot_fired=False)
        if not pending.exists():
            return Response({"detail": "nothing new to fire"}, status=400)
        # Cross-module seam: deduct recipe ingredients for the newly fired lines
        # before flipping kot_fired (so the deduction sees only this round).
        from apps.recipes.services import deduct_for_newly_fired
        deduct_for_newly_fired(order, list(pending))
        pending.update(kot_fired=True)
        if not order.kot_no:
            order.kot_no = f"KOT-{order.id:05d}"
        order.status = Order.KOT_FIRED
        order.kitchen_status = "cooking"  # appears on the KDS
        order.save(update_fields=["kot_no", "status", "kitchen_status"])
        if order.table:
            order.table.status = Table.RUNNING
            order.table.save(update_fields=["status"])
        log_action(request.user, "kot_fire", entity="Order", entity_id=order.id,
                   after={"kot": order.kot_no})
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
        t = order.totals()
        tender = request.data.get("tender", "Cash")
        reference = request.data.get("reference", f"POS order {order.id}")
        if tender == "Gateway":
            from apps.integrations import services as integ
            result = integ.charge_card(t["total"], request.data.get("token", ""), reference)
            if result.get("status") != "approved":
                return Response({"detail": result.get("reason", "payment declined")}, status=402)
            reference = result["ref"]
        Settlement.objects.create(tender=tender, amount=t["total"], reference=reference)
        self._finalize_promotions(order, t["total"])
        order.status = Order.SETTLED
        order.save(update_fields=["status"])
        if order.table:
            order.table.status = Table.FREE
            order.table.save(update_fields=["status"])
        # Receipt notification to the customer (FR-NOT-001).
        if order.customer:
            from apps.integrations import services as integ
            integ.notify("sms", order.customer.mobile,
                         f"Thanks for dining with Hearth! Bill {reference}: ₹{t['total']}.")
        log_action(request.user, "pos_settle", entity="Order", entity_id=order.id,
                   after={"total": str(t["total"]), "discount": str(t["discount"]), "tender": tender})
        return Response(OrderSerializer(order).data)

    @action(detail=True, methods=["post"])
    def post_to_room(self, request, pk=None):
        """Cross-module seam: post an in-house guest's F&B bill to their folio (FR-PAY-009).

        Only when the Hotel edition (hms) is enabled, else hidden/blocked.
        """
        if not active_entitlements().get("hms"):
            return Response(
                {"detail": "post-to-room is unavailable in the Restaurant edition"},
                status=403,
            )
        order = self.get_object()
        folio = Folio.objects.filter(pk=request.data.get("folio"), status=Folio.OPEN).first()
        if not folio:
            return Response({"detail": "open folio not found for this room/guest"}, status=400)
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
            if order.table:
                order.table.status = Table.FREE
                order.table.save(update_fields=["status"])
        log_action(request.user, "post_to_room", entity="Order", entity_id=order.id,
                   after={"folio": folio.id})
        return Response(OrderSerializer(order).data)
