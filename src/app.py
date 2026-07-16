"""Backend for the voice travel agent.

Two kinds of endpoints:
  1. /api/voice-token        -> browser calls this to get a LiveKit token (secret key stays here)
  2. /api/tools/*            -> the Vocal Bridge agent calls these as HTTP API tools;
                                they run flight search / booking against Sabre.

Tool endpoints are protected by a shared secret (X-Tool-Key) so only the agent
(which sends the same secret, configured in api_tools.json) can invoke them.

Search results are cached per browser-session (`session_id`) so that a later
"book offer 2" call re-sells the exact itinerary the user heard.
"""
from __future__ import annotations

import os
import time
import uuid
from functools import wraps

import requests
from flask import Flask, jsonify, request
from flask_cors import CORS

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass

from sabre_client import (
    SabreClient,
    SabreError,
    airline_logo,
    airline_name,
    airline_photo,
    car_photo,
    hotel_photo,
)

app = Flask(__name__)
CORS(app)  # browser (Vite dev server) calls /api/voice-token cross-origin

VOCAL_BRIDGE_API_KEY = os.environ.get("VOCAL_BRIDGE_API_KEY")
VOCAL_BRIDGE_URL = os.environ.get("VOCAL_BRIDGE_URL", "https://vocalbridgeai.com").rstrip("/")
VOCAL_BRIDGE_AGENT_ID = os.environ.get("VOCAL_BRIDGE_AGENT_ID", "")
TOOL_API_KEY = os.environ.get("TOOL_API_KEY", "")

sabre = SabreClient()

# In-memory session store, keyed by session_id. Each entry holds, per product
# ("flight" / "hotel" / "car"), the last search results and the last booking:
#   {"flight_offers": [...], "flight_booking": {...}, "hotel_offers": [...], ..., "ts": epoch}
# Fine for a demo; swap for Redis if you run multiple backend workers.
_SESSIONS: dict[str, dict] = {}
_SESSION_TTL = 3600


def _entry(session_id: str) -> dict:
    entry = _SESSIONS.get(session_id)
    if entry is None:
        entry = {"ts": time.time()}
        _SESSIONS[session_id] = entry
    return entry


def _remember(session_id: str, kind: str, offers: list) -> None:
    """Cache a product's search results; a fresh search clears that product's
    prior booking (but leaves the other products' state untouched)."""
    entry = _entry(session_id)
    entry[f"{kind}_offers"] = offers
    entry[f"{kind}_booking"] = None
    entry["ts"] = time.time()
    # opportunistic cleanup of stale sessions
    cutoff = time.time() - _SESSION_TTL
    for sid in [s for s, v in _SESSIONS.items() if v["ts"] < cutoff]:
        _SESSIONS.pop(sid, None)


def _offers_for(session_id: str, kind: str) -> list:
    entry = _SESSIONS.get(session_id)
    return entry.get(f"{kind}_offers", []) if entry else []


def _store_booking(session_id: str, kind: str, booking: dict) -> None:
    entry = _entry(session_id)
    entry[f"{kind}_booking"] = booking
    entry["ts"] = time.time()


def _enrich_offer(offer: dict) -> dict:
    """Add airline name, logo, and an airplane photo for the browser UI."""
    o = dict(offer)
    o["airline_name"] = airline_name(offer.get("airline", ""))
    o["logo_url"] = airline_logo(offer.get("airline", ""))
    o["photo_url"] = airline_photo(offer.get("airline", ""))
    o["segments"] = [
        {
            **s,
            "airline_name": airline_name(s.get("airline", "")),
            "logo_url": airline_logo(s.get("airline", "")),
        }
        for s in offer.get("segments", [])
    ]
    return o


def _enrich_hotel(offer: dict) -> dict:
    """Add a property photo for the browser UI."""
    o = dict(offer)
    o["photo_url"] = hotel_photo(offer.get("name", ""))
    return o


def _enrich_car(offer: dict) -> dict:
    """Add a vehicle photo for the browser UI."""
    o = dict(offer)
    o["photo_url"] = car_photo(offer.get("car_type", ""))
    return o


def _params() -> dict:
    """Merge JSON body and query/form params so tool calls work regardless of
    how Vocal Bridge transports the arguments."""
    data = dict(request.args)
    data.update(request.form.to_dict())
    body = request.get_json(silent=True)
    if isinstance(body, dict):
        data.update(body)
    return data


def require_tool_key(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        if not TOOL_API_KEY or request.headers.get("X-Tool-Key") != TOOL_API_KEY:
            return jsonify({"error": "unauthorized"}), 401
        return fn(*args, **kwargs)

    return wrapper


# ------------------------------------------------------------- voice token
@app.route("/api/voice-token", methods=["POST", "GET"])
def voice_token():
    """Browser -> LiveKit token. Also mints a session_id the frontend passes to the agent."""
    body = request.get_json(silent=True) or {}
    participant = body.get("participant_name", "Web User")
    headers = {"X-API-Key": VOCAL_BRIDGE_API_KEY, "Content-Type": "application/json"}
    if VOCAL_BRIDGE_AGENT_ID:  # required when using an account-level API key
        headers["X-Agent-Id"] = VOCAL_BRIDGE_AGENT_ID
    resp = requests.post(
        f"{VOCAL_BRIDGE_URL}/api/v1/token",
        headers=headers,
        json={"participant_name": participant},
        timeout=20,
    )
    data = resp.json()
    data["session_id"] = uuid.uuid4().hex
    return jsonify(data), resp.status_code


# ------------------------------------------------------------- agent tools
@app.route("/api/tools/search-flights", methods=["POST"])
@require_tool_key
def tool_search_flights():
    p = _params()
    required = ("origin", "destination", "departure_date")
    missing = [k for k in required if not p.get(k)]
    if missing:
        return jsonify({"error": f"missing required fields: {', '.join(missing)}"}), 400
    try:
        offers = sabre.search_flights(
            origin=p["origin"],
            destination=p["destination"],
            departure_date=p["departure_date"],
            return_date=p.get("return_date"),
            adults=int(p.get("adults", 1)),
            cabin=p.get("cabin"),
            max_results=int(p.get("max_results", 5)),
        )
    except SabreError as e:
        return jsonify({"error": str(e)}), 502

    session_id = p.get("session_id", "default")
    _remember(session_id, "flight", offers)

    # Compact payload the agent reads aloud; full segments stay server-side.
    return jsonify(
        {
            "count": len(offers),
            "offers": [
                {
                    "offer_id": o["offer_id"],
                    "airline": o["airline"],
                    "stops": o["stops"],
                    "total_price": o["total_price"],
                    "currency": o["currency"],
                    "summary": o["summary"],
                }
                for o in offers
            ],
        }
    )


@app.route("/api/tools/offer-details", methods=["POST"])
@require_tool_key
def tool_offer_details():
    p = _params()
    offers = _offers_for(p.get("session_id", "default"), "flight")
    match = next((o for o in offers if o["offer_id"] == str(p.get("offer_id"))), None)
    if not match:
        return jsonify({"error": "offer not found; search again"}), 404
    return jsonify(match)


@app.route("/api/tools/book-flight", methods=["POST"])
@require_tool_key
def tool_book_flight():
    p = _params()
    offers = _offers_for(p.get("session_id", "default"), "flight")
    match = next((o for o in offers if o["offer_id"] == str(p.get("offer_id"))), None)
    if not match:
        return jsonify({"error": "offer not found; search again before booking"}), 404
    for k in ("first", "last"):
        if not p.get(k):
            return jsonify({"error": f"passenger '{k}' name required"}), 400
    passenger = {
        "first": p["first"],
        "last": p["last"],
        "email": p.get("email"),
        "phone": p.get("phone"),
    }
    try:
        result = sabre.create_pnr(match, passenger)
    except SabreError as e:
        return jsonify({"error": str(e)}), 502
    # remember the booked itinerary so the browser can render the confirmation
    _store_booking(p.get("session_id", "default"), "flight", {**result, "offer": match})
    return jsonify(result)


# ------------------------------------------------------------- hotel tools
@app.route("/api/tools/search-hotels", methods=["POST"])
@require_tool_key
def tool_search_hotels():
    p = _params()
    required = ("city", "check_in", "check_out")
    missing = [k for k in required if not p.get(k)]
    if missing:
        return jsonify({"error": f"missing required fields: {', '.join(missing)}"}), 400
    try:
        offers = sabre.search_hotels(
            city=p["city"],
            check_in=p["check_in"],
            check_out=p["check_out"],
            guests=int(p.get("guests", 2)),
            rooms=int(p.get("rooms", 1)),
            max_results=int(p.get("max_results", 5)),
        )
    except SabreError as e:
        return jsonify({"error": str(e)}), 502

    _remember(p.get("session_id", "default"), "hotel", offers)
    fields = ("offer_id", "name", "brand_name", "rating", "room_type",
              "nightly_rate", "total_price", "currency", "nights", "summary")
    return jsonify(
        {"count": len(offers), "offers": [{k: o.get(k) for k in fields} for o in offers]}
    )


@app.route("/api/tools/book-hotel", methods=["POST"])
@require_tool_key
def tool_book_hotel():
    p = _params()
    offers = _offers_for(p.get("session_id", "default"), "hotel")
    match = next((o for o in offers if o["offer_id"] == str(p.get("offer_id"))), None)
    if not match:
        return jsonify({"error": "hotel offer not found; search again before booking"}), 404
    for k in ("first", "last"):
        if not p.get(k):
            return jsonify({"error": f"guest '{k}' name required"}), 400
    guest = {
        "first": p["first"],
        "last": p["last"],
        "email": p.get("email"),
        "phone": p.get("phone"),
    }
    try:
        result = sabre.create_hotel_booking(match, guest)
    except SabreError as e:
        return jsonify({"error": str(e)}), 502
    _store_booking(p.get("session_id", "default"), "hotel", {**result, "offer": match})
    return jsonify(result)


# --------------------------------------------------------------- car tools
@app.route("/api/tools/search-cars", methods=["POST"])
@require_tool_key
def tool_search_cars():
    p = _params()
    required = ("pickup_location", "pickup_date", "dropoff_date")
    missing = [k for k in required if not p.get(k)]
    if missing:
        return jsonify({"error": f"missing required fields: {', '.join(missing)}"}), 400
    try:
        offers = sabre.search_cars(
            pickup_location=p["pickup_location"],
            pickup_date=p["pickup_date"],
            dropoff_date=p["dropoff_date"],
            max_results=int(p.get("max_results", 5)),
        )
    except SabreError as e:
        return jsonify({"error": str(e)}), 502

    _remember(p.get("session_id", "default"), "car", offers)
    fields = ("offer_id", "vendor_name", "car_type", "transmission",
              "daily_rate", "total_price", "currency", "days", "summary")
    return jsonify(
        {"count": len(offers), "offers": [{k: o.get(k) for k in fields} for o in offers]}
    )


@app.route("/api/tools/book-car", methods=["POST"])
@require_tool_key
def tool_book_car():
    p = _params()
    offers = _offers_for(p.get("session_id", "default"), "car")
    match = next((o for o in offers if o["offer_id"] == str(p.get("offer_id"))), None)
    if not match:
        return jsonify({"error": "car offer not found; search again before booking"}), 404
    for k in ("first", "last"):
        if not p.get(k):
            return jsonify({"error": f"driver '{k}' name required"}), 400
    driver = {
        "first": p["first"],
        "last": p["last"],
        "email": p.get("email"),
        "phone": p.get("phone"),
    }
    try:
        result = sabre.create_car_booking(match, driver)
    except SabreError as e:
        return jsonify({"error": str(e)}), 502
    _store_booking(p.get("session_id", "default"), "car", {**result, "offer": match})
    return jsonify(result)


@app.route("/api/state")
def state():
    """The browser polls this (by session_id) to render the itinerary table,
    airline logos, plane photos, and the booking confirmation."""
    sid = request.args.get("session_id", "default")
    entry = _SESSIONS.get(sid) or {}

    offers = [_enrich_offer(o) for o in entry.get("flight_offers", [])]
    booking = entry.get("flight_booking")
    if booking and booking.get("offer"):
        booking = {**booking, "offer": _enrich_offer(booking["offer"])}

    hotels = [_enrich_hotel(o) for o in entry.get("hotel_offers", [])]
    hotel_booking = entry.get("hotel_booking")
    if hotel_booking and hotel_booking.get("offer"):
        hotel_booking = {**hotel_booking, "offer": _enrich_hotel(hotel_booking["offer"])}

    cars = [_enrich_car(o) for o in entry.get("car_offers", [])]
    car_booking = entry.get("car_booking")
    if car_booking and car_booking.get("offer"):
        car_booking = {**car_booking, "offer": _enrich_car(car_booking["offer"])}

    return jsonify(
        {
            "offers": offers,
            "booking": booking,
            "hotels": hotels,
            "hotel_booking": hotel_booking,
            "cars": cars,
            "car_booking": car_booking,
        }
    )


@app.route("/api/session")
def new_session():
    """Browser fetches a session_id, then hands it to the agent via the
    set_session client action so tool calls and search results line up."""
    return jsonify({"session_id": uuid.uuid4().hex})


@app.route("/api/health")
def health():
    return jsonify({"ok": True, "mock": os.environ.get("SABRE_MOCK", "0") == "1"})


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5001))
    app.run(host="0.0.0.0", port=port)
