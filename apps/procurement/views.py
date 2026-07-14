from django.db import transaction
from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from apps.accounts.models import log_action
from apps.accounts.permissions import (
    AnyModuleViewSetMixin,
    ModuleViewSetMixin,
    resolve_active_branch,
    shared_or_visible,
    visible_branch_ids,
)
from apps.inventory.models import apply_movement

from .models import GoodsReceipt, PurchaseOrder, PurchaseOrderLine, Supplier, Vendor


def _po_dict(po):
    return {
        "id": po.id, "po_no": po.po_no, "supplier": po.supplier.name, "status": po.status,
        "location": po.location_id, "location_name": po.location.name if po.location_id else None,
        "total": str(po.total), "created_at": po.created_at,
        "lines": [
            {"ingredient": l.ingredient.name, "qty": str(l.qty),
             "rate": str(l.rate), "received_qty": str(l.received_qty)}
            for l in po.lines.all()
        ],
    }


def _requester_branch(request):
    """Active branch header if sent, else the caller's own branch when
    they're only ever assigned to one — same fallback as POS orders and
    material requests, so a single-branch login never has to pick it."""
    location = resolve_active_branch(request)
    if location is None:
        visible = visible_branch_ids(request)
        if isinstance(visible, set) and len(visible) == 1:
            location = next(iter(visible))
    return location


class SupplierViewSet(ModuleViewSetMixin, viewsets.ViewSet):
    module = "suppliers"

    def list(self, request):
        qs = shared_or_visible(Supplier.objects.all(), request)
        return Response([
            {"id": s.id, "name": s.name, "gstin": s.gstin, "contact": s.contact,
             "payment_terms": s.payment_terms, "lead_time_days": s.lead_time_days,
             "rating": str(s.rating), "location": s.location_id}
            for s in qs
        ])


class VendorViewSet(ModuleViewSetMixin, viewsets.ViewSet):
    module = "vendors"

    def list(self, request):
        qs = shared_or_visible(Vendor.objects.all(), request)
        return Response([
            {"id": v.id, "name": v.name, "category": v.category, "contact": v.contact,
             "payment_terms": v.payment_terms, "status": v.status, "location": v.location_id}
            for v in qs
        ])


class PurchaseOrderViewSet(AnyModuleViewSetMixin, viewsets.ViewSet):
    # Two doors into POs: the store side ("procurement" — Store Keeper,
    # Restaurant Manager) and the payables side ("pomanage" — Finance, whose
    # Purchase Orders screen gates on it and who sits in PO_APPROVER_ROLES).
    # Gating on "procurement" alone left Finance a designated approver who
    # could never actually reach the approve endpoint. Spend approval is
    # still guarded by PO_APPROVER_ROLES inside approve() regardless of
    # which module let the request in.
    modules = ["procurement", "pomanage"]

    def list(self, request):
        # A PO is exclusive to the branch that raised it, not shared like a
        # supplier — same "mine + not-yet-branch-tagged" rule as orders and
        # indents, so a pre-existing PO doesn't vanish for anyone.
        qs = shared_or_visible(
            PurchaseOrder.objects.select_related("supplier").prefetch_related("lines__ingredient"),
            request,
        )
        status_ = request.query_params.get("status")
        if status_:
            qs = qs.filter(status=status_)
        return Response([_po_dict(po) for po in qs])

    def create(self, request):
        """Raise a purchase order: {supplier, lines: [{ingredient, qty, rate?}]}.
        Rate defaults to the material's current purchase rate."""
        from decimal import Decimal, InvalidOperation

        from apps.accounts.constants import PO_HANDLER_ROLES
        from apps.inventory.models import Ingredient

        if getattr(request.user, "role", "") not in PO_HANDLER_ROLES:
            return Response(
                {"detail": "raising a purchase order is the store's job — Restaurant Manager or "
                           "Store Keeper (Finance approves the spend once it's raised)"},
                status=403)
        supplier = shared_or_visible(Supplier.objects.all(), request).filter(
            pk=request.data.get("supplier")).first()
        if not supplier:
            return Response({"detail": "supplier not found"}, status=400)
        wanted = request.data.get("lines") or []
        if not wanted:
            return Response({"detail": "at least one line is required"}, status=400)
        ingredient_qs = shared_or_visible(Ingredient.objects.all(), request)
        parsed = []
        for w in wanted:
            ing = ingredient_qs.filter(pk=w.get("ingredient")).first()
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
            from apps.accounts.models import Property
            from apps.accounts.numbering import next_document_number
            po = PurchaseOrder.objects.create(supplier=supplier, location_id=_requester_branch(request))
            prop = Property.objects.first()
            po.po_no = next_document_number(PurchaseOrder, "po_no", prop.po_prefix if prop else "PO")
            po.save(update_fields=["po_no"])
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
        po = shared_or_visible(PurchaseOrder.objects.all(), request).filter(pk=pk).first()
        if not po or po.status != PurchaseOrder.PENDING:
            return Response({"detail": "PO not pending"}, status=400)
        po.status = PurchaseOrder.APPROVED
        po.save(update_fields=["status"])
        log_action(request.user, "po_approve", entity="PurchaseOrder", entity_id=po.id)
        return Response(_po_dict(po))

    @action(detail=True, methods=["post"])
    def receive(self, request, pk=None):
        """Goods receipt: post each line's qty to stock and mark the PO received."""
        from apps.accounts.constants import PO_HANDLER_ROLES
        if getattr(request.user, "role", "") not in PO_HANDLER_ROLES:
            return Response(
                {"detail": "goods receipt is the store's job — Restaurant Manager or Store Keeper"},
                status=403)
        po = shared_or_visible(PurchaseOrder.objects.all(), request).prefetch_related(
            "lines__ingredient").filter(pk=pk).first()
        if not po or po.status != PurchaseOrder.APPROVED:
            return Response({"detail": "PO must be approved before receipt"}, status=400)
        with transaction.atomic():
            from apps.accounts.models import Property
            from apps.accounts.numbering import next_document_number
            grn = GoodsReceipt.objects.create(purchase_order=po, note=request.data.get("note", ""))
            prop = Property.objects.first()
            grn.grn_no = next_document_number(GoodsReceipt, "grn_no", prop.grn_prefix if prop else "GRN")
            grn.save(update_fields=["grn_no"])
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
        # GoodsReceipt has no location of its own — it follows its PO's.
        qs = shared_or_visible(
            GoodsReceipt.objects.select_related("purchase_order__supplier"),
            request, field="purchase_order__location",
        )
        return Response([
            {"id": g.id, "grn_no": g.grn_no, "po": g.purchase_order_id,
             "po_no": g.purchase_order.po_no, "supplier": g.purchase_order.supplier.name,
             "note": g.note, "created_at": g.created_at}
            for g in qs[:30]
        ])
