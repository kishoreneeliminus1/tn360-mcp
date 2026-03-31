# ============================================================================ #
# TN360 MCP Server – Fully Integrated with DashCam Video Support
# ============================================================================ #

import os
import httpx
import asyncio
import base64
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional, Any, Dict, List

from fastmcp import FastMCP
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Route, Mount

mcp = FastMCP("TN360 Fleet Server")

# ============================================================================ #
# LOGGING CONFIGURATION
# ============================================================================ #

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)

# ============================================================================ #
# CONFIG
# ============================================================================ #

TN360_BASE_URL = os.environ.get("TN360_BASE_URL", "https://api-au.telematics.com")
TN360_API_KEY = os.environ.get("TN360_API_KEY", "")


def _headers() -> dict:
    """Generate headers for TN360 HTTP requests."""
    if not TN360_API_KEY:
        raise RuntimeError("TN360_API_KEY environment variable is not set.")

    return {
        "Authorization": f"Bearer {TN360_API_KEY}",
        "Accept": "application/json",
        "Content-Type": "application/json",
        "Expect": ""  # Prevent 417 Expectation Failed
    }

# ============================================================================ #
# EVENT TYPE FILTERING
# ============================================================================ #

# FIX: Only include event type names the TN360 API actually accepts.
# All confirmed via live API testing — camelCase types were rejected with
# "invalid_type_name". The API accepts: SPEED, CAMERA, GEOFENCE, DRIVER.
# Additional types (HARSH_BRAKING etc.) should be tested before re-adding.
VALID_TN360_EVENT_TYPES = {
    "SPEED",
    "CAMERA",
    "GEOFENCE",
    "DRIVER",
    "IGNITION",
    "IOR",        
    "POSITION",    
    "ALARM",       
    # Add others below only after confirming they are accepted by the TN360 API:
    # "HARSH_BRAKING", "HARSH_ACCELERATION", "HARSH_CORNERING",
    # "OVER_REVVING", "DRIVER_FATIGUE", "DRIVER_DISTRACTION", "SEATBELT_VIOLATION",
    # "POSITION", "GPIO", "INSTALLATION", "ALARM", "ALERT",
    # "COMMUNICATION", "MASS", "PTO", "PRETRIP", "IGNITION",
}

# FIX: Default to types that are confirmed working. Removed all camelCase
# and unverified types that caused "invalid_type_name" API errors.
DEFAULT_EVENT_TYPES = "SPEED,CAMERA,GEOFENCE,DRIVER"


def sanitize_event_types(raw: str) -> str:
    """Sanitize and validate event type list against confirmed-working types."""
    cleaned = [t.strip().upper() for t in raw.split(",") if t.strip().upper() in VALID_TN360_EVENT_TYPES]
    # Fall back to default if nothing valid remains
    return ",".join(cleaned) if cleaned else DEFAULT_EVENT_TYPES


# ============================================================================ #
# UNIVERSAL SAFE WRAPPER
# ============================================================================ #

def wrap_result(raw: Any) -> dict:
    """Normalize MCP response output format."""
    if isinstance(raw, dict):
        if "error" in raw:
            return {
                "success": False,
                "data": None,
                "error": raw.get("error"),
                "meta": {k: v for k, v in raw.items() if k != "error"}
            }
        # FIX: Handle paginated TN360 responses that wrap data in {"data": [...], "meta": {...}}
        if "data" in raw:
            return {
                "success": True,
                "data": raw.get("data"),
                "error": None,
                "meta": raw.get("meta", {})
            }
        return {"success": True, "data": raw, "error": None, "meta": {}}

    if isinstance(raw, list):
        return {"success": True, "data": raw, "error": None, "meta": {"count": len(raw)}}

    return {
        "success": False,
        "data": None,
        "error": f"Unexpected TN360 response type: {type(raw).__name__}",
        "meta": {"raw": str(raw)}
    }

# ============================================================================ #
# HTTP GET WRAPPER
# ============================================================================ #

async def _get(path: str, params: Optional[dict] = None) -> dict | list:
    """Generic GET wrapper with logging + retry."""
    url = f"{TN360_BASE_URL}/v1{path}"
    timeout = httpx.Timeout(60.0)
    params = params or {}

    logging.info(f"[HTTP GET] URL={url} PARAMS={params}")

    for attempt in range(3):
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                r = await client.get(url, headers=_headers(), params=params)

                logging.info(f"[HTTP GET] STATUS={r.status_code}")
                logging.debug(f"[HTTP GET] RESPONSE={r.text[:400]}")

                if 200 <= r.status_code < 300:
                    try:
                        return r.json()
                    except Exception:
                        return {"error": "Invalid JSON from TN360", "response_text": r.text}

                return {"error": f"HTTP {r.status_code}", "response": r.text}

        except Exception as e:
            logging.error(f"[HTTP GET] ERROR: {e}")
            if attempt == 2:
                return {"error": str(e)}
            await asyncio.sleep(1.5 * (attempt + 1))

# ============================================================================ #
# HTTP POST WRAPPER
# ============================================================================ #

async def _post(path: str, payload: dict) -> dict:
    """Generic POST wrapper with retry."""
    url = f"{TN360_BASE_URL}/v1{path}"
    timeout = httpx.Timeout(60.0)

    logging.info(f"[HTTP POST] URL={url}")
    logging.info(f"[HTTP POST] PAYLOAD={payload}")

    for attempt in range(3):
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                r = await client.post(url, headers=_headers(), json=payload)

                logging.info(f"[HTTP POST] STATUS={r.status_code}")
                logging.debug(f"[HTTP POST] RESPONSE={r.text[:400]}")

                if 200 <= r.status_code < 300:
                    try:
                        return r.json()
                    except Exception:
                        return {"error": "Invalid JSON", "response_text": r.text}

                return {"error": f"HTTP {r.status_code}", "response": r.text}

        except Exception as e:
            logging.error(f"[HTTP POST] ERROR: {e}")
            if attempt == 2:
                return {"error": str(e)}
            await asyncio.sleep(1.25 * (attempt + 1))

# ============================================================================ #
# HTTP PUT WRAPPER
# ============================================================================ #

async def _put(path: str, payload: dict) -> dict:
    """Generic PUT wrapper."""
    url = f"{TN360_BASE_URL}/v1{path}"
    timeout = httpx.Timeout(60.0)

    logging.info(f"[HTTP PUT] URL={url}")
    logging.info(f"[HTTP PUT] PAYLOAD={payload}")

    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            r = await client.put(url, headers=_headers(), json=payload)

            logging.info(f"[HTTP PUT] STATUS={r.status_code}")
            logging.debug(f"[HTTP PUT] RESPONSE={r.text[:400]}")

            if 200 <= r.status_code < 300:
                try:
                    return r.json()
                except Exception:
                    return {"error": "Invalid JSON", "response_text": r.text}

            return {"error": f"HTTP {r.status_code}", "response": r.text}

    except Exception as e:
        logging.error(f"[HTTP PUT] ERROR: {e}")
        return {"error": str(e)}

# ============================================================================ #
# MCP TOOLS
# ============================================================================ #

# ============================================================================ #
# MCP TOOLS
# ============================================================================ #

@mcp.tool()
async def get_vehicles(fleet_id: Optional[int] = None) -> dict:
    """
    Fetch all vehicles in the fleet, optionally filtered by fleet_id.
    Returns vehicle name, registration, type, status and associated IDs.
    """
    params = {"fleetId": fleet_id} if fleet_id else {}
    return wrap_result(await _get("/vehicles", params))


# ─────────────────────────────────────────────────────────────────────────────
# NEW: get_vehicle_stats
# ─────────────────────────────────────────────────────────────────────────────

@mcp.tool()
async def get_vehicle_stats(
    vehicle_id: Optional[int] = None,
    embed_vehicles: bool = True,
    last_updated: Optional[str] = None,
) -> dict:
    """
    Fetch last-known GPS location for one or all vehicles using the
    purpose-built /v1/vehicles/stats endpoint.

    This is the PREFERRED method for "recent location" queries. It returns
    each vehicle's last GPS fix (lat, lng, speed, direction, timestamp) in a
    single lightweight call — far faster than pulling event streams and
    filtering client-side.

    Parameters
    ──────────
    vehicle_id:      Optional. Filter to a single vehicle by ID.
                     Omit to get last-known location for the entire fleet.

    embed_vehicles:  When True (default), the response includes the vehicle's
                     name, registration, and other metadata alongside the GPS
                     data. Set False for a minimal/faster payload when you
                     only need coordinates.

    last_updated:    ISO 8601 timestamp string (e.g. "2026-03-31T10:00:00Z").
                     When provided, only vehicles whose GPS position has been
                     updated SINCE this timestamp are returned. Use the most
                     recent 'updatedAt' value from the previous response to
                     implement efficient delta polling.
                     Fair-use guideline: poll no more than once every 5 minutes.

    Response fields (per vehicle)
    ──────────────────────────────
    GPS.Lat        — Latitude
    GPS.Lng        — Longitude
    GPS.Spd        — Speed (km/h)
    GPS.Dir        — Heading (degrees)
    GPS.Alt        — Altitude (metres)
    GPS.NSat       — Satellites in use
    GPS.valid      — Whether the GPS fix is valid
    updatedAt      — Timestamp of the last GPS update (use for next poll)
    vehicle.name   — Vehicle name (e.g. "RR13") — only if embed_vehicles=True
    vehicle.registration — Rego plate — only if embed_vehicles=True

    Example usage
    ──────────────
    # Single vehicle last location:
    get_vehicle_stats(vehicle_id=62118)

    # Full fleet snapshot:
    get_vehicle_stats()

    # Efficient delta poll (only vehicles that moved since last check):
    get_vehicle_stats(last_updated="2026-03-31T10:00:00Z")
    """
    params: dict = {"gps": "true"}

    if embed_vehicles:
        params["embed"] = "vehicles"

    if last_updated:
        # Ensure the timestamp is URL-safe ISO 8601
        try:
            dt = datetime.fromisoformat(last_updated.replace("Z", "+00:00"))
            params["last_updated"] = dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
        except ValueError:
            # Pass through as-is if parsing fails — let the API reject it
            params["last_updated"] = last_updated

    if vehicle_id:
        params["vehicleId"] = vehicle_id

    return wrap_result(await _get("/vehicles/stats", params))


# ─────────────────────────────────────────────────────────────────────────────
# NEW: get_vehicle_location
# ─────────────────────────────────────────────────────────────────────────────

@mcp.tool()
async def get_vehicle_location(
    vehicle_id: int,
    hours_back: int = 6,
) -> dict:
    """
    Get a vehicle's recent location from its GEOFENCE events using a tight
    time window and single-vehicle filter.

    Use this as a fallback when get_vehicle_stats() does not return data for
    a vehicle (e.g. the device hasn't reported recently and falls outside the
    stats window). GEOFENCE events carry GPS coordinates and are generated
    whenever a vehicle enters or exits a monitored zone, making them a reliable
    position source even when ignition/position events are unavailable.

    This is significantly faster than a full fleet event query because:
      • Only GEOFENCE type events are fetched (smallest event volume)
      • Scoped to a single vehicle_id
      • Time window defaults to 6 hours (not 3 days)

    Parameters
    ──────────
    vehicle_id:   Required. The TN360 vehicle ID (integer).
                  Use get_vehicles() to look up IDs by name/registration.

    hours_back:   How many hours of history to search. Default 6.
                  Increase to 24 if the vehicle hasn't moved recently
                  (e.g. overnight or weekend). Max is 168 (7 days per API limit).

    Response
    ────────
    Returns raw GEOFENCE events sorted most-recent-first. Each event includes:
      GPS.Lat / GPS.Lng   — position at time of geofence trigger
      GPS.Spd             — speed at trigger
      location            — human-readable address string
      timeAt              — UTC timestamp of the event
      action              — GEO-EN (entered) or GEO-EX (exited)

    Interpret the most recent event's GPS and location as the vehicle's last
    known position.

    Example usage
    ──────────────
    # Last 6 hours for RR13 (vehicle ID 62118):
    get_vehicle_location(vehicle_id=62118)

    # Last 24 hours for a vehicle that may have been parked overnight:
    get_vehicle_location(vehicle_id=62118, hours_back=24)
    """
    hours_back = max(1, min(hours_back, 168))  # clamp: 1h–168h (7 days)

    now = datetime.now(timezone.utc)
    from_dt = now - timedelta(hours=hours_back)

    params = {
        "types": "GEOFENCE,IGNITION,IOR",
        "from": from_dt.isoformat().replace("+00:00", "Z"),
        "to": now.isoformat().replace("+00:00", "Z"),
        "vehicleId": vehicle_id,
        "pruning": "ALL",
    }

    logging.info(f"[get_vehicle_location] vehicle_id={vehicle_id} hours_back={hours_back}")
    return wrap_result(await _get("/events", params))


# ─────────────────────────────────────────────────────────────────────────────
# EXISTING: get_events (tightened default window + IGNITION support)
# ─────────────────────────────────────────────────────────────────────────────

@mcp.tool()
async def get_events(
    event_types: str = DEFAULT_EVENT_TYPES,
    from_date: Optional[str] = None,
    to_date: Optional[str] = None,
    vehicle_id: Optional[int] = None,
) -> dict:
    """
    Fetch vehicle events from TN360.

    event_types: Comma-separated list of event types.
                 Confirmed working: SPEED, CAMERA, GEOFENCE, DRIVER, IGNITION
                 Defaults to SPEED,CAMERA,GEOFENCE,DRIVER if not specified.

                 IGNITION events are especially useful for KM-driven queries:
                 each event includes an `odometer` field, so today's distance =
                 (last IGNITION OFF odometer) − (first IGNITION ON odometer).

    from_date:   ISO 8601 datetime string. Defaults to 24 hours ago.
                 (Previously defaulted to 3 days — tightened for performance.)

    to_date:     ISO 8601 datetime string. Defaults to now.

    vehicle_id:  Optional — filter to a specific vehicle.
                 Always provide this when querying a single vehicle to
                 dramatically reduce response size and API load.

    Tips for faster results
    ────────────────────────
    • Always supply vehicle_id when you only need one vehicle's data.
    • Use the most specific event_types list possible — avoid fetching all
      types when you only need one (e.g. use "IGNITION" for KM queries,
      "GEOFENCE" for location queries).
    • For last-known location, prefer get_vehicle_stats() or
      get_vehicle_location() over this tool.
    • Keep date ranges as tight as possible — the API performs best with
      narrow windows.
    """
    now = datetime.now(timezone.utc).replace(microsecond=0)

    if to_date:
        to_dt = datetime.fromisoformat(to_date).astimezone(timezone.utc)
    else:
        to_dt = now

    if from_date:
        from_dt = datetime.fromisoformat(from_date).astimezone(timezone.utc)
    else:
        # IMPROVED: tightened from 3 days → 24 hours for better default performance
        from_dt = to_dt - timedelta(hours=24)

    params = {
        "types": sanitize_event_types(event_types),
        "from": from_dt.isoformat().replace("+00:00", "Z"),
        "to": to_dt.isoformat().replace("+00:00", "Z"),
        "pruning": "ALL",
    }

    if vehicle_id:
        params["vehicleId"] = vehicle_id

    return wrap_result(await _get("/events", params))


# ─────────────────────────────────────────────────────────────────────────────
# EXISTING TOOLS (unchanged)
# ─────────────────────────────────────────────────────────────────────────────

@mcp.tool()
async def get_fleets() -> dict:
    """Fetch all fleet groups defined in the TN360 account."""
    return wrap_result(await _get("/fleets"))


@mcp.tool()
async def get_users(status: str = "active") -> dict:
    """
    Fetch TN360 users. status='active' (default) or 'all'.
    """
    params = {} if status == "all" else {"code": status}
    return wrap_result(await _get("/users", params))


@mcp.tool()
async def get_geofences() -> dict:
    """Fetch all geofence zones configured in the TN360 account."""
    return wrap_result(await _get("/geofences"))


@mcp.tool()
async def get_vehicle_odometer(vehicle_id: int) -> dict:
    """
    Fetch odometer, engine hours, distance, and battery meters for a vehicle.

    The 'odometer' type entry shows the vehicle's total computed odometer.
    The 'distance' type entry shows cumulative GPS-tracked distance.
    The 'hours' type entry shows total engine hours.

    For today's KM driven, prefer fetching IGNITION events via get_events()
    and subtracting first-ON odometer from last-OFF odometer — more accurate
    than differencing two meter snapshots taken at different times.
    """
    return wrap_result(await _get(f"/vehicles/{vehicle_id}/meters"))


@mcp.tool()
async def get_vehicle_users(vehicle_id: int) -> dict:
    """Fetch users (drivers) associated with a specific vehicle."""
    return wrap_result(await _get(f"/vehicles/{vehicle_id}/users"))


@mcp.tool()
async def get_vehicle_fleets(vehicle_id: int) -> dict:
    """Fetch fleet group memberships for a specific vehicle."""
    return wrap_result(await _get(f"/vehicles/{vehicle_id}/fleets"))


@mcp.tool()
async def get_vehicle_devices(vehicle_id: int) -> dict:
    """Fetch telematics device(s) installed on a specific vehicle."""
    return wrap_result(await _get(f"/vehicles/{vehicle_id}/devices", {"pruning": "all"}))


@mcp.tool()
async def get_vehicle_images(vehicle_id: int) -> dict:
    """Fetch dashcam or inspection images associated with a specific vehicle."""
    return wrap_result(await _get(f"/vehicles/{vehicle_id}/images"))


@mcp.tool()
async def get_vehicle_drivers(
    from_date: Optional[str] = None,
    to_date: Optional[str] = None,
    vehicle_id: Optional[int] = None,
) -> dict:
    """
    Fetch EWD/driver events (logon, logoff, work, rest) from TN360.

    Uses the /events endpoint with types=DRIVER. Note: the TN360 API does not
    filter server-side by vehicle_id for driver events — all vehicle results
    are returned and should be filtered client-side by vehicleId field.

    from_date: ISO 8601 datetime string. Defaults to 3 days ago.
    to_date:   ISO 8601 datetime string. Defaults to now.
    vehicle_id: Passed as a query param — may not be honoured server-side.
    """
    now = datetime.now(timezone.utc).replace(microsecond=0)

    if to_date:
        to_dt = datetime.fromisoformat(to_date).astimezone(timezone.utc)
    else:
        to_dt = now

    if from_date:
        from_dt = datetime.fromisoformat(from_date).astimezone(timezone.utc)
    else:
        from_dt = to_dt - timedelta(days=3)

    params = {
        "types": "DRIVER",
        "from": from_dt.isoformat().replace("+00:00", "Z"),
        "to": to_dt.isoformat().replace("+00:00", "Z"),
        "pruning": "ALL",
    }

    if vehicle_id:
        params["vehicleId"] = vehicle_id

    return wrap_result(await _get("/events", params))

@mcp.tool()
async def get_trip_summary(
    vehicle_id: int,
    from_date: Optional[str] = None,
    to_date: Optional[str] = None,
) -> dict:
    """
    Return a structured summary of all trips made by a vehicle over a date range.

    Fetches IGNITION events and pairs ON→OFF sequences into discrete trips,
    each with start/end time, start/end location, duration, and odometer-based
    distance where the device reports odometer readings.

    Where odometer is None (common on some devices), GPS-based distance is
    estimated using the Haversine formula between the ON and OFF GPS coordinates.
    This is a straight-line approximation — actual road distance will be higher.

    Use this tool when the user asks any of:
      • "How far did X drive today / this week?"
      • "How many trips did X make?"
      • "What time did X start / finish?"
      • "How long was X on the road?"
      • "Where did X go today?"

    Parameters
    ──────────
    vehicle_id:   Required. TN360 vehicle ID (integer).
                  Use get_vehicles() or get_vehicle_stats() to look up by name/rego.

    from_date:    ISO 8601 datetime string. Defaults to start of current day (local midnight UTC+11).
                  Examples: "2026-03-31T00:00:00+11:00", "2026-03-31T00:00:00Z"

    to_date:      ISO 8601 datetime string. Defaults to now.

    Response structure
    ──────────────────
    {
      "vehicle_id": 319679,
      "period": {
        "from": "2026-03-31T00:00:00+11:00",
        "to":   "2026-03-31T23:59:59+11:00"
      },
      "summary": {
        "total_trips": 3,
        "total_distance_km": 365.4,       # sum of all trip distances
        "total_drive_time_mins": 312,      # sum of all trip durations
        "total_idle_time_mins": 47,        # time engine on but not moving (approx)
        "first_ignition_on":  "02:25 AEST",
        "last_ignition_off":  "12:36 AEST",
        "odometer_available": false        # true if device reported odometer readings
      },
      "trips": [
        {
          "trip_number": 1,
          "ignition_on":  "2026-03-31T02:25:00+11:00",
          "ignition_off": "2026-03-31T05:46:00+11:00",
          "start_location": "Shell Keith, Dukes Highway, Keith, SA",
          "end_location":   "Bradford Way, Cavan, SA",
          "duration_mins": 201,
          "distance_km": 160.2,            # odometer-based if available, else Haversine
          "distance_source": "haversine",  # "odometer" | "haversine" | "unknown"
          "odometer_start": null,
          "odometer_end":   null,
          "start_gps": {"lat": -36.1, "lng": 140.6},
          "end_gps":   {"lat": -34.8, "lng": 138.6},
          "max_speed_kmh": null            # populated if SPEED events available
        }
      ],
      "unpaired_events": []  # ON events with no matching OFF (vehicle still running)
    }

    Notes
    ─────
    • Pairing logic: each IGNITION ON is matched to the next IGNITION OFF.
      If the last event is an ON with no following OFF, it is placed in
      `unpaired_events` — the vehicle is likely still running.
    • Short ignition cycles < 2 minutes are flagged with "possible_idle": true
      as they likely represent engine starts without a real trip (e.g. depot moves).
    • Odometer readings: the TN360 API returns odometer on ignition events for
      many devices but not all. When present, distance = off_odo - on_odo.
      When absent, Haversine straight-line distance is used as a fallback.
    • For vehicles that crossed midnight, ensure from_date is set to the
      previous day's start time to capture the full run.

    Example usage
    ──────────────
    # All trips for P115 today:
    get_trip_summary(vehicle_id=319679,
                     from_date="2026-03-31T00:00:00+11:00",
                     to_date="2026-03-31T23:59:59+11:00")

    # Full week for RR32:
    get_trip_summary(vehicle_id=229418,
                     from_date="2026-03-25T00:00:00+11:00",
                     to_date="2026-03-31T23:59:59+11:00")
    """
    import math

    def haversine_km(lat1, lng1, lat2, lng2) -> float:
        """Straight-line distance between two GPS coordinates in km."""
        R = 6371.0
        phi1, phi2 = math.radians(lat1), math.radians(lat2)
        dphi = math.radians(lat2 - lat1)
        dlam = math.radians(lng2 - lng1)
        a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlam / 2) ** 2
        return round(R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a)), 2)

    def parse_dt(s: str) -> datetime:
        return datetime.fromisoformat(s).astimezone(timezone.utc)

    def fmt_local(dt_utc: datetime, tz_offset_hrs: int = 11) -> str:
        """Format a UTC datetime as a human-readable local time string."""
        local = dt_utc + timedelta(hours=tz_offset_hrs)
        return local.strftime("%H:%M AEST")

    # ── Date range ────────────────────────────────────────────────────────────
    now = datetime.now(timezone.utc).replace(microsecond=0)

    to_dt = parse_dt(to_date) if to_date else now
    if from_date:
        from_dt = parse_dt(from_date)
    else:
        # Default: start of today in AEST (UTC+11)
        today_aest = (now + timedelta(hours=11)).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        from_dt = today_aest - timedelta(hours=11)

    # ── Fetch IGNITION events ─────────────────────────────────────────────────
    params = {
        "types": "IGNITION",
        "from": from_dt.isoformat().replace("+00:00", "Z"),
        "to": to_dt.isoformat().replace("+00:00", "Z"),
        "vehicleId": vehicle_id,
        "pruning": "ALL",
    }

    raw = await _get("/events", params)

    # Handle API errors
    if isinstance(raw, dict) and "error" in raw:
        return wrap_result(raw)

    events = raw.get("data", []) if isinstance(raw, dict) else raw
    if not isinstance(events, list):
        return wrap_result({"error": "Unexpected response format from /events", "raw": str(raw)[:200]})

    # Filter strictly to this vehicle (API may return others)
    events = [e for e in events if e.get("vehicleId") == vehicle_id]

    # Sort chronologically
    events.sort(key=lambda e: e.get("timeAt", ""))

    if not events:
        return wrap_result({
            "vehicle_id": vehicle_id,
            "period": {"from": from_dt.isoformat(), "to": to_dt.isoformat()},
            "summary": {
                "total_trips": 0,
                "total_distance_km": 0,
                "total_drive_time_mins": 0,
                "total_idle_time_mins": 0,
                "first_ignition_on": None,
                "last_ignition_off": None,
                "odometer_available": False,
            },
            "trips": [],
            "unpaired_events": [],
            "note": "No IGNITION events found for this vehicle in the specified period."
        })

    # ── Pair ON → OFF ─────────────────────────────────────────────────────────
    trips = []
    unpaired = []
    trip_number = 0
    i = 0
    odometer_seen = False

    while i < len(events):
        ev = events[i]
        action = (ev.get("action") or "").upper()

        if action == "ON":
            # Find the next OFF
            off_ev = None
            for j in range(i + 1, len(events)):
                if (events[j].get("action") or "").upper() == "OFF":
                    off_ev = events[j]
                    i = j  # advance outer loop past the OFF event
                    break

            on_dt = parse_dt(ev["timeAt"])
            on_gps = ev.get("GPS") or {}
            on_loc = ev.get("location") or ""
            on_odo = ev.get("odometer")

            if on_odo is not None:
                odometer_seen = True

            if off_ev:
                off_dt = parse_dt(off_ev["timeAt"])
                off_gps = off_ev.get("GPS") or {}
                off_loc = off_ev.get("location") or ""
                off_odo = off_ev.get("odometer")

                if off_odo is not None:
                    odometer_seen = True

                duration_mins = round((off_dt - on_dt).total_seconds() / 60)

                # Distance calculation
                if on_odo is not None and off_odo is not None:
                    distance_km = round(abs(off_odo - on_odo), 2)
                    distance_source = "odometer"
                elif (
                    on_gps.get("Lat") and on_gps.get("Lng")
                    and off_gps.get("Lat") and off_gps.get("Lng")
                ):
                    distance_km = haversine_km(
                        on_gps["Lat"], on_gps["Lng"],
                        off_gps["Lat"], off_gps["Lng"]
                    )
                    distance_source = "haversine"
                else:
                    distance_km = None
                    distance_source = "unknown"

                trip_number += 1
                trips.append({
                    "trip_number": trip_number,
                    "ignition_on":  ev["timeAt"],
                    "ignition_off": off_ev["timeAt"],
                    "ignition_on_local":  fmt_local(on_dt),
                    "ignition_off_local": fmt_local(off_dt),
                    "start_location": on_loc,
                    "end_location":   off_loc,
                    "duration_mins":  duration_mins,
                    "distance_km":    distance_km,
                    "distance_source": distance_source,
                    "odometer_start": on_odo,
                    "odometer_end":   off_odo,
                    "start_gps": {
                        "lat": on_gps.get("Lat"),
                        "lng": on_gps.get("Lng"),
                    },
                    "end_gps": {
                        "lat": off_gps.get("Lat"),
                        "lng": off_gps.get("Lng"),
                    },
                    "possible_idle": duration_mins < 2,
                })
            else:
                # No matching OFF — vehicle still running or event missing
                unpaired.append({
                    "action": "ON",
                    "timeAt": ev["timeAt"],
                    "timeAt_local": fmt_local(on_dt),
                    "location": on_loc,
                    "note": "No matching IGNITION OFF found — vehicle may still be running."
                })

        i += 1

    # ── Aggregate summary ─────────────────────────────────────────────────────
    real_trips = [t for t in trips if not t.get("possible_idle")]
    total_distance = sum(t["distance_km"] for t in trips if t["distance_km"] is not None)
    total_drive_mins = sum(t["duration_mins"] for t in trips)

    # Approximate idle: short ignition cycles < 2 min
    idle_trips = [t for t in trips if t.get("possible_idle")]
    idle_mins = sum(t["duration_mins"] for t in idle_trips)

    first_on = trips[0]["ignition_on_local"] if trips else (
        unpaired[0]["timeAt_local"] if unpaired else None
    )
    last_off_trips = [t for t in trips if not t.get("possible_idle")]
    last_off = last_off_trips[-1]["ignition_off_local"] if last_off_trips else None

    return wrap_result({
        "vehicle_id": vehicle_id,
        "period": {
            "from": from_dt.isoformat(),
            "to": to_dt.isoformat(),
        },
        "summary": {
            "total_trips": len(real_trips),
            "total_distance_km": round(total_distance, 2),
            "distance_source": "odometer" if odometer_seen else "haversine_approximation",
            "total_drive_time_mins": total_drive_mins - idle_mins,
            "total_idle_time_mins": idle_mins,
            "first_ignition_on":  first_on,
            "last_ignition_off":  last_off,
            "odometer_available": odometer_seen,
        },
        "trips": trips,
        "unpaired_events": unpaired,
    })

@mcp.tool()
async def get_camera_events(
    action: Optional[str] = None,
    vehicle_id: Optional[int] = None,
    from_date: Optional[str] = None,
    to_date: Optional[str] = None,
) -> dict:
    """
    Fetch dashcam/camera events from TN360, optionally filtered by action type.

    This is a focused wrapper around get_events(event_types="CAMERA") that adds
    server-side-style filtering on the `action` field — allowing callers to
    request only red light violations, only fatigue alerts, etc., without
    pulling and filtering the full CAMERA event stream client-side.

    CAMERA Event Action Types
    ─────────────────────────
    TRAFFIC_LIGHT_VIOLATION     — Vehicle ran a red light or stop sign.
                                  subTypeDescription: "Red - Major" or "Red - Minor"
                                  confidence field (0.0–1.0) indicates AI certainty.

    SPEED_VIOLATION             — Vehicle exceeded the posted speed limit.
                                  speedData.speed = actual speed (km/h)
                                  speedData.speedLimit = posted limit (km/h)

    FATIGUE                     — Driver drowsiness detected by AI camera.
                                  subTypeDescription: "Drowsy"
                                  duration field = seconds of detected fatigue.

    DISTRACTION                 — Driver not looking at road.
                                  subTypeDescription examples:
                                    "Looking At Phone", "Looking Down", "Talking On Phone"
                                  confidence field indicates AI certainty.

    FOLLOWING_DISTANCE          — Tailgating / insufficient gap to vehicle ahead.
                                  subTypeDescription: "From Front" or "Other Vehicle From Right"
                                  duration = seconds of unsafe following distance.

    INTERNAL_CAMERA_OBSTRUCTION — Dashcam lens blocked or incorrectly mounted.
                                  subTypeDescription: "Inward Complete - Bad Mount"
                                  Useful for identifying cameras needing attention.

    DRIVER_INPUT                — Driver manually triggered the camera (panic button).
                                  subTypeDescription: "Driver Side Button"

    Parameters
    ──────────
    action:      Optional. One of the action type strings listed above.
                 Case-insensitive. If omitted, all CAMERA events are returned.
                 Examples:
                   "TRAFFIC_LIGHT_VIOLATION"  — red light jumps only
                   "FATIGUE"                  — drowsiness alerts only
                   "DISTRACTION"              — phone use / inattention only
                   "SPEED_VIOLATION"          — camera-detected speeding only
                   "FOLLOWING_DISTANCE"       — tailgating alerts only

    vehicle_id:  Optional. Scope to a single vehicle by TN360 vehicle ID.
                 Always provide when querying one vehicle — much faster.

    from_date:   ISO 8601 datetime string. Defaults to 24 hours ago.

    to_date:     ISO 8601 datetime string. Defaults to now.

    Response fields (per event)
    ────────────────────────────
    action              — The event action type (see above)
    location            — Human-readable address where event occurred
    timeAt              — UTC timestamp of the event
    vehicle.name        — Vehicle name (e.g. "P49")
    vehicle.registration — Rego plate
    GPS.Lat / GPS.Lng   — Position at time of event
    GPS.Spd             — Speed at time of event
    attributes.context  — JSON string with full AI analysis details including:
                            speedData.speed / speedData.speedLimit
                            details.confidence
                            details.subTypeDescription
                            details.severity / details.severityDescription
                            driver.firstName / driver.lastName
                            videos[].id — dashcam clip IDs

    Example usage
    ──────────────
    # All red light violations in the last 24 hours:
    get_camera_events(action="TRAFFIC_LIGHT_VIOLATION")

    # Fatigue alerts for a specific vehicle today:
    get_camera_events(action="FATIGUE", vehicle_id=37507,
                      from_date="2026-03-31T00:00:00+11:00",
                      to_date="2026-03-31T23:59:59+11:00")

    # All camera events for one vehicle (no action filter):
    get_camera_events(vehicle_id=319679,
                      from_date="2026-03-31T00:00:00+11:00")

    # Phone use / distraction incidents fleet-wide today:
    get_camera_events(action="DISTRACTION",
                      from_date="2026-03-31T00:00:00+11:00")
    """
    now = datetime.now(timezone.utc).replace(microsecond=0)

    if to_date:
        to_dt = datetime.fromisoformat(to_date).astimezone(timezone.utc)
    else:
        to_dt = now

    if from_date:
        from_dt = datetime.fromisoformat(from_date).astimezone(timezone.utc)
    else:
        from_dt = to_dt - timedelta(hours=24)

    params = {
        "types": "CAMERA",
        "from": from_dt.isoformat().replace("+00:00", "Z"),
        "to": to_dt.isoformat().replace("+00:00", "Z"),
        "pruning": "ALL",
    }

    if vehicle_id:
        params["vehicleId"] = vehicle_id

    raw = await _get("/events", params)

    # Client-side action filtering — TN360 API does not support action= param
    if action and isinstance(raw, dict) and "data" in raw:
        action_upper = action.strip().upper()
        raw["data"] = [
            e for e in (raw.get("data") or [])
            if (e.get("action") or "").upper() == action_upper
        ]
        # Update meta count to reflect filtered result
        if "meta" in raw and isinstance(raw["meta"], dict):
            raw["meta"]["count"] = len(raw["data"])
            raw["meta"]["filtered_by_action"] = action_upper
            raw["meta"]["note"] = (
                "action filter applied client-side — "
                "total CAMERA events fetched may be higher"
            )

    elif action and isinstance(raw, list):
        action_upper = action.strip().upper()
        raw = [e for e in raw if (e.get("action") or "").upper() == action_upper]

    return wrap_result(raw)

    
# ============================================================================ #
# SYSTEM ROUTES
# ============================================================================ #

async def health(request):
    return JSONResponse({"status": "ok"})


async def oauth_metadata(request):
    return JSONResponse({
        "issuer": "https://tn360-mcp.onrender.com",
        "response_types_supported": ["token"],
    })

# ============================================================================ #
# STARLETTE APP
# ============================================================================ #

mcp_app = mcp.http_app(path="/mcp")

app = Starlette(
    lifespan=mcp_app.lifespan,
    routes=[
        Route("/health", health),
        Route("/.well-known/oauth-authorization-server", oauth_metadata),
        Mount("/", app=mcp_app),
    ],
)
