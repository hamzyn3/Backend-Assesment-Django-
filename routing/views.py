import time

import numpy as np
from django.conf import settings
from rest_framework.response import Response
from rest_framework.views import APIView

from .serializers import RouteRequestSerializer
from .services import ors, stations
from .services.errors import RoutingError
from .services.optimizer import plan_fuel_stops


def _thin(route):
    """Drop points closer than ROUTE_GEOMETRY_STEP_MILES to keep the response small."""
    step = settings.ROUTE_GEOMETRY_STEP_MILES
    cum = route.cum_miles
    keep = np.unique(np.concatenate([
        [0], np.searchsorted(cum, np.arange(step, cum[-1], step)), [len(cum) - 1]]))
    return [[round(float(lon), 5), round(float(lat), 5)]
            for lat, lon in route.coords[keep]]


def _place(loc):
    return {"label": loc.label, "lat": round(loc.lat, 5), "lon": round(loc.lon, 5)}


class FuelRouteView(APIView):
    """POST {"start": "...", "finish": "..."} -> route + cost-optimal fuel stops."""

    def get(self, request):          # convenience: ?start=...&finish=...
        return self._handle(request.query_params)

    def post(self, request):
        return self._handle(request.data)

    def _handle(self, data):
        t0 = time.perf_counter()
        ors.external_calls.set(0)
        ser = RouteRequestSerializer(data=data)
        ser.is_valid(raise_exception=True)
        try:
            start, end, route = ors.plan_route(ser.validated_data["start"],
                                               ser.validated_data["finish"])
            candidates = stations.stations_along_route(route)
            plan = plan_fuel_stops(
                candidates, route.distance_miles,
                range_miles=settings.VEHICLE_RANGE_MILES, mpg=settings.VEHICLE_MPG,
                fallback_price=candidates[0]["price"] if candidates else None)
        except RoutingError as e:
            return Response({"error": str(e)}, status=e.status)

        stops = [{
            "order": i,
            "name": s["name"], "address": s["address"], "city": s["city"], "state": s["state"],
            "lat": round(s["lat"], 5), "lon": round(s["lon"], 5),
            "mile_marker": round(s["mile"], 1),
            "off_route_miles": round(s["off_route_miles"], 2),
            "price_per_gallon": round(s["price"], 3),
            "gallons_purchased": round(s["gallons"], 2),
            "cost": round(s["cost"], 2),
        } for i, s in enumerate(plan["stops"], 1)]
        tank = plan["start_tank"]

        return Response({
            "start": _place(start),
            "finish": _place(end),
            "route": {
                "distance_miles": round(route.distance_miles, 1),
                "duration_hours": round(route.duration_seconds / 3600, 2),
                "geometry": {"type": "LineString", "coordinates": _thin(route)},
            },
            "fuel_stops": stops,
            "starting_tank": {
                "note": "Full tank at departure, valued at the first stop's price.",
                "gallons": round(tank["gallons"], 2),
                "price_per_gallon": round(tank["price"], 3),
                "cost": round(tank["cost"], 2),
            },
            "summary": {
                "total_fuel_cost_usd": round(plan["total_cost"], 2),
                "total_gallons": round(plan["total_gallons"], 2),
                "number_of_fuel_stops": len(stops),
                "assumptions": {"mpg": settings.VEHICLE_MPG,
                                "max_range_miles": settings.VEHICLE_RANGE_MILES},
            },
            "meta": {
                "external_api_calls": ors.external_calls.get(),
                "candidate_stations_considered": len(candidates),
                "processing_ms": round((time.perf_counter() - t0) * 1000),
            },
        })
