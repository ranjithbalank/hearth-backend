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
        "requested_by": po.requested_by,
        "location": po.location_id, "location_name": po.location.name if po.location_id else None,
        "total": str(po.total), "created_at": po.created_at,
        "lines": [
            {"id": l.id, "ingredient": l.ingredient.name, "qty": str(l.qty),
             "rate": str(l.rate), "received_qty": str(l.received_qty)}
            for l in po.lines.all()
        ],
        # Receipts on file — presence flag only; the image comes from
        # /goods-receipts/{id}/bill/ on demand.
        "grns": [{"id": g.id, "grn_no": g.grn_no, "has_bill": bool(g.bill_image)}
                 for g in po.grns.all()],
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


def _supplier_dict(s):
    return {"id": s.id, "name": s.name, "gstin": s.gstin, "contact": s.contact,
            "payment_terms": s.payment_terms, "lead_time_days": s.lead_time_days,
            "rating": str(s.rating), "location": s.location_id}


class SupplierViewSet(ModuleViewSetMixin, viewsets.ViewSet):
    module = "suppliers"

    def list(self, request):
        qs = shared_or_visible(Supplier.objects.all(), request)
        return Response([_supplier_dict(s) for s in qs])

    def create(self, request):
        """Register a supplier — POs can't be raised for one that isn't on
        file, and until go-live QA (TC-086) this master was seed-only."""
        from apps.accounts.models import log_action
        name = (request.data.get("name") or "").strip()
        if not name:
            return Response({"detail": "supplier name is required"}, status=400)
        if Supplier.objects.filter(name__iexact=name).exists():
            return Response({"detail": f"'{name}' is already on the supplier list"}, status=400)
        s = Supplier.objects.create(
            name=name,
            gstin=(request.data.get("gstin") or "").strip(),
            contact=(request.data.get("contact") or "").strip(),
            payment_terms=(request.data.get("payment_terms") or "").strip(),
            lead_time_days=int(request.data.get("lead_time_days") or 2),
            location_id=request.data.get("location") or _requester_branch(request),
        )
        log_action(request.user, "supplier_created", entity="Supplier", entity_id=s.id,
                   after={"name": s.name})
        return Response(_supplier_dict(s), status=201)

    def partial_update(self, request, pk=None):
        """Edit a supplier as terms change — including the rating, which is
        the buyer's own scorecard of them."""
        from decimal import Decimal, InvalidOperation
        s = shared_or_visible(Supplier.objects.all(), request).filter(pk=pk).first()
        if not s:
            return Response({"detail": "not found"}, status=404)
        before = _supplier_dict(s)
        if "name" in request.data:
            name = (request.data.get("name") or "").strip()
            if not name:
                return Response({"detail": "supplier name is required"}, status=400)
            if Supplier.objects.exclude(pk=s.pk).filter(name__iexact=name).exists():
                return Response({"detail": f"'{name}' is already on the supplier list"}, status=400)
            s.name = name
        for f in ("gstin", "contact", "payment_terms"):
            if f in request.data:
                setattr(s, f, (request.data.get(f) or "").strip())
        if "lead_time_days" in request.data:
            try:
                s.lead_time_days = max(0, int(request.data.get("lead_time_days") or 0))
            except (TypeError, ValueError):
                return Response({"detail": "lead time must be a number of days"}, status=400)
        if "rating" in request.data:
            try:
                rating = Decimal(str(request.data.get("rating")))
            except InvalidOperation:
                return Response({"detail": "rating must be a number"}, status=400)
            if not (0 <= rating <= 5):
                return Response({"detail": "rating is 0–5"}, status=400)
            s.rating = rating
        s.save()
        log_action(request.user, "supplier_updated", entity="Supplier", entity_id=s.id,
                   before=before, after=_supplier_dict(s))
        return Response(_supplier_dict(s))


def _vendor_dict(v):
    return {"id": v.id, "name": v.name, "category": v.category, "contact": v.contact,
            "payment_terms": v.payment_terms, "status": v.status, "location": v.location_id}


class VendorViewSet(ModuleViewSetMixin, viewsets.ViewSet):
    module = "vendors"

    def list(self, request):
        qs = shared_or_visible(Vendor.objects.all(), request)
        return Response([_vendor_dict(v) for v in qs])

    def create(self, request):
        """Register a service vendor (same seed-only gap as suppliers)."""
        from apps.accounts.models import log_action
        name = (request.data.get("name") or "").strip()
        if not name:
            return Response({"detail": "vendor name is required"}, status=400)
        if Vendor.objects.filter(name__iexact=name).exists():
            return Response({"detail": f"'{name}' is already on the vendor list"}, status=400)
        v = Vendor.objects.create(
            name=name,
            category=(request.data.get("category") or "").strip(),
            contact=(request.data.get("contact") or "").strip(),
            payment_terms=(request.data.get("payment_terms") or "").strip(),
            location_id=request.data.get("location") or _requester_branch(request),
        )
        log_action(request.user, "vendor_created", entity="Vendor", entity_id=v.id,
                   after={"name": v.name})
        return Response(_vendor_dict(v), status=201)


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
            po = PurchaseOrder.objects.create(supplier=supplier, location_id=_requester_branch(request),
                                              requested_by=request.user.username)
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
        if po.requested_by and po.requested_by == request.user.username:
            return Response({"detail": "you raised this PO — someone else must approve the spend"},
                            status=403)
        po.status = PurchaseOrder.APPROVED
        po.save(update_fields=["status"])
        log_action(request.user, "po_approve", entity="PurchaseOrder", entity_id=po.id)
        return Response(_po_dict(po))

    @action(detail=True, methods=["post"])
    def receive(self, request, pk=None):
        """Goods receipt: post received quantities to stock. With no body the
        full outstanding quantity is received (the common case); pass
        {lines: [{line: <id>, qty: "..."}]} to book a short/partial delivery —
        the PO stays approved with the remainder outstanding, and a later
        receipt (another GRN) closes it."""
        from decimal import Decimal, InvalidOperation
        from apps.accounts.constants import PO_HANDLER_ROLES
        if getattr(request.user, "role", "") not in PO_HANDLER_ROLES:
            return Response(
                {"detail": "goods receipt is the store's job — Restaurant Manager or Store Keeper"},
                status=403)
        po = shared_or_visible(PurchaseOrder.objects.all(), request).prefetch_related(
            "lines__ingredient").filter(pk=pk).first()
        if not po or po.status != PurchaseOrder.APPROVED:
            return Response({"detail": "PO must be approved before receipt"}, status=400)

        # Resolve how much of each line this delivery brings.
        requested = request.data.get("lines")
        to_receive = {}
        if requested is None:
            for line in po.lines.all():
                to_receive[line.id] = line.qty - line.received_qty
        else:
            if not isinstance(requested, list) or not requested:
                return Response({"detail": "lines must be a non-empty list"}, status=400)
            lines_by_id = {l.id: l for l in po.lines.all()}
            for row in requested:
                line = lines_by_id.get(row.get("line"))
                if not line:
                    return Response({"detail": "unknown PO line"}, status=400)
                try:
                    qty = Decimal(str(row.get("qty")))
                except InvalidOperation:
                    return Response({"detail": f"{line.ingredient.name}: qty must be a number"},
                                    status=400)
                outstanding = line.qty - line.received_qty
                if qty < 0:
                    return Response({"detail": f"{line.ingredient.name}: qty can't be negative"},
                                    status=400)
                if qty > outstanding:
                    return Response({"detail": f"{line.ingredient.name}: only {outstanding} "
                                               f"outstanding on this PO"}, status=400)
                to_receive[line.id] = qty
        if not any(q > 0 for q in to_receive.values()):
            return Response({"detail": "nothing to receive — every quantity is zero"}, status=400)

        # Optional photo of the supplier's bill/challan (same rules as ID scans).
        bill_image = request.data.get("bill_image") or ""
        if bill_image and not bill_image.startswith("data:image/"):
            return Response({"detail": "the bill must be an image"}, status=400)
        if len(bill_image) > 800_000:
            return Response({"detail": "the bill photo is too large — retake at a smaller size"},
                            status=400)

        with transaction.atomic():
            from apps.accounts.models import Property
            from apps.accounts.numbering import next_document_number
            grn = GoodsReceipt.objects.create(purchase_order=po, note=request.data.get("note", ""),
                                              bill_image=bill_image)
            prop = Property.objects.first()
            grn.grn_no = next_document_number(GoodsReceipt, "grn_no", prop.grn_prefix if prop else "GRN")
            grn.save(update_fields=["grn_no"])
            for line in po.lines.all():
                qty = to_receive.get(line.id, Decimal("0"))
                if qty <= 0:
                    continue
                ing = line.ingredient
                # Re-cost on receipt (weighted average of held stock + this
                # consignment) so plate costs track what stock actually cost.
                if line.rate and line.rate > 0:
                    held = max(ing.current_stock or Decimal("0"), Decimal("0"))
                    total_qty = held + qty
                    if total_qty > 0:
                        ing.unit_cost = round(
                            ((held * (ing.unit_cost or Decimal("0")))
                             + qty * line.rate) / total_qty, 2)
                        ing.save(update_fields=["unit_cost"])
                apply_movement(ing, "receipt", qty,
                               reason="GRN", source=f"PO:{po.id}", user=request.user)
                line.received_qty += qty
                line.save(update_fields=["received_qty"])
            fully = all(l.received_qty >= l.qty for l in po.lines.all())
            if fully:
                po.status = PurchaseOrder.RECEIVED
                po.save(update_fields=["status"])
        log_action(request.user, "goods_receipt", entity="PurchaseOrder", entity_id=po.id,
                   after={"grn": grn.id, "partial": not fully})
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
             "note": g.note, "has_bill": bool(g.bill_image), "created_at": g.created_at}
            for g in qs[:30]
        ])

    @action(detail=True, methods=["get"])
    def bill(self, request, pk=None):
        """The supplier-bill photo attached at receipt, on demand."""
        g = shared_or_visible(GoodsReceipt.objects.all(), request,
                              field="purchase_order__location").filter(pk=pk).first()
        if not g:
            return Response({"detail": "not found"}, status=404)
        if not g.bill_image:
            return Response({"detail": "no bill photo on this GRN"}, status=404)
        return Response({"id": g.id, "grn_no": g.grn_no, "bill_image": g.bill_image})
