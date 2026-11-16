from django.test import SimpleTestCase

from routing.services.optimizer import NoFeasibleStops, plan_fuel_stops


def st(mile, price):
    return {"mile": mile, "price": price}


class OptimizerTests(SimpleTestCase):
    def test_short_trip_needs_no_stop(self):
        r = plan_fuel_stops([st(100, 3.0)], 300, fallback_price=3.0)
        self.assertEqual(r["stops"], [])
        self.assertAlmostEqual(r["total_cost"], 30 * 3.0)

    def test_gallons_always_equal_distance_over_mpg(self):
        stations = [st(m, 3 + (m % 7) / 10) for m in range(50, 1500, 60)]
        r = plan_fuel_stops(stations, 1400)
        self.assertAlmostEqual(r["total_gallons"], 140)

    def test_no_leg_exceeds_range(self):
        stations = [st(m, 3 + (m % 11) / 10) for m in range(40, 2000, 40)]
        r = plan_fuel_stops(stations, 1900)
        marks = [0] + [s["mile"] for s in r["stops"]] + [1900]
        self.assertTrue(all(b - a <= 500 for a, b in zip(marks, marks[1:])))

    def test_prefers_cheap_station_over_nearer_expensive_one(self):
        # A (mile 100, $4) vs B (mile 480, $3): the whole refuel happens at B
        r = plan_fuel_stops([st(100, 4.0), st(480, 3.0)], 900)
        self.assertEqual([s["mile"] for s in r["stops"]], [480])
        self.assertAlmostEqual(r["stops"][0]["gallons"], 40.0)  # 400 miles needed

    def test_buys_only_enough_to_reach_cheaper_station(self):
        # A (mile 450, $3.5) then cheaper B (mile 600, $3.0): at A buy just the 100
        # miles (10 gal) needed to reach B, and fill the rest at B.
        r = plan_fuel_stops([st(450, 3.5), st(600, 3.0)], 1000)
        a, b = r["stops"]
        self.assertAlmostEqual(a["gallons"], 10.0)
        self.assertAlmostEqual(b["gallons"], 40.0)

    def test_gap_larger_than_range_raises(self):
        with self.assertRaises(NoFeasibleStops):
            plan_fuel_stops([st(100, 3.0), st(800, 3.0)], 1200)
