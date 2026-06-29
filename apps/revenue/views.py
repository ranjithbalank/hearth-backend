from datetime import date, timedelta

from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from apps.accounts.models import log_action
from apps.accounts.permissions import ModuleViewSetMixin
from apps.channel import services as channel_services
from apps.rooms.models import Room

from .models import RateRecommendation, RateRestriction


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

    @action(detail=False, methods=["get", "post"])
    def restrictions(self, request):
        """List or set rate restrictions; setting one pushes it to the channel manager."""
        from apps.channel.models import ChannelPush
        if request.method == "POST":
            code = request.data.get("room_type")
            rt = RoomType.objects.filter(code=code).first()
            if not rt:
                return Response({"detail": "room_type not found"}, status=400)
            r, _ = RateRestriction.objects.get_or_create(room_type=rt)
            for f in ["min_los", "cta", "ctd", "stop_sell"]:
                if f in request.data:
                    setattr(r, f, request.data[f])
            r.save()
            ChannelPush.objects.create(
                kind=ChannelPush.KIND_RMS,
                detail=f"Restrictions {rt.code}: MLOS {r.min_los}"
                       + (", CTA" if r.cta else "") + (", CTD" if r.ctd else "")
                       + (", STOP-SELL" if r.stop_sell else ""),
            )
            log_action(request.user, "rate_restriction", entity="RateRestriction",
                       entity_id=r.id, after={"min_los": r.min_los, "stop_sell": r.stop_sell})
        rows = [
            {"room_type": x.room_type.code, "name": x.room_type.name, "min_los": x.min_los,
             "cta": x.cta, "ctd": x.ctd, "stop_sell": x.stop_sell}
            for x in RateRestriction.objects.select_related("room_type")
        ]
        return Response(rows)

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
