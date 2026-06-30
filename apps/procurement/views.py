from django.db import transaction
from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from apps.accounts.models import log_action
from apps.accounts.permissions import ModuleViewSetMixin
from apps.inventory.models import apply_movement

from .models import GoodsReceipt, PurchaseOrder, Supplier, Vendor


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

    @action(detail=True, methods=["post"])
    def approve(self, request, pk=None):
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
                apply_movement(line.ingredient, "receipt", outstanding,
                               reason="GRN", source=f"PO:{po.id}")
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
