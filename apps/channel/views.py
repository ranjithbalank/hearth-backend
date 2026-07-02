from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from apps.accounts.permissions import ModuleViewSetMixin
from apps.rooms.models import RoomType

from . import services
from .models import Channel, ChannelPush, ChannelRate


class ChannelViewSet(ModuleViewSetMixin, viewsets.ViewSet):
    module = "channel"

    def list(self, request):
        chans = [
            {
                "id": c.id, "name": c.name, "connected": c.connected,
                "commission_pct": str(c.commission_pct),
            }
            for c in Channel.objects.all()
        ]
        return Response(chans)

    @action(detail=False, methods=["get"])
    def ari(self, request):
        """ARI grid: room types × channels, plus parity breach flags."""
        channels = list(Channel.objects.filter(connected=True))
        breaches = set(services.parity_breaches())
        grid = []
        for rt in RoomType.objects.all():
            cells = []
            for ch in channels:
                cr = ChannelRate.objects.filter(channel=ch, room_type=rt).first()
                cells.append({
                    "channel": ch.name,
                    "rate": str(cr.rate) if cr else None,
                    "availability": cr.availability if cr else 0,
                })
            grid.append({
                "room_type": rt.code, "name": rt.name,
                "cells": cells, "parity_breach": rt.code in breaches,
            })
        return Response({
            "channels": [c.name for c in channels],
            "grid": grid,
            "parity_ok": not breaches,
        })

    @action(detail=False, methods=["post"])
    def fix_parity(self, request):
        fixed = services.fix_parity()
        return Response({"fixed": fixed})

    @action(detail=False, methods=["post"])
    def ingest(self, request):
        """Inbound webhook seam: accept an OTA (e.g. Booking.com) reservation and
        create it in the PMS. A real channel-manager connector would post here."""
        from apps.accounts.models import log_action
        from apps.reservations.serializers import ReservationSerializer
        try:
            resv, created = services.ingest_booking(request.data, user=request.user)
        except services.IngestError as e:
            return Response({"detail": str(e)}, status=400)
        if created:
            log_action(request.user, "ota_ingest", entity="Reservation", entity_id=resv.id,
                       after={"channel": resv.channel_name, "ref": resv.ota_ref})
        return Response({"created": created, "reservation": ReservationSerializer(resv).data},
                        status=(201 if created else 200))

    @action(detail=False, methods=["get"])
    def pushes(self, request):
        rows = [
            {"id": p.id, "kind": p.kind, "detail": p.detail,
             "status": p.status, "created_at": p.created_at}
            for p in ChannelPush.objects.all()[:30]
        ]
        return Response(rows)
