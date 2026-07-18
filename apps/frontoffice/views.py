from datetime import date

from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from apps.accounts.permissions import ModuleViewSetMixin, shared_or_visible
from apps.reservations.models import Reservation
from apps.rooms.models import Room

from . import services
from .models import Folio, FolioLine, NightAuditRun
from .serializers import FolioSerializer, NightAuditRunSerializer


class FolioViewSet(ModuleViewSetMixin, viewsets.ModelViewSet):
    module = "folio"
    queryset = Folio.objects.prefetch_related("lines", "settlements").select_related("room").all()
    serializer_class = FolioSerializer

    def get_queryset(self):
        # Each desk sees its own branch's bills (+ legacy untagged rows).
        qs = shared_or_visible(super().get_queryset(), self.request)
        status_ = self.request.query_params.get("status")
        room = self.request.query_params.get("room")
        if status_:
            qs = qs.filter(status=status_)
        if room:
            qs = qs.filter(room__number=room)
        return qs

    @action(detail=True, methods=["get"])
    def registration(self, request, pk=None):
        """The stay's registration-card evidence: ID proof scan + guest
        signature. Kept out of the folio list/detail payloads (sensitive PII
        and big base64 blobs) — and every read of it leaves an audit entry."""
        from apps.accounts.models import log_action
        folio = self.get_object()
        log_action(request.user, "registration_viewed", entity="Folio", entity_id=folio.id,
                   note="ID scan / signature viewed")
        return Response({
            "id": folio.id, "guest_name": folio.guest_name,
            "id_type": folio.id_type, "id_number": folio.id_number,
            "id_scan": folio.id_scan, "signature": folio.signature,
        })

    @action(detail=True, methods=["post"])
    def transfer_charge(self, request, pk=None):
        """Move a charge line to another OPEN folio (FR-PMS-006 split/route):
        the room-service dinner posted to 102 that actually belongs to the
        companion in 103, or splitting incidentals between two stays.
        Body: {line: <FolioLine id>, to_folio: <Folio id>}. Room-night and tax
        lines stay put — they belong to the stay that slept in the room."""
        from apps.accounts.models import log_action
        folio = self.get_object()
        if folio.status != Folio.OPEN:
            return Response({"detail": "the source folio is not open"}, status=400)
        line = folio.lines.filter(pk=request.data.get("line")).first()
        if not line:
            return Response({"detail": "charge line not found on this folio"}, status=404)
        if line.kind in (FolioLine.KIND_ROOM, FolioLine.KIND_TAX):
            return Response({"detail": "room and tax charges belong to the stay — only F&B and "
                                        "incidentals can transfer"}, status=400)
        target = Folio.objects.filter(pk=request.data.get("to_folio"), status=Folio.OPEN).first()
        if not target:
            return Response({"detail": "target folio not found or not open"}, status=400)
        if target.pk == folio.pk:
            return Response({"detail": "target is the same folio"}, status=400)
        before = {"folio": folio.id, "description": line.description, "total": str(line.total)}
        line.folio = target
        line.source = (line.source + " · " if line.source else "") + f"moved from folio {folio.id}"
        line.save(update_fields=["folio", "source"])
        log_action(request.user, "charge_transfer", entity="FolioLine", entity_id=line.id,
                   before=before, after={"folio": target.id},
                   note=f"{line.description} → {target.guest_name}")
        return Response({"moved": line.id, "to_folio": target.id, "to_guest": target.guest_name})

    @action(detail=True, methods=["post"])
    def settle(self, request, pk=None):
        folio = self.get_object()
        payments = request.data.get("payments", [])
        if not payments:
            return Response({"detail": "payments required"}, status=400)
        services.settle_folio(folio, payments, user=request.user)
        # settle_folio() creates new Settlement rows — the queryset that fetched
        # `folio` already prefetched (and cached) the old, empty settlements list,
        # so re-fetch before serializing or the response would show stale data.
        fresh = Folio.objects.prefetch_related("lines", "settlements").get(pk=folio.pk)
        return Response(FolioSerializer(fresh).data)

    @action(detail=True, methods=["post"])
    def billing_mode(self, request, pk=None):
        """Switch this bill between GST tax invoice and bill of supply (BRD 5.23).
        Existing charge lines are recomputed accordingly."""
        folio = self.get_object()
        try:
            services.set_billing_mode(folio, request.data.get("mode", ""), user=request.user)
        except ValueError as e:
            return Response({"detail": str(e)}, status=400)
        return Response(FolioSerializer(folio).data)

    @action(detail=True, methods=["get"])
    def invoice_pdf(self, request, pk=None):
        """Download the folio bill: GST tax invoice or bill of supply (FR-TAX-003)."""
        from django.http import HttpResponse

        from apps.accounts.views import get_property
        from .invoice_pdf import build_invoice_pdf
        folio = self.get_object()
        prop = get_property()
        with_gst = services.effective_billing_mode(folio) == "with_gst"
        pdf = build_invoice_pdf(folio, prop.name, prop.gstin, prop.address, with_gst=with_gst,
                                logo=prop.logo, doc_header=prop.doc_header,
                                doc_footer=prop.doc_footer,
                                doc_header_align=prop.doc_header_align,
                                doc_footer_align=prop.doc_footer_align)
        resp = HttpResponse(pdf.read(), content_type="application/pdf")
        name = folio.invoice_no or f"folio-{folio.id}"
        resp["Content-Disposition"] = f'attachment; filename="{name}.pdf"'
        return resp

    @action(detail=True, methods=["post"])
    def email_invoice(self, request, pk=None):
        """Send the invoice to the guest via the messaging adapter (FR-NOT-001)."""
        from apps.integrations import services as integ
        folio = self.get_object()
        guest = folio.reservation.guest if folio.reservation else None
        email = getattr(guest, "email", "") or ""
        mobile = getattr(guest, "mobile", "") or ""
        from apps.accounts.constants import currency_symbol
        body = (f"{folio.guest_name}, your invoice {folio.invoice_no or '(pending)'} "
                f"total {currency_symbol()}{folio.charges_total}. Thank you for staying with us.")
        if email:
            integ.notify("email", email, body)
            return Response({"sent": True, "channel": "email", "to": email})
        if mobile:
            integ.notify("sms", mobile, body)
            return Response({"sent": True, "channel": "sms", "to": mobile})
        return Response({"detail": "No email or mobile on file for this guest"}, status=400)

    @action(detail=False, methods=["get"])
    def room_service_menu(self, request):
        """Menu for the front desk's room-service flow.

        Front Office has no POS module (segregation of duties) — this exposes
        only what ordering needs: available items, nothing about the till.
        """
        from apps.accounts.permissions import active_entitlements
        from apps.pos.models import MenuItem
        if not active_entitlements().get("restaurant"):
            return Response({"detail": "the Restaurant edition is not enabled"}, status=403)
        items = MenuItem.objects.filter(available=True).select_related("category")
        return Response([
            {"id": m.id, "name": m.name, "category": m.category.name,
             "price": str(m.price), "diet": m.diet}
            for m in items
        ])

    @action(detail=True, methods=["post"])
    def room_service(self, request, pk=None):
        """Front-desk room service (POS-lite): create the guest's F&B order,
        fire the KOT to the kitchen and post the bill straight to this folio.

        No till, no discounts, no voids — segregation of duties holds while the
        desk can still feed an in-house guest. Body: {items: [{menu_item, qty}]}.
        """
        from django.db import transaction

        from apps.accounts.models import log_action
        from apps.accounts.permissions import active_entitlements
        from apps.pos.models import Kot, MenuItem, Order, OrderLine
        from apps.recipes.services import deduct_for_newly_fired
        from .models import FolioLine

        if not active_entitlements().get("restaurant"):
            return Response({"detail": "the Restaurant edition is not enabled"}, status=403)
        folio = self.get_object()
        if folio.status != Folio.OPEN:
            return Response({"detail": "the folio is not open"}, status=400)
        wanted = request.data.get("items") or []
        if not wanted:
            return Response({"detail": "at least one item is required"}, status=400)
        menu = {m.id: m for m in MenuItem.objects.filter(
            id__in=[w.get("menu_item") for w in wanted], available=True)}
        parsed = []
        for w in wanted:
            item = menu.get(w.get("menu_item"))
            qty = int(w.get("qty") or 0)
            if not item:
                return Response({"detail": "unknown or unavailable menu item"}, status=400)
            if qty <= 0:
                return Response({"detail": "quantities must be positive"}, status=400)
            parsed.append((item, qty))

        room_no = folio.room.number if folio.room else "—"
        with transaction.atomic():
            order = Order.objects.create(
                mode=Order.ROOM,
                folio=folio,
                captain=f"Room {room_no}",           # destination shown to the kitchen
                source_platform="roomservice",
                external_ref=f"folio:{folio.id}",
            )
            lines = [OrderLine.objects.create(order=order, menu_item=item, qty=qty,
                                              unit_price=item.price)
                     for item, qty in parsed]
            # Fire the KOT: stock deducts via recipes, the round appears on the KDS.
            deduct_for_newly_fired(order, lines)
            kot = Kot.objects.create(order=order, number=f"KOT-{order.id:05d}/1")
            order.lines.update(kot_fired=True, kot=kot)
            # Post every line to the folio at menu price (no discounts at the desk).
            for line in lines:
                services.post_charge(
                    folio, kind=FolioLine.KIND_FNB,
                    description=f"Room service — {line.qty}× {line.menu_item.name}",
                    amount=line.unit_price * line.qty,
                    gst_rate=line.menu_item.gst_rate,
                    source=f"POS order {order.id}", user=request.user,
                )
            order.kot_no = kot.number
            order.status = Order.POSTED_TO_ROOM
            order.kitchen_status = "cooking"
            order.folio = folio
            order.save(update_fields=["kot_no", "status", "kitchen_status", "folio"])
        log_action(request.user, "room_service", entity="Order", entity_id=order.id,
                   after={"folio": folio.id, "kot": kot.number,
                          "items": [f"{q}× {i.name}" for i, q in parsed]})
        # Re-fetch: get_object()'s prefetched lines are stale after the writes.
        fresh = Folio.objects.prefetch_related("lines", "settlements").get(pk=folio.pk)
        return Response(FolioSerializer(fresh).data, status=201)

    @action(detail=True, methods=["get"])
    def room_service_orders(self, request, pk=None):
        """This folio's room-service orders with live kitchen status, so the
        desk can review what was sent and cancel a mistake before it's served."""
        folio = self.get_object()
        orders = (folio.pos_orders.filter(source_platform="roomservice")
                  .prefetch_related("lines__menu_item", "kots").order_by("-created_at"))
        out = []
        for o in orders:
            kot = o.kots.order_by("-created_at").first()
            kitchen = kot.status if kot else ("cancelled" if "CANCELLED" in o.discount_reason else "done")
            out.append({
                "order": o.id,
                "kot_no": o.kot_no,
                "kitchen_status": kitchen,
                "cancellable": bool(kot and kot.status in ("cooking", "ready")),
                "items": [f"{l.qty}× {l.menu_item.name}" for l in o.lines.all()],
                "total": str(o._subtotal()),
                "created_at": o.created_at,
            })
        return Response(out)

    @action(detail=True, methods=["post"])
    def room_service_delivered(self, request, pk=None):
        """Front desk confirms the tray reached the room — closes the kitchen
        ticket (clears the KDS + the 'food ready' alert) and the audit trail."""
        from django.utils import timezone

        from apps.accounts.models import log_action
        from apps.pos.models import Kot, Order

        folio = self.get_object()
        order = (Order.objects.filter(pk=request.data.get("order"), folio=folio,
                                      source_platform="roomservice")
                 .prefetch_related("kots").first())
        if not order:
            return Response({"detail": "room-service order not found on this folio"}, status=400)
        ready = order.kots.filter(status=Kot.READY)
        if not ready.exists():
            return Response({"detail": "the kitchen hasn't marked this order ready yet"}, status=400)
        ready.update(status=Kot.SERVED, served_at=timezone.now())
        order.kitchen_status = "served"
        order.save(update_fields=["kitchen_status"])
        log_action(request.user, "room_service_delivered", entity="Order", entity_id=order.id,
                   after={"folio": folio.id})
        return Response({"delivered": True})

    @action(detail=True, methods=["post"])
    def room_service_cancel(self, request, pk=None):
        """Cancel a wrongly placed room-service order before it's served.

        Reverses everything: folio lines come off the bill, consumed stock is
        returned, the KDS ticket disappears, and the order closes as cancelled.
        Served orders can't be cancelled here — that's a manager void in POS.
        """
        from django.db import transaction

        from apps.accounts.models import log_action
        from apps.inventory.models import StockMovement, apply_movement
        from apps.pos.models import Order

        folio = self.get_object()
        order = (Order.objects.filter(pk=request.data.get("order"), folio=folio,
                                      source_platform="roomservice")
                 .prefetch_related("kots").first())
        if not order:
            return Response({"detail": "room-service order not found on this folio"}, status=400)
        kot = order.kots.order_by("-created_at").first()
        if not kot or kot.status not in ("cooking", "ready"):
            return Response({"detail": "already served or cancelled — ask a manager to void it in POS"},
                            status=400)
        reason = (request.data.get("reason") or "").strip() or "wrong order"
        with transaction.atomic():
            # Return the ingredients the KOT already consumed.
            for m in StockMovement.objects.filter(kind=StockMovement.CONSUMPTION,
                                                  source=f"order:{order.id}").select_related("ingredient"):
                apply_movement(m.ingredient, StockMovement.RETURN, -m.qty,
                               reason=f"room service cancelled — {reason}",
                               source=f"order:{order.id}", user=request.user)
            # Take the charges off the guest's bill and pull the kitchen ticket.
            folio.lines.filter(source=f"POS order {order.id}").delete()
            order.kots.all().delete()
            order.status = Order.SETTLED  # closed as cancelled (same convention as void)
            order.discount_reason = f"CANCELLED by {request.user.username} — {reason}"
            order.save(update_fields=["status", "discount_reason"])
        log_action(request.user, "room_service_cancel", entity="Order", entity_id=order.id,
                   after={"folio": folio.id, "reason": reason})
        fresh = Folio.objects.prefetch_related("lines", "settlements").get(pk=folio.pk)
        return Response(FolioSerializer(fresh).data)

    @action(detail=True, methods=["post"])
    def checkout(self, request, pk=None):
        folio = self.get_object()
        # Always settle the full remaining balance with one tender — check-out may
        # post the stay's room charges first, so the client's pre-read balance is
        # stale. Tender comes from payments[0], an explicit `tender`, else Cash.
        pays = request.data.get("payments") or []
        tender = (pays[0].get("tender") if pays else None) or request.data.get("tender") or "Cash"
        try:
            services.check_out(folio, tender=tender, user=request.user)
        except ValueError as e:
            return Response({"detail": str(e)}, status=400)
        # check_out() posts new room-charge lines and a settlement — re-fetch so
        # the response isn't served from the stale pre-checkout prefetch cache.
        fresh = Folio.objects.prefetch_related("lines", "settlements").get(pk=folio.pk)
        return Response(FolioSerializer(fresh).data)


class CheckInView(ModuleViewSetMixin, viewsets.ViewSet):
    module = "checkin"

    def create(self, request):
        resv_id = request.data.get("reservation")
        room_id = request.data.get("room")
        resv = Reservation.objects.filter(pk=resv_id).first()
        if not resv:
            return Response({"detail": "reservation not found"}, status=404)
        # Re-running check-in on an in-house/closed stay would double-assign
        # rooms and re-write KYC over the live folio (QA finding TC-037).
        if resv.status != Reservation.BOOKED:
            return Response({"detail": f"reservation is already {resv.get_status_display().lower()}"},
                            status=400)
        room = Room.objects.filter(pk=room_id).first() if room_id else None
        # A stay can only go into a room at its own branch — catch a stale
        # or cross-branch room id before opening the folio against it.
        if room and resv.location_id and room.location_id and room.location_id != resv.location_id:
            return Response({"detail": "that room is at a different branch than this reservation"},
                            status=400)
        if room is None:
            sellable = Room.objects.filter(room_type=resv.room_type, status__in=Room.SELLABLE)
            if resv.location_id:
                # Prefer the stay's own branch; fall back to untagged rooms.
                room = (sellable.filter(location_id=resv.location_id).first()
                        or sellable.filter(location__isnull=True).first())
            else:
                room = shared_or_visible(sellable, request).first()
        if room is None:
            return Response({"detail": "no sellable room available"}, status=400)
        # ID proof is mandatory for check-in (KYC — BRD FR-PMS-004).
        id_type = (request.data.get("id_type") or "").strip()
        id_number = (request.data.get("id_number") or "").strip()
        if not id_type or not id_number:
            return Response({"detail": "ID proof (type and number) is required to check in."}, status=400)
        # A contact mobile is mandatory too (guest comms + folio record).
        mobile_digits = "".join(ch for ch in (request.data.get("mobile") or "") if ch.isdigit())
        if len(mobile_digits) < 7:
            return Response({"detail": "A valid mobile number is required to check in."}, status=400)
        # Registration-card evidence must be validated BEFORE check_in runs —
        # a rejected scan used to leave the guest already checked in (room
        # occupied, folio open) behind a 400 response (QA finding TC-033/034).
        id_scan = request.data.get("id_scan") or ""
        signature = request.data.get("signature") or ""
        for label, blob in (("ID scan", id_scan), ("signature", signature)):
            if blob and not blob.startswith("data:image/"):
                return Response({"detail": f"{label} must be an image"}, status=400)
            if len(blob) > 800_000:
                return Response({"detail": f"{label} is too large — retake at a smaller size"},
                                status=400)
        folio = services.check_in(resv, room, user=request.user)
        # Persist the guest's contact to the customer store for later enquiry.
        mobile = (request.data.get("mobile") or "").strip()
        if mobile:
            from apps.crm.models import Customer
            if resv.guest:
                if not resv.guest.mobile or resv.guest.mobile.startswith("erased"):
                    resv.guest.mobile = mobile
                    resv.guest.save(update_fields=["mobile"])
            else:
                guest, _ = Customer.objects.get_or_create(
                    mobile=mobile, defaults={"name": resv.guest_name})
                resv.guest = guest
                resv.save(update_fields=["guest"])
        # Capture & store KYC + guest-type from the multi-step wizard (BRD FR-PMS-004/012).
        id_type = request.data.get("id_type", "")
        id_number = request.data.get("id_number", "")
        guest_type = request.data.get("guest_type", "")
        company_name = (request.data.get("company_name") or "").strip()
        if id_type or guest_type or id_number:
            from apps.accounts.models import log_action
            folio.id_type = id_type
            folio.id_number = id_number
            folio.guest_type = guest_type
            folio.id_scan = id_scan
            folio.signature = signature
            # Company name only applies when billing to a company.
            folio.company_name = company_name if guest_type == "corporate" else ""
            if guest_type == "corporate":
                folio.routing = "city_ledger"
                folio.company = services.company_account(company_name) if company_name else None
            folio.save(update_fields=["id_type", "id_number", "guest_type", "company_name",
                                      "company", "routing", "id_scan", "signature"])
            log_action(
                request.user, "kyc_capture", entity="Folio", entity_id=folio.id,
                after={"id_type": id_type, "id_number_present": bool(id_number),
                       "id_scan_present": bool(id_scan), "signature_present": bool(signature),
                       "guest_type": guest_type, "company": folio.company_name},
                note="Check-in KYC captured",
            )
        return Response(FolioSerializer(folio).data, status=status.HTTP_201_CREATED)


class NightAuditView(ModuleViewSetMixin, viewsets.ViewSet):
    module = "accounting"

    def list(self, request):
        runs = NightAuditRun.objects.all()[:30]
        return Response(NightAuditRunSerializer(runs, many=True).data)

    def create(self, request):
        from apps.accounts.models import Property
        prop = Property.objects.first()
        biz = (prop.business_date if prop and prop.business_date else date.today())
        run = services.run_night_audit(biz, user=request.user)
        return Response(NightAuditRunSerializer(run).data, status=status.HTTP_201_CREATED)
