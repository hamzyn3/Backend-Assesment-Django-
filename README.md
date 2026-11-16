# Fuel Route API

Given a start and finish inside the USA, returns the driving route, the cheapest
places to fuel up along it (500-mile max range) and the total fuel cost at 10 MPG.

Django 6.1 + Django REST Framework · OpenRouteService (routing + geocoding) · SQLite

## Setup

```bash
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env            # put your OpenRouteService key in ORS_API_KEY
python manage.py migrate
python manage.py load_fuel_stations      # cleans + geocodes fuel_prices.csv (offline, ~seconds)
python manage.py runserver
python manage.py test routing            # 9 tests, no network needed
```

## API

`POST /api/route/` (JSON). `GET /api/route/?start=..&finish=..` also works.

```json
{"start": "Los Angeles, CA", "finish": "New York, NY"}
```
`start` / `finish` accept a US place/address or `"lat,lng"`.

Response (abridged): `route` (distance, duration, GeoJSON LineString for the map),
`fuel_stops[]` (station, location, mile marker, price, gallons, cost),
`starting_tank`, `summary` (total cost / gallons / stops), `meta` (external calls, ms).

Errors are JSON `{"error": "..."}`: 400 bad/non-US input, 422 no route or no station in range,
502 routing provider unavailable.

Import `docs/Fuel_Route_API.postman_collection.json` into Postman (variable `base_url`).
Requests carry test scripts, so the Runner shows pass/fail.

## How it works

1. **Data prep (offline, once)** — `load_fuel_stations`: trim fields, drop non-US rows,
   collapse duplicate station IDs to the lowest price, geocode each city from the Census
   Gazetteer (+ cached one-time Nominatim lookups for tiny towns). 6,620 stations loaded.
2. **Routing** (`services/ors.py`) — one ORS directions call per route (+1 geocode per
   place name); everything cached for 24 h, so repeats make 0 external calls.
3. **Stations along route** (`services/stations.py`) — KD-tree on 3-D coordinates finds
   stations within 5 miles of the route in milliseconds; each gets a mile marker.
4. **Optimizer** (`services/optimizer.py`) — greedy, cost-optimal: reach a cheaper station
   with just enough fuel, otherwise fill up and go to the cheapest station in range.
5. **View** (`views.py`) — validate, orchestrate, shape the JSON.

## Assumptions / trade-offs

- Vehicle leaves with a full tank; it is valued at the first stop's price (no origin price
  data), and arrives empty — so total gallons = miles / 10.
- Duplicate station rows (same ID, several prices) -> lowest price kept.
- Stations within 5 miles of the route count as on-route; detour distance is not added.
- Contiguous USA only (fuel data has no AK/HI coverage). Ambiguous names ("Paris") should
  include a state.
- The optimum can include tiny top-ups; a minimum-purchase rule would trade a little cost
  for fewer stops.

