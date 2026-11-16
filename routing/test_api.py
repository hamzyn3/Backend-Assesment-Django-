from unittest import mock

import numpy as np
from django.test import TestCase

from routing.models import FuelStation
from routing.services import stations
from routing.services.errors import LocationNotInUSA
from routing.services.ors import Location, Route

URL = "/api/route/"


def fake_route():
    """Straight west->east line along lat 40 (~1,000 miles)."""
    lon = np.linspace(-110, -88, 400)
    coords = np.column_stack([np.full_like(lon, 40.0), lon])
    miles = np.linspace(0, 1000, 400)
    return (Location(40, -110, "A"), Location(40, -88, "B"),
            Route(coords, miles, 1000.0, 36000.0))


class FuelRouteApiTests(TestCase):
    def setUp(self):
        stations.clear_cache()
        for i, lon in enumerate([-107, -103, -99, -95, -91]):
            FuelStation.objects.create(
                opis_id=i + 1, name=f"S{i}", address="x", city="c", state="NE",
                price=3.0 + i * 0.1, latitude=40.01, longitude=lon)
        FuelStation.objects.create(  # far from the route: must be ignored
            opis_id=99, name="FAR", address="x", city="c", state="NE",
            price=0.5, latitude=45, longitude=-100)

    @mock.patch("routing.views.ors.plan_route", side_effect=lambda s, f: fake_route())
    def test_success_shape_and_totals(self, _):
        r = self.client.post(URL, {"start": "a", "finish": "b"}, content_type="application/json")
        self.assertEqual(r.status_code, 200)
        d = r.json()
        self.assertEqual(d["route"]["geometry"]["type"], "LineString")
        self.assertAlmostEqual(d["summary"]["total_gallons"], 100.0, places=1)
        self.assertNotIn("FAR", [s["name"] for s in d["fuel_stops"]])
        self.assertTrue(all(s["price_per_gallon"] > 0 for s in d["fuel_stops"]))

    def test_missing_field_is_400(self):
        r = self.client.post(URL, {"start": "a"}, content_type="application/json")
        self.assertEqual(r.status_code, 400)

    @mock.patch("routing.views.ors.plan_route", side_effect=LocationNotInUSA("nope"))
    def test_non_us_location_is_400(self, _):
        r = self.client.post(URL, {"start": "a", "finish": "b"}, content_type="application/json")
        self.assertEqual((r.status_code, r.json()["error"]), (400, "nope"))
