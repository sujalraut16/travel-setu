"""
flight_service.py  -  the ONLY file in Travel Setu that talks to flight APIs.

Your routes/templates call:   search_flights("PNQ", "DEL", "2026-10-20")
and always get a list of dicts in the SAME format, whichever provider is active.

Switch provider with an environment variable (.env):
    FLIGHT_PROVIDER=aerodatabox     # now (RapidAPI free plan)
    FLIGHT_PROVIDER=duffel          # later, when Duffel approves you
    FLIGHT_PROVIDER=demo            # offline demo data, no API calls
    FLIGHT_CACHE_MINUTES=10         # how often live data is refreshed

Other .env values:
    RAPIDAPI_KEY=your_rapidapi_key
    DUFFEL_TOKEN=duffel_test_xxx

Setup:  pip install requests python-dotenv
In app.py (top):  from dotenv import load_dotenv; load_dotenv()

NOTE: AeroDataBox gives schedules + status, NOT prices or seats.
So `price` and `seats_available` come back as None for it. Fill them from
YOUR OWN database (package/seat tables) in your route. Duffel does return prices.

Field names of the third-party responses were written from API docs, not tested
against live responses here. Print one real response and adjust the .get() paths
in the _normalize_* functions if anything comes back empty.
"""
import json
import os
import sqlite3
import time
from datetime import datetime, timedelta

import requests

PROVIDER = os.getenv("FLIGHT_PROVIDER", "demo").lower()
CACHE_TTL_SECONDS = int(os.getenv("FLIGHT_CACHE_MINUTES", "10")) * 60  # how long data counts as "fresh"
CACHE_DB = os.path.join(os.path.dirname(__file__), "flight_cache.db")
TIMEOUT = 15


# --------------------------------------------------------------------------
# Tiny SQLite cache (separate file, no changes to your SQLAlchemy models)
# --------------------------------------------------------------------------
def _cache_conn():
    conn = sqlite3.connect(CACHE_DB)
    conn.execute(
        "CREATE TABLE IF NOT EXISTS cache (k TEXT PRIMARY KEY, v TEXT, ts REAL)"
    )
    return conn


def _cache_get(key):
    """Returns (data, saved_timestamp) or (None, None)."""
    conn = _cache_conn()
    row = conn.execute("SELECT v, ts FROM cache WHERE k=?", (key,)).fetchone()
    conn.close()
    if row:
        return json.loads(row[0]), row[1]
    return None, None


def _cache_set(key, value):
    conn = _cache_conn()
    conn.execute(
        "INSERT OR REPLACE INTO cache (k, v, ts) VALUES (?, ?, ?)",
        (key, json.dumps(value), time.time()),
    )
    conn.commit()
    conn.close()


# --------------------------------------------------------------------------
# Common output format (your templates only ever see this)
# --------------------------------------------------------------------------
def _flight(flight_number, airline, origin, destination,
            departure_time, arrival_time, status=None,
            price=None, currency=None, seats_available=None, source=""):
    return {
        "flight_number": flight_number,
        "airline": airline,
        "origin": origin,
        "destination": destination,
        "departure_time": departure_time,   # "YYYY-MM-DD HH:MM" local time
        "arrival_time": arrival_time,
        "status": status,
        "price": price,
        "currency": currency,
        "seats_available": seats_available,  # fill from your own DB
        "source": source,
    }


# --------------------------------------------------------------------------
# Provider 1: AeroDataBox via RapidAPI
# --------------------------------------------------------------------------
def _aerodatabox_search(origin, destination, date):
    key = os.getenv("RAPIDAPI_KEY")
    if not key:
        raise RuntimeError("RAPIDAPI_KEY missing in .env")

    headers = {
        "x-rapidapi-key": key,
        "x-rapidapi-host": "aerodatabox.p.rapidapi.com",
    }
    results = []
    # Free plan allows max 12 hours per call -> two calls cover the whole day.
    # Each call costs units from your monthly quota, so results are cached.
    for start, end in (("00:00", "11:59"), ("12:00", "23:59")):
        url = (
            f"https://aerodatabox.p.rapidapi.com/flights/airports/iata/"
            f"{origin}/{date}T{start}/{date}T{end}"
        )
        params = {"direction": "Departure", "withCancelled": "false",
                  "withCodeshared": "false", "withPrivate": "false"}
        resp = requests.get(url, headers=headers, params=params, timeout=TIMEOUT)
        resp.raise_for_status()
        results.extend(_normalize_aerodatabox(resp.json(), origin, destination))
    return results


def _local(t):
    """AeroDataBox time block -> 'YYYY-MM-DD HH:MM'."""
    if not t:
        return None
    return (t.get("local") or "")[:16].replace("T", " ") or None


UNAVAILABLE_STATUSES = {"Departed", "EnRoute", "Approaching", "Arrived",
                        "Canceled", "CanceledUncertain", "Diverted"}


def _normalize_aerodatabox(data, origin, destination):
    out = []
    for f in data.get("departures", []):
        if f.get("status") in UNAVAILABLE_STATUSES:
            continue  # already left / cancelled: not bookable
        arr_airport = (f.get("arrival", {}).get("airport") or {})
        if arr_airport.get("iata", "").upper() != destination.upper():
            continue  # keep only flights going to the requested destination
        out.append(_flight(
            flight_number=f.get("number"),
            airline=(f.get("airline") or {}).get("name"),
            origin=origin.upper(),
            destination=destination.upper(),
            departure_time=_local(f.get("departure", {}).get("scheduledTime")),
            arrival_time=_local(f.get("arrival", {}).get("scheduledTime")),
            status=f.get("status"),
            source="aerodatabox",
        ))
    return out


# --------------------------------------------------------------------------
# Provider 2: Duffel (use after approval; gives real prices)
# --------------------------------------------------------------------------
def _duffel_search(origin, destination, date):
    token = os.getenv("DUFFEL_TOKEN")
    if not token:
        raise RuntimeError("DUFFEL_TOKEN missing in .env")

    resp = requests.post(
        "https://api.duffel.com/air/offer_requests?return_offers=true",
        headers={
            "Authorization": f"Bearer {token}",
            "Duffel-Version": "v2",          # check Duffel docs for the current version
            "Content-Type": "application/json",
        },
        json={"data": {
            "slices": [{"origin": origin, "destination": destination,
                        "departure_date": date}],
            "passengers": [{"type": "adult"}],
            "cabin_class": "economy",
        }},
        timeout=TIMEOUT,
    )
    resp.raise_for_status()
    return _normalize_duffel(resp.json())


def _normalize_duffel(data):
    out = []
    for offer in data.get("data", {}).get("offers", []):
        seg = offer["slices"][0]["segments"][0]
        out.append(_flight(
            flight_number=(seg.get("marketing_carrier", {}).get("iata_code", "")
                           + seg.get("marketing_carrier_flight_number", "")),
            airline=offer.get("owner", {}).get("name"),
            origin=seg["origin"]["iata_code"],
            destination=seg["destination"]["iata_code"],
            departure_time=(seg.get("departing_at") or "")[:16].replace("T", " "),
            arrival_time=(seg.get("arriving_at") or "")[:16].replace("T", " "),
            price=float(offer["total_amount"]),
            currency=offer.get("total_currency"),
            source="duffel",
        ))
    return out


# --------------------------------------------------------------------------
# Provider 3: Demo data (fallback so your demo never breaks)
# --------------------------------------------------------------------------
DEMO_FILE = os.path.join(os.path.dirname(__file__), "demo_flights.json")


def _demo_search(origin, destination, date):
    """Sample flights from demo_flights.json (edit that file to add routes).
    Any route not in the file gets 4 generic flights."""
    try:
        with open(DEMO_FILE, encoding="utf-8") as fh:
            routes = json.load(fh).get("routes", {})
    except (OSError, ValueError):
        routes = {}

    entries = routes.get(f"{origin.upper()}-{destination.upper()}")
    if not entries:
        entries = [
            {"airline": "Air India", "flight_number": "AI 605", "departs": "06:30", "duration_min": 130, "price": 5200},
            {"airline": "IndiGo", "flight_number": "6E 2134", "departs": "09:15", "duration_min": 130, "price": 4600},
            {"airline": "Vistara", "flight_number": "UK 972", "departs": "14:00", "duration_min": 130, "price": 5900},
            {"airline": "SpiceJet", "flight_number": "SG 8194", "departs": "19:45", "duration_min": 130, "price": 4300},
        ]

    out = []
    for e in entries:
        dep = datetime.strptime(f"{date} {e['departs']}", "%Y-%m-%d %H:%M")
        arr = dep + timedelta(minutes=e["duration_min"])
        out.append(_flight(
            e["flight_number"], e["airline"], origin.upper(), destination.upper(),
            dep.strftime("%Y-%m-%d %H:%M"), arr.strftime("%Y-%m-%d %H:%M"),
            status="Scheduled", price=e["price"], currency="INR", source="demo"))
    return out


# --------------------------------------------------------------------------
# Public functions
# --------------------------------------------------------------------------
_PROVIDERS = {
    "aerodatabox": _aerodatabox_search,
    "duffel": _duffel_search,
    "demo": _demo_search,           # only used when FLIGHT_PROVIDER=demo
}


def search_flights_live(origin, destination, date):
    """
    Returns a dict:
      flights     list of flight dicts (REAL API data only)
      updated_at  unix time the data was fetched (None if nothing available)
      from_cache  True if served from the saved copy
      error       None, or a message to show the user

    How "live" works: data is re-fetched from the API when the saved copy is
    older than FLIGHT_CACHE_MINUTES (default 10). Refresh happens only when
    someone looks at the page, so quota is not spent while nobody is browsing.
    If the API fails, the last saved live data is shown with an error note.
    No fake or estimated flights are ever returned in live mode.
    """
    origin, destination = origin.strip().upper(), destination.strip().upper()
    key = f"{PROVIDER}:{origin}:{destination}:{date}"

    cached, ts = _cache_get(key)
    if cached is not None and time.time() - ts < CACHE_TTL_SECONDS:
        return {"flights": cached, "updated_at": ts, "from_cache": True, "error": None}

    try:
        flights = _PROVIDERS[PROVIDER](origin, destination, date)
    except Exception as exc:  # noqa: BLE001
        print(f"[flight_service] {PROVIDER} failed: {exc}")
        if cached is not None:
            return {"flights": cached, "updated_at": ts, "from_cache": True,
                    "error": "Live update failed. Showing last known data."}
        return {"flights": [], "updated_at": None, "from_cache": False,
                "error": "Live flight data is unavailable right now."}

    now = time.time()
    _cache_set(key, flights)   # an empty list is also a valid live answer; cached to save quota
    return {"flights": flights, "updated_at": now, "from_cache": False, "error": None}


def search_flights(origin, destination, date):
    """Simple version: just the list of flights."""
    return search_flights_live(origin, destination, date)["flights"]
