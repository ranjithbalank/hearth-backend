from datetime import date, timedelta

from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from apps.accounts.models import log_action
from apps.accounts.permissions import ModuleViewSetMixin
from apps.channel import services as channel_services
from apps.rooms.models import Room

from .models import RateRecommendation


class RevenueViewSet(ModuleViewSetMixin, viewsets.ViewSet):
    module = "revenue"

    def _serialize(self, r):
        return {
            "id": r.id, "room_type": r.room_type.code, "name": r.room_type.name,
            "current_rate": str(r.current_rate), "recommended_rate": str(r.recommended_rate),
            "reason": r.reason, "demand_index": r.demand_index, "status": r.status,
        }

    def list(self, request):
        recs = RateRecommendation.objects.select_related("room_type").filter(
            status=RateRecommendation.OPEN
        )
        return Response([self._serialize(r) for r in recs])

    @action(detail=True, methods=["post"])
    def accept(self, request, pk=None):
        """Accept a recommendation → push the new rate to the channel manager (seam)."""
        rec = RateRecommendation.objects.select_related("room_type").filter(pk=pk).first()
        if not rec or rec.status != RateRecommendation.OPEN:
            return Response({"detail": "recommendation not open"}, status=400)
        rec.status = RateRecommendation.ACCEPTED
        rec.save(update_fields=["status"])
        pushed = channel_services.push_rate(
            rec.room_type, rec.recommended_rate,
            detail=f"RMS: {rec.room_type.code} → {rec.recommended_rate}",
        )
        log_action(request.user, "rate_rec_accept", entity="RateRecommendation",
                   entity_id=rec.id, after={"rate": str(rec.recommended_rate), "channels": pushed})
        return Response({"accepted": True, "channels_pushed": pushed})

    @action(detail=True, methods=["post"])
    def dismiss(self, request, pk=None):
        rec = RateRecommendation.objects.filter(pk=pk).first()
        if not rec:
            return Response({"detail": "not found"}, status=404)
        rec.status = RateRecommendation.DISMISSED
        rec.save(update_fields=["status"])
        return Response({"dismissed": True})

    @action(detail=False, methods=["get"])
    def forecast(self, request):
        """A simple 14-day demand forecast derived from current occupancy."""
        rooms = Room.objects.all()
        total = rooms.count() or 1
        occ = rooms.filter(status=Room.OCCUPIED).count()
        base = round(occ / total * 100)
        out = []
        for i in range(14):
            d = date.today() + timedelta(days=i)
            # Weekends lift demand; taper across the window.
            weekend = d.weekday() >= 4
            idx = min(100, max(10, base + (25 if weekend else 0) + (i % 5) * 3))
            out.append({"date": d.isoformat(), "demand_index": idx, "weekend": weekend})
        return Response(out)
