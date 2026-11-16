"""OpenRouteService client: geocoding and directions.

External-call budget per request:
  * directions: exactly 1 call (cached afterwards)
  * geocoding:  1 call per *text* location (cached); 0 when the caller sends
    "lat,lng" coordinates. So coordinates -> 1 external call total,
    place names -> 3 on a cold cache, 0 on a warm one.
"""
import hashlib
import re
from contextvars import ContextVar
from dataclasses import dataclass

import numpy as np
import requests
from django.conf import settings
from django.core.cache import cache

from .errors import InvalidLocation, LocationNotInUSA, RouteNotFound, UpstreamError

# Rough bounding box of the contiguous USA (the fuel data covers only these).
US_LAT = (24.3, 49.6)
US_LON = (-125.1, -66.8)

_COORD_RE = re.compile(r"^\s*(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)\s*$")
_session = requests.Session()

# Counts external HTTP calls made while serving the current request (shown in API `meta`).
external_calls: ContextVar[int] = ContextVar("external_calls", default=0)


def _http(method, url, **kw):
    external_calls.set(external_calls.get() + 1)
    return _session.request(method, url, **kw)


@dataclass(frozen=True)
class Location:
    lat: float
    lon: float
    label: str


@dataclass
class Route:
    coords: np.ndarray        # (N, 2) array of [lat, lon]
    cum_miles: np.ndarray     # (N,) cumulative miles along the route
    distance_miles: float
    duration_seconds: float


def _in_usa(lat, lon):
    return US_LAT[0] <= lat <= US_LAT[1] and US_LON[0] <= lon <= US_LON[1]


def _headers():
    if not settings.ORS_API_KEY:
        raise UpstreamError("ORS_API_KEY is not configured.")
    return {"Authorization": settings.ORS_API_KEY}


def _key(prefix, value):
    return prefix + hashlib.md5(value.encode()).hexdigest()


def geocode(text: str) -> Location:
    """Resolve 'lat,lng' or a free-text US place into a Location."""
    text = (text or "").strip()
    if not text:
        raise InvalidLocation("Location must not be empty.")

    m = _COORD_RE.match(text)
    if m:
        lat, lon = float(m.group(1)), float(m.group(2))
        if not _in_usa(lat, lon):
            raise LocationNotInUSA(f"Coordinates '{text}' are outside the USA.")
        return Location(lat, lon, f"{lat:.5f},{lon:.5f}")

    ck = _key("geo:", text.lower())
    hit = cache.get(ck)
    if hit:
        return hit
    try:
        r = _http(
            "GET", f"{settings.ORS_BASE_URL}/geocode/search", headers=_headers(),
            params={"text": text, "size": 1},  # unrestricted so non-US places are rejected, not mis-matched
            timeout=settings.ORS_TIMEOUT)
    except requests.RequestException as e:
        raise UpstreamError(f"Geocoding service unreachable: {e}")
    if r.status_code != 200:
        raise UpstreamError(f"Geocoding failed ({r.status_code}).")
    feats = r.json().get("features") or []
    if not feats:
        raise InvalidLocation(f"Could not find '{text}' in the USA.")
    f = feats[0]
    lon, lat = f["geometry"]["coordinates"]
    if f["properties"].get("country_a") != "USA" or not _in_usa(lat, lon):
        where = f.get("properties", {}).get("label", "outside the USA")
        raise LocationNotInUSA(
            f"'{text}' resolved to '{where}', which is not in the contiguous USA. "
            "Add a state (e.g. 'Paris, TX') for ambiguous names.")
    loc = Location(lat, lon, f["properties"].get("label", text))
    cache.set(ck, loc, settings.ROUTE_CACHE_TTL)
    return loc


def haversine_miles(lat1, lon1, lat2, lon2):
    """Vectorised great-circle distance in miles."""
    lat1, lon1, lat2, lon2 = map(np.radians, (lat1, lon1, lat2, lon2))
    a = (np.sin((lat2 - lat1) / 2) ** 2
         + np.cos(lat1) * np.cos(lat2) * np.sin((lon2 - lon1) / 2) ** 2)
    return 3958.7613 * 2 * np.arcsin(np.sqrt(a))


def get_route(start: Location, end: Location) -> Route:
    """ONE directions call to ORS; result cached by endpoint coordinates."""
    ck = _key("route:", f"{start.lat:.4f},{start.lon:.4f}>{end.lat:.4f},{end.lon:.4f}")
    hit = cache.get(ck)
    if hit:
        return hit
    try:
        r = _http(
            "POST", f"{settings.ORS_BASE_URL}/v2/directions/driving-car/geojson",
            headers=_headers(),
            json={"coordinates": [[start.lon, start.lat], [end.lon, end.lat]],
                  "radiuses": [5000, 5000]},  # snap points up to 5 km to a road
            timeout=settings.ORS_TIMEOUT)
    except requests.RequestException as e:
        raise UpstreamError(f"Routing service unreachable: {e}")
    if r.status_code == 404 or (r.status_code == 400 and "find routable point" in r.text):
        raise RouteNotFound("No drivable route found between these locations.")
    if r.status_code != 200:
        raise UpstreamError(f"Routing failed ({r.status_code}).")

    feat = r.json()["features"][0]
    pts = np.asarray(feat["geometry"]["coordinates"], dtype=float)[:, :2]  # [lon, lat]
    coords = pts[:, ::-1].copy()                                            # [lat, lon]
    seg = haversine_miles(coords[:-1, 0], coords[:-1, 1], coords[1:, 0], coords[1:, 1])
    cum = np.concatenate([[0.0], np.cumsum(seg)])
    summary = feat["properties"]["summary"]
    route = Route(coords, cum, summary["distance"] / 1609.344, summary["duration"])
    # scale haversine mileage so it matches the routing engine's own distance
    if cum[-1] > 0:
        route.cum_miles = cum * (route.distance_miles / cum[-1])
    cache.set(ck, route, settings.ROUTE_CACHE_TTL)
    return route


def plan_route(start_text: str, finish_text: str):
    start, end = geocode(start_text), geocode(finish_text)
    if (start.lat, start.lon) == (end.lat, end.lon):
        raise InvalidLocation("Start and finish are the same location.")
    return start, end, get_route(start, end)
