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
        order.save(update_fields=["kot_no", "status"])
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
        """Settle by tender at the outlet (FR-PAY-001)."""
        order = self.get_object()
        t = order.totals()
        Settlement.objects.create(
            tender=request.data.get("tender", "Cash"),
            amount=t["total"],
            reference=request.data.get("reference", f"POS order {order.id}"),
        )
        self._finalize_promotions(order, t["total"])
        order.status = Order.SETTLED
        order.save(update_fields=["status"])
        if order.table:
            order.table.status = Table.FREE
            order.table.save(update_fields=["status"])
        log_action(request.user, "pos_settle", entity="Order", entity_id=order.id,
                   after={"total": str(t["total"]), "discount": str(t["discount"])})
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
