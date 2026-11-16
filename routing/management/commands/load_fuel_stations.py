"""Clean the fuel price CSV, geocode stations offline and load them into the DB.

Geocoding uses the US Census Gazetteer files (data/gaz_places.txt and
data/gaz_cousubs.txt), matched on (state, normalised city name), so no external
API is called.

Cleaning rules:
  * whitespace stripped from every field
  * non-US rows (Canadian provinces) dropped
  * duplicate OPIS IDs (same station listed at several prices) collapsed to
    the lowest price
  * rows whose city cannot be located are skipped and reported
"""
import csv
import json
import re
import time
from collections import defaultdict
from decimal import Decimal, InvalidOperation
from pathlib import Path

import requests
from django.conf import settings
from django.core.management.base import BaseCommand

from routing.models import FuelStation

US_STATES = set(
    "AL AK AZ AR CA CO CT DE DC FL GA HI ID IL IN IA KS KY LA ME MD MA MI MN MS MO "
    "MT NE NV NH NJ NM NY NC ND OH OK OR PA RI SC SD TN TX UT VT VA WA WV WI WY".split()
)

SUFFIXES = (
    "city and borough", "census designated place", "consolidated government",
    "metropolitan government", "unified government", "urban county", "municipality",
    "charter township", "borough", "village", "town", "city", "cdp", "township", "twp", "plantation",
    "comunidad", "zona urbana", "county",
)
_suffix_re = re.compile(r"\s+(?:%s)(?:\s*\(.*\))?$" % "|".join(SUFFIXES))


def normalize(name: str) -> str:
    n = name.strip().lower()
    n = re.sub(r"\s+", " ", n)
    for _ in range(2):
        n = _suffix_re.sub("", n)
    n = re.sub(r"\bsaint\b", "st", n)
    n = re.sub(r"\bmount\b", "mt", n)
    n = re.sub(r"\bfort\b", "ft", n)
    n = re.sub(r"[.'`]", "", n)
    n = re.sub(r"[-/]", " ", n)
    return n.strip()


def load_gazetteer(path: Path, index: dict):
    with open(path, encoding="utf-8", newline="") as f:
        reader = csv.reader(f, delimiter="\t")
        header = [h.strip() for h in next(reader)]
        i_st, i_nm = header.index("USPS"), header.index("NAME")
        i_la, i_lo = header.index("INTPTLAT"), header.index("INTPTLONG")
        for row in reader:
            try:
                lat, lon = float(row[i_la]), float(row[i_lo])
            except ValueError:
                continue
            key = normalize(row[i_nm])
            index[(row[i_st].strip(), key)].append((lat, lon))
            # space-insensitive key ("Mc Calla" == "McCalla")
            index[(row[i_st].strip(), key.replace(" ", ""))].append((lat, lon))


class Command(BaseCommand):
    help = "Load, clean and geocode fuel stations from the CSV into the database."

    def add_arguments(self, parser):
        parser.add_argument("--csv", default=str(settings.BASE_DIR / "fuel_prices.csv"))
        parser.add_argument(
            "--online-fallback", action="store_true",
            help="One-time Nominatim lookup (1 req/s, cached in data/geocode_cache.json) "
                 "for cities missing from the Census gazetteer.")

    def _nominatim(self, cities, cache_path):
        cache = json.loads(cache_path.read_text()) if cache_path.exists() else {}
        todo = [c for c in cities if f"{c[0]}|{c[1]}" not in cache]
        self.stdout.write(f"online fallback: {len(todo)} cities to look up")
        for n, (city, state) in enumerate(todo, 1):
            try:
                r = requests.get(
                    "https://nominatim.openstreetmap.org/search",
                    params={"city": city, "state": state, "country": "US",
                            "format": "json", "limit": 1},
                    headers={"User-Agent": "fuel-route-assessment/1.0"}, timeout=15)
                res = r.json() if r.ok else []
                cache[f"{city}|{state}"] = (
                    [float(res[0]["lat"]), float(res[0]["lon"])] if res else None)
            except (requests.RequestException, ValueError, KeyError):
                continue  # not cached -> retried on the next run
            if n % 25 == 0:
                cache_path.write_text(json.dumps(cache))
            time.sleep(1.1)
        cache_path.write_text(json.dumps(cache))
        return cache

    def handle(self, *args, **opts):
        data_dir = settings.BASE_DIR / "data"
        index = defaultdict(list)
        # places take priority over county subdivisions: insert them first
        load_gazetteer(data_dir / "gaz_places.txt", index)
        load_gazetteer(data_dir / "gaz_cousubs.txt", index)

        stations, non_us, bad = {}, 0, 0
        total = 0
        with open(opts["csv"], encoding="utf-8-sig", newline="") as f:
            for row in csv.DictReader(f):
                total += 1
                row = {k.strip(): (v or "").strip() for k, v in row.items()}
                state = row["State"].upper()
                if state not in US_STATES:
                    non_us += 1
                    continue
                try:
                    price = Decimal(row["Retail Price"])
                    oid = int(row["OPIS Truckstop ID"])
                except (InvalidOperation, ValueError):
                    bad += 1
                    continue
                cur = stations.get(oid)
                if cur is None or price < cur["price"]:
                    stations[oid] = {
                        "opis_id": oid, "name": row["Truckstop Name"],
                        "address": row["Address"], "city": row["City"],
                        "state": state, "price": price,
                        "rack_id": int(row["Rack ID"]) if row["Rack ID"].isdigit() else None,
                    }

        objs, unmatched = [], []
        for s in stations.values():
            key = normalize(s["city"])
            hits = index.get((s["state"], key)) or index.get((s["state"], key.replace(" ", "")))
            if not hits:
                unmatched.append(s)
                continue
            # first entry = place file match when one exists
            s["latitude"], s["longitude"] = hits[0]
            objs.append(FuelStation(**s))

        if opts["online_fallback"] and unmatched:
            cache = self._nominatim(
                sorted({(u["city"], u["state"]) for u in unmatched}),
                data_dir / "geocode_cache.json")
        else:  # reuse any previously cached lookups
            cp = data_dir / "geocode_cache.json"
            cache = json.loads(cp.read_text()) if cp.exists() else {}
        # guard: fallback hits far from the state's other stations are wrong homonyms
        by_state = defaultdict(list)
        for o in objs:
            by_state[o.state].append((o.latitude, o.longitude))
        centre = {st: (sorted(a for a, _ in v)[len(v) // 2], sorted(b for _, b in v)[len(v) // 2])
                  for st, v in by_state.items()}

        def plausible(state, hit):
            c = centre.get(state)
            return c is None or (abs(hit[0] - c[0]) <= 8 and abs(hit[1] - c[1]) <= 12)

        still = []
        for s in unmatched:
            hit = cache.get(f"{s['city']}|{s['state']}")
            if hit and plausible(s["state"], hit):
                s["latitude"], s["longitude"] = hit
                objs.append(FuelStation(**s))
            else:
                still.append(s)
        unmatched = still

        FuelStation.objects.all().delete()
        FuelStation.objects.bulk_create(objs, batch_size=1000)

        self.stdout.write(self.style.SUCCESS(
            f"rows read: {total} | non-US dropped: {non_us} | malformed: {bad} | "
            f"unique US stations: {len(stations)} | loaded: {len(objs)} | "
            f"unmatched city: {len(unmatched)}"))
        for s in unmatched[:40]:
            self.stdout.write(f"  unmatched: {s['city']!r}, {s['state']} ({s['name']})")
