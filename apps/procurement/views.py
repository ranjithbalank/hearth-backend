from django.db import transaction
from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from apps.accounts.models import log_action
from apps.accounts.permissions import ModuleViewSetMixin
from apps.inventory.models import apply_movement

from .models import GoodsReceipt, PurchaseOrder, PurchaseOrderLine, Supplier, Vendor


def _po_dict(po):
    return {
        "id": po.id, "supplier": po.supplier.name, "status": po.status,
        "total": str(po.total), "created_at": po.created_at,
        "lines": [
            {"ingredient": l.ingredient.name, "qty": str(l.qty),
             "rate": str(l.rate), "received_qty": str(l.received_qty)}
            for l in po.lines.all()
        ],
    }


class SupplierViewSet(ModuleViewSetMixin, viewsets.ViewSet):
    module = "suppliers"

    def list(self, request):
        return Response([
            {"id": s.id, "name": s.name, "gstin": s.gstin, "contact": s.contact,
             "payment_terms": s.payment_terms, "lead_time_days": s.lead_time_days,
             "rating": str(s.rating)}
            for s in Supplier.objects.all()
        ])


class VendorViewSet(ModuleViewSetMixin, viewsets.ViewSet):
    module = "vendors"

    def list(self, request):
        return Response([
            {"id": v.id, "name": v.name, "category": v.category, "contact": v.contact,
             "payment_terms": v.payment_terms, "status": v.status}
            for v in Vendor.objects.all()
        ])


class PurchaseOrderViewSet(ModuleViewSetMixin, viewsets.ViewSet):
    module = "procurement"

    def list(self, request):
        qs = PurchaseOrder.objects.select_related("supplier").prefetch_related("lines__ingredient")
        status_ = request.query_params.get("status")
        if status_:
            qs = qs.filter(status=status_)
        return Response([_po_dict(po) for po in qs])

    def create(self, request):
        """Raise a purchase order: {supplier, lines: [{ingredient, qty, rate?}]}.
        Rate defaults to the material's current purchase rate."""
        from decimal import Decimal, InvalidOperation

        from apps.inventory.models import Ingredient

        supplier = Supplier.objects.filter(pk=request.data.get("supplier")).first()
        if not supplier:
            return Response({"detail": "supplier not found"}, status=400)
        wanted = request.data.get("lines") or []
        if not wanted:
            return Response({"detail": "at least one line is required"}, status=400)
        parsed = []
        for w in wanted:
            ing = Ingredient.objects.filter(pk=w.get("ingredient")).first()
            if not ing:
                return Response({"detail": "unknown raw material on a line"}, status=400)
            try:
                qty = Decimal(str(w.get("qty", 0)))
                rate = Decimal(str(w.get("rate") or ing.unit_cost or 0))
            except InvalidOperation:
                return Response({"detail": "invalid quantity or rate"}, status=400)
            if qty <= 0:
                return Response({"detail": "quantities must be positive"}, status=400)
            parsed.append((ing, qty, rate))
        with transaction.atomic():
            po = PurchaseOrder.objects.create(supplier=supplier)
            for ing, qty, rate in parsed:
                PurchaseOrderLine.objects.create(purchase_order=po, ingredient=ing,
                                                 qty=qty, rate=rate)
        log_action(request.user, "po_create", entity="PurchaseOrder", entity_id=po.id,
                   after={"supplier": supplier.name, "lines": len(parsed)})
        return Response(_po_dict(po), status=201)

    @action(detail=True, methods=["post"])
    def approve(self, request, pk=None):
        from apps.accounts.constants import PO_APPROVER_ROLES
        if getattr(request.user, "role", "") not in PO_APPROVER_ROLES:
            return Response(
                {"detail": "PO approval is a spend decision — it needs the restaurant manager, finance or GM"},
                status=403)
        po = PurchaseOrder.objects.filter(pk=pk).first()
        if not po or po.status != PurchaseOrder.PENDING:
            return Response({"detail": "PO not pending"}, status=400)
        po.status = PurchaseOrder.APPROVED
        po.save(update_fields=["status"])
        log_action(request.user, "po_approve", entity="PurchaseOrder", entity_id=po.id)
        return Response(_po_dict(po))

    @action(detail=True, methods=["post"])
    def receive(self, request, pk=None):
        """Goods receipt: post each line's qty to stock and mark the PO received."""
        po = PurchaseOrder.objects.filter(pk=pk).prefetch_related("lines__ingredient").first()
        if not po or po.status != PurchaseOrder.APPROVED:
            return Response({"detail": "PO must be approved before receipt"}, status=400)
        with transaction.atomic():
            grn = GoodsReceipt.objects.create(purchase_order=po, note=request.data.get("note", ""))
            for line in po.lines.all():
                outstanding = line.qty - line.received_qty
                if outstanding <= 0:
                    continue
                ing = line.ingredient
                # Re-cost on receipt (weighted average of held stock + this
                # consignment) so plate costs track what stock actually cost.
                if line.rate and line.rate > 0:
                    from decimal import Decimal
                    held = max(ing.current_stock or Decimal("0"), Decimal("0"))
                    total_qty = held + outstanding
                    if total_qty > 0:
                        ing.unit_cost = round(
                            ((held * (ing.unit_cost or Decimal("0")))
                             + outstanding * line.rate) / total_qty, 2)
                        ing.save(update_fields=["unit_cost"])
                apply_movement(ing, "receipt", outstanding,
                               reason="GRN", source=f"PO:{po.id}", user=request.user)
                line.received_qty = line.qty
                line.save(update_fields=["received_qty"])
            po.status = PurchaseOrder.RECEIVED
            po.save(update_fields=["status"])
        log_action(request.user, "goods_receipt", entity="PurchaseOrder", entity_id=po.id,
                   after={"grn": grn.id})
        return Response(_po_dict(po))


class GoodsReceiptViewSet(ModuleViewSetMixin, viewsets.ViewSet):
    module = "procurement"

    def list(self, request):
        return Response([
            {"id": g.id, "po": g.purchase_order_id, "supplier": g.purchase_order.supplier.name,
             "note": g.note, "created_at": g.created_at}
            for g in GoodsReceipt.objects.select_related("purchase_order__supplier")[:30]
        ])
