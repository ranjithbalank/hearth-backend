from decimal import Decimal

from django.db import transaction
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from apps.accounts.models import log_action
from apps.accounts.permissions import ModuleViewSetMixin, active_entitlements
from apps.frontoffice import services as fo_services
from apps.frontoffice.models import Folio, FolioLine, Settlement

from .models import Category, MenuItem, Order, OrderLine, Table
from .serializers import (
    CategorySerializer,
    MenuItemSerializer,
    OrderSerializer,
    TableSerializer,
)


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
        line = order.lines.filter(menu_item=item, kot_fired=False, note="").first()
        if line:
            line.qty += qty
            line.save(update_fields=["qty"])
        else:
            line = OrderLine.objects.create(
                order=order, menu_item=item, qty=qty,
                unit_price=item.price, note=request.data.get("note", ""),
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
        order.status = Order.SETTLED
        order.save(update_fields=["status"])
        if order.table:
            order.table.status = Table.FREE
            order.table.save(update_fields=["status"])
        log_action(request.user, "pos_settle", entity="Order", entity_id=order.id,
                   after={"total": str(t["total"])})
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
        with transaction.atomic():
            for line in order.lines.select_related("menu_item"):
                fo_services.post_charge(
                    folio, kind=FolioLine.KIND_FNB,
                    description=f"{line.qty}× {line.menu_item.name}",
                    amount=line.unit_price * line.qty,
                    gst_rate=line.menu_item.gst_rate,
                    source=f"POS order {order.id}", user=request.user,
                )
            order.status = Order.POSTED_TO_ROOM
            order.folio = folio
            order.save(update_fields=["status", "folio"])
            if order.table:
                order.table.status = Table.FREE
                order.table.save(update_fields=["status"])
        log_action(request.user, "post_to_room", entity="Order", entity_id=order.id,
                   after={"folio": folio.id})
        return Response(OrderSerializer(order).data)
