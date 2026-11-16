"""Cost-optimal fuel stops for a trip with a limited tank range.

Pure function (no Django / HTTP) so it is easy to read and unit-test.

Greedy rule, optimal for the classic "gas station" problem:
  at each stop, look at every station reachable on a full tank:
    * if the destination is reachable           -> buy only what's needed, done
    * elif a CHEAPER station is reachable       -> buy just enough to reach the
                                                   nearest cheaper one
    * else (current station is the cheapest)    -> fill the tank, then drive to the
                                                   cheapest station in range
  Fuel is only ever bought at a price no higher than what comes next.

Assumption: the vehicle leaves the origin with a full tank. We have no price
data for the origin, so that starting tank is valued at the price of the first
stop (or `fallback_price` when no stop is needed). The vehicle arrives with an
empty tank, so total gallons == total_miles / mpg and the total cost covers the
whole trip.
"""
from .errors import RoutingError


class NoFeasibleStops(RoutingError):
    status = 422


def plan_fuel_stops(stations, total_miles, range_miles=500.0, mpg=10.0,
                    fallback_price=None):
    """stations: dicts with 'mile' and 'price', sorted by mile.

    Returns {"stops", "start_tank", "total_gallons", "total_cost"}. Each stop is
    the station dict plus gallons / cost / fuel_before_miles (fuel is tracked as
    miles of range).
    """
    stops = []
    pos, fuel = 0.0, range_miles     # leave the origin with a full tank
    cur, price_here = -1, 0.0        # -1 = still at the origin

    while True:
        remaining = total_miles - pos
        if remaining <= range_miles:             # destination fits in one tank
            if stops:                            # top up just enough to arrive
                _buy(stops[-1], max(0.0, remaining - fuel), mpg)
            break

        # stations ahead that a full tank can reach
        ahead = [(j, s) for j, s in enumerate(stations)
                 if j > cur and pos < s["mile"] <= pos + range_miles]
        if not ahead:
            raise NoFeasibleStops(
                f"No fuel station within {range_miles:.0f} miles after mile {pos:.0f}.")

        cheaper = next(((j, s) for j, s in ahead if s["price"] < price_here), None)
        if cur == -1 or not cheaper:
            # origin tank is free, or this is the cheapest around: head for the
            # cheapest station in range (ties -> furthest), filling up here first
            j, s = min(ahead, key=lambda t: (t[1]["price"], -t[1]["mile"]))
            buy = 0.0 if cur == -1 else range_miles - fuel
        else:
            j, s = cheaper                       # buy only enough to reach it
            buy = max(0.0, (s["mile"] - pos) - fuel)

        if stops:
            _buy(stops[-1], buy, mpg)
        fuel += buy - (s["mile"] - pos)
        pos, cur, price_here = s["mile"], j, s["price"]
        stops.append(_open_stop(s, fuel))

    # value the starting tank (what we left the origin with and burned before stop 1)
    start_miles = min(range_miles, total_miles)
    start_price = stops[0]["price"] if stops else fallback_price
    if start_price is None:
        raise NoFeasibleStops("No fuel price data available near this route.")
    start_gal = start_miles / mpg
    start_tank = {"gallons": start_gal, "price": start_price, "cost": start_gal * start_price}
    total_cost = start_tank["cost"] + sum(s["cost"] for s in stops)
    total_gallons = start_gal + sum(s["gallons"] for s in stops)
    return {"stops": stops, "start_tank": start_tank,
            "total_gallons": total_gallons, "total_cost": total_cost}


def _open_stop(station, fuel_before):
    s = dict(station)
    s.update(fuel_before_miles=fuel_before, gallons=0.0, cost=0.0)
    return s


def _buy(stop, miles_of_fuel, mpg):
    gallons = miles_of_fuel / mpg
    stop["gallons"] += gallons
    stop["cost"] += gallons * stop["price"]
