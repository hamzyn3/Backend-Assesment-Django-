"""Find fuel stations that lie along a route.

Stations are loaded once into numpy arrays. Both the route points and the
stations are converted to 3-D unit-sphere coordinates, so a KD-tree lookup of
"nearest route point" is exact great-circle distance with no per-station loop.
Each candidate gets a mile marker = cumulative route miles at its nearest point.
"""
from functools import lru_cache

import numpy as np
from django.conf import settings
from scipy.spatial import cKDTree

from routing.models import FuelStation

EARTH_MI = 3958.7613


def _xyz(lat, lon):
    lat, lon = np.radians(lat), np.radians(lon)
    return np.column_stack([np.cos(lat) * np.cos(lon), np.cos(lat) * np.sin(lon), np.sin(lat)])


@lru_cache(maxsize=1)
def _load():
    rows = list(FuelStation.objects.values_list(
        "id", "name", "address", "city", "state", "price", "latitude", "longitude"))
    lat = np.array([r[6] for r in rows])
    lon = np.array([r[7] for r in rows])
    return rows, lat, lon, _xyz(lat, lon)


def clear_cache():
    _load.cache_clear()


def stations_along_route(route, corridor_miles=None):
    """Return candidate stations sorted by mile marker."""
    corridor = corridor_miles or settings.CORRIDOR_MILES
    rows, lat, lon, xyz = _load()
    if not rows:
        return []

    # 1) cheap bounding-box prefilter (corridor ~ 1 degree latitude per 69 miles)
    pad = corridor / 69 + 0.1
    la, lo = route.coords[:, 0], route.coords[:, 1]
    box = ((lat >= la.min() - pad) & (lat <= la.max() + pad)
           & (lon >= lo.min() - pad * 1.5) & (lon <= lo.max() + pad * 1.5))
    idx = np.flatnonzero(box)
    if idx.size == 0:
        return []

    # 2) nearest route point for every remaining station (chord -> miles)
    tree = cKDTree(_xyz(la, lo))
    chord, nearest = tree.query(xyz[idx])
    miles_off = 2 * EARTH_MI * np.arcsin(np.minimum(chord / 2, 1.0))
    keep = miles_off <= corridor

    out = []
    for i, n, off in zip(idx[keep], nearest[keep], miles_off[keep]):
        r = rows[i]
        out.append({
            "id": r[0], "name": r[1], "address": r[2], "city": r[3], "state": r[4],
            "price": float(r[5]), "lat": r[6], "lon": r[7],
            "mile": float(route.cum_miles[n]), "off_route_miles": float(off),
        })
    out.sort(key=lambda s: s["mile"])
    return out
