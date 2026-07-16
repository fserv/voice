# Skylark — Voice Travel Agent (Vocal Bridge + Sabre)

Book airline flights, hotels, and rental cars by voice in the browser. A Vocal
Bridge voice agent ("Skylark") talks to the caller, calls this backend as HTTP
tools, and the backend searches/books through the Sabre Dev Studio (CERT) REST
APIs.

```
Browser (React + @vocalbridgeai/react)
   │  voice/WebRTC  ─────────────►  Vocal Bridge agent "Skylark" (server-side prompt + tools)
   │  GET /api/voice-token ───────►  Flask backend (mints LiveKit token, key stays server-side)
   │  ◄── show_flights/hotels/cars + *_booking client actions ── agent updates the UI
                                          │
   Vocal Bridge agent ──HTTP API tools (HTTPS)──►  Flask backend
                                          ├─ POST /api/tools/search-flights ─► Sabre Bargain Finder Max
                                          ├─ POST /api/tools/offer-details
                                          ├─ POST /api/tools/book-flight     ─► Sabre Create PNR (test)
                                          ├─ POST /api/tools/search-hotels   ─► Sabre Hotel Availability
                                          ├─ POST /api/tools/book-hotel      ─► test confirmation
                                          ├─ POST /api/tools/search-cars     ─► Sabre Cars availability
                                          └─ POST /api/tools/book-car        ─► test confirmation
```

## Files
| Path | What it is |
|------|-----------|
| `app.py` | Flask backend: voice-token + `/api/tools/*` (flight/hotel/car agent tools) + `/api/session` |
| `sabre_client.py` | Sabre OAuth2 + flight/hotel/car search + booking (with `SABRE_MOCK`) |
| `agent/travel_prompt.txt` | The voice agent's system prompt |
| `agent/api_tools.template.json` | HTTP tool defs (URL + secret filled in by `apply_config.sh`) |
| `agent/client_actions.json` | `set_session` + `show_*` / `*_booking` UI actions |
| `agent/apply_config.sh` | Pushes prompt + tools + actions to the Vocal Bridge agent via `vb` |
| `frontend/` | React browser app (mic, live transcript, flight/hotel/car cards, confirmations) |

## Setup

### 1. Configure
```bash
cp .env.example .env
# edit .env: SABRE_CLIENT_ID / SABRE_CLIENT_SECRET / SABRE_PCC,
# VOCAL_BRIDGE_API_KEY, and a long random TOOL_API_KEY.
# Leave SABRE_MOCK=1 to try it with no Sabre creds; set 0 for real CERT calls.
```

### 2. Backend
```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
./run_backend.sh          # serves on :5001
```

### 3. Expose over HTTPS (Vocal Bridge tool URLs must be HTTPS)
```bash
cloudflared tunnel --url http://localhost:5001
# copy the https://...trycloudflare.com URL into .env as PUBLIC_BASE_URL
```

### 4. Configure the voice agent
```bash
set -a && . ./.env && set +a
./agent/apply_config.sh   # renders api_tools.json with your URL+key and pushes it
```

### 5. Frontend
```bash
cd frontend && npm install && npm run dev   # http://localhost:5173 (proxies /api to :5001)
```
Open the page, click **Start talking**, and say *"I want to fly from New York to
Los Angeles next Friday, and I'll need a hotel and a rental car there."*

## Notes
- **Sandbox only.** Flight booking creates a *test* PNR in Sabre CERT — no payment, no ticketing. To go live you need a production Sabre PCC + ticketing agreement, then switch `SABRE_BASE_URL`.
- **Hotels & cars.** Mock mode is fully wired end to end. Live hotel/car **booking** sells a `Hotel` / `Car` segment through the *same* `CreatePassengerNameRecordRQ` endpoint as flights (shared `_commit_pnr`), using the chain/hotel code + rate key (hotels) or vendor + rate code (cars) captured during search. The live search + booking calls follow Sabre's documented REST shapes but are **UNVERIFIED against live CERT** — hotel/car content there is sparse, and the exact leaf field names, the hotel `TimeSpan` date format, and whether a guarantee/form-of-payment is required may need adjusting for your CSL/GDS setup. If the response shape differs, the code raises a specific `SabreError` rather than fabricating a confirmation. Keep `SABRE_MOCK=1` for the demo flow; validate the live path with one test booking against CERT before relying on it.
- **Auth.** Tool endpoints require the `X-Tool-Key` shared secret; only the agent (configured with the same key) can call them. The Vocal Bridge API key never reaches the browser.
- **Sessions.** Search results are cached per `session_id` so "book option 2" re-sells the exact itinerary. The browser mints the id (`/api/session`) and passes it to the agent via the `set_session` action.
- **Mock mode.** `SABRE_MOCK=1` returns canned flights, hotels, cars, and bookings so you can demo the full voice flow before wiring real Sabre creds.
