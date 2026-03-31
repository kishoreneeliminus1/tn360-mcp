# ============================================================================ #
# TN360 MCP Server – Fully Integrated with DashCam Video Support
# ============================================================================ #

import os
import math
import httpx
import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional, Any
from zoneinfo import ZoneInfo

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

# FIX #8: Fail fast at startup if API key is missing — don't wait for first request.
if not TN360_API_KEY:
    raise RuntimeError("TN360_API_KEY environment variable is not set.")

# FIX #6: Use proper timezone-aware formatting instead of hardcoded UTC+11 offset.
# This correctly handles both AEST (UTC+10) and AEDT (UTC+11) daylight saving transitions.
AUSTRALIA_TZ = ZoneInfo("Australia/Sydney")


def _headers() -> dict:
    """Generate headers for TN360 HTTP requests."""
    return {
        "Authorization": f"Bearer {TN360_API_KEY}",
        "Accept": "application/json",
        "Content-Type": "application/json",
        "Expect": ""  # Prevent 417 Expectation Failed
    }

# ============================================================================ #
# FIX #1: PERSISTENT HTTP CLIENT
# Reuse a single AsyncClient across all requests instead of creating/tearing
# down a new one per call. Dramatically reduces connection overhead.
# ============================================================================ #

_client: Optional[httpx.AsyncClient] = None


async def get_client() -> httpx.AsyncClient:
    """Return the shared AsyncClient, creating it if needed."""
    global _client
    if _client is None or _client.is_closed:
        _client = httpx.AsyncClient(timeout=httpx.Timeout(60.0))
    return _client

# ============================================================================ #
# EVENT TYPE FILTERING
# ============================================================================ #

VALID_TN360_EVENT_TYPES = {
    "SPEED",
    "CAMERA",
    "GEOFENCE",
    "DRIVER",
    "IGNITION",
    "IOR",
    "POSITION",
    "ALARM",
    "PRETRIP",
    "TRIP",
}

DEFAULT_EVENT_TYPES = "SPEED,CAMERA,GEOFENCE,DRIVER"


def sanitize_event_types(raw: str) -> str:
    """Sanitize and validate event type list against confirmed-working types."""
    cleaned = [t.strip().upper() for t in raw.split(",") if t.strip().upper() in VALID_TN360_EVENT_TYPES]
    return ",".join(cleaned) if cleaned else DEFAULT_EVENT_TYPES

# ============================================================================ #
# UNIVERSAL SAFE WRAPPER
# ============================================================================ #

def wrap_result(raw: Any) -> dict:
    """
    Normalize MCP response output format.

    FIX #3: Check for 'data' key before 'error' key so that partial-error
    responses (containing both) don't silently drop the data payload.
    """
    if isinstance(raw, dict):
        # Check 'data' first — may coexist with 'error' in partial responses
        if "data" in raw:
            return {
                "success": "error" not in raw,
                "data": raw.get("data"),
                "error": raw.get("error"),
                "meta": raw.get("meta", {})
            }
        if "error" in raw:
            return {
                "success": False,
                "data": None,
                "error": raw.get("error"),
                "meta": {k: v for k, v in raw.items() if k != "error"}
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
# FIX #2: SHARED _request() HELPER
# Replaces the three separate _get / _post / _put wrappers that each
# duplicated retry logic. All HTTP methods now go through one place.
# _put gains retry logic it previously lacked (Fix #2 + original Fix for _put).
# ============================================================================ #

async def _request(method: str, path: str, **kwargs) -> dict | list:
    """
    Shared HTTP request helper with logging and 3-attempt retry.
    Supports GET, POST, PUT (and any other httpx method).
    """
    url = f"{TN360_BASE_URL}/v1{path}"

    logging.info(f"[HTTP {method}] URL={url}")
    if "params" in kwargs:
        logging.info(f"[HTTP {method}] PARAMS={kwargs['params']}")
    if "json" in kwargs:
        logging.info(f"[HTTP {method}] PAYLOAD={kwargs['json']}")

    for attempt in range(3):
        try:
            client = await get_client()
            r = await client.request(method, url, headers=_headers(), **kwargs)

            logging.info(f"[HTTP {method}] STATUS={r.status_code}")
            logging.debug(f"[HTTP {method}] RESPONSE={r.text[:400]}")

            if 200 <= r.status_code < 300:
                try:
                    return r.json()
                except Exception:
                    return {"error": "Invalid JSON from TN360", "response_text": r.text}

            return {"error": f"HTTP {r.status_code}", "response": r.text}

        except Exception as e:
            logging.error(f"[HTTP {method}] ERROR (attempt {attempt + 1}): {e}")
            if attempt == 2:
                return {"error": str(e)}
            await asyncio.sleep(1.5 * (attempt + 1))


# Convenience wrappers — keep call sites clean
async def _get(path: str, params: Optional[dict] = None) -> dict | list:
    return await _request("GET", path, params=params or {})


async def _post(path: str, payload: dict) -> dict:
    return await _request("POST", path, json=payload)


async def _put(path: str, payload: dict) -> dict:
    return await _request("PUT", path, json=payload)

# ============================================================================ #
# MCP TOOLS
# ============================================================================ #

@mcp.tool()
async def get_vehicles(fleet_id: Optional[int] = None) -> dict:
    """
    Fetch all vehicles in the fleet, optionally filtered by fleet_id.
    Returns vehicle name, registration, type, status and associated IDs.
    """
    params = {"fleetId": fleet_id} if fleet_id is not None else {}
    return wrap_result(await _get("/vehicles", params))


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
    """
    params: dict = {"gps": "true"}

    if embed_vehicles:
        params["embed"] = "vehicles"

    if last_updated:
        try:
            dt = datetime.fromisoformat(last_updated.replace("Z", "+00:00"))
            params["last_updated"] = dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
        except ValueError:
            params["last_updated"] = last_updated

    # FIX #4: Use `is not None` to correctly handle vehicle_id=0
    if vehicle_id is not None:
        params["vehicleId"] = vehicle_id

    return wrap_result(await _get("/vehicles/stats", params))


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
    stats window).

    Parameters
    ──────────
    vehicle_id:   Required. The TN360 vehicle ID (integer).
    hours_back:   How many hours of history to search. Default 6. Max 168 (7 days).

    Response
    ────────
    Returns GEOFENCE events sorted most-recent-first. Each event includes:
      GPS.Lat / GPS.Lng   — position at time of geofence trigger
      GPS.Spd             — speed at trigger
      location            — human-readable address string
      timeAt              — UTC timestamp of the event
      action              — GEO-EN (entered) or GEO-EX (exited)
    """
    hours_back = max(1, min(hours_back, 168))

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
    raw = await _get("/events", params)

    # FIX #7: Sort events most-recent-first as the docstring promises.
    if isinstance(raw, dict) and "data" in raw and isinstance(raw["data"], list):
        raw["data"].sort(key=lambda e: e.get("timeAt", ""), reverse=True)
    elif isinstance(raw, list):
        raw.sort(key=lambda e: e.get("timeAt", ""), reverse=True)

    return wrap_result(raw)


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

    from_date:   ISO 8601 datetime string. Defaults to 24 hours ago.
    to_date:     ISO 8601 datetime string. Defaults to now.
    vehicle_id:  Optional — filter to a specific vehicle.

    Tips for faster results
    ────────────────────────
    • Always supply vehicle_id when you only need one vehicle's data.
    • Use the most specific event_types list possible.
    • For last-known location, prefer get_vehicle_stats() or get_vehicle_location().
    • Keep date ranges as tight as possible.
    """
    now = datetime.now(timezone.utc).replace(microsecond=0)

    to_dt = datetime.fromisoformat(to_date).astimezone(timezone.utc) if to_date else now
    from_dt = datetime.fromisoformat(from_date).astimezone(timezone.utc) if from_date else to_dt - timedelta(hours=24)

    params = {
        "types": sanitize_event_types(event_types),
        "from": from_dt.isoformat().replace("+00:00", "Z"),
        "to": to_dt.isoformat().replace("+00:00", "Z"),
        "pruning": "ALL",
    }

    # FIX #4: Use `is not None` check
    if vehicle_id is not None:
        params["vehicleId"] = vehicle_id

    return wrap_result(await _get("/events", params))


@mcp.tool()
async def get_fleets() -> dict:
    """Fetch all fleet groups defined in the TN360 account."""
    return wrap_result(await _get("/fleets"))


@mcp.tool()
async def get_users(status: str = "active") -> dict:
    """Fetch TN360 users. status='active' (default) or 'all'."""
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
    and subtracting first-ON odometer from last-OFF odometer.
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

    to_dt = datetime.fromisoformat(to_date).astimezone(timezone.utc) if to_date else now
    from_dt = datetime.fromisoformat(from_date).astimezone(timezone.utc) if from_date else to_dt - timedelta(days=3)

    params = {
        "types": "DRIVER",
        "from": from_dt.isoformat().replace("+00:00", "Z"),
        "to": to_dt.isoformat().replace("+00:00", "Z"),
        "pruning": "ALL",
    }

    # FIX #4: Use `is not None` check
    if vehicle_id is not None:
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
    from_date:    ISO 8601 datetime string. Defaults to start of current day (local midnight).
    to_date:      ISO 8601 datetime string. Defaults to now.
    """
    # FIX #5: math and zoneinfo are now top-level imports — removed from inside function.

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

    def fmt_local(dt_utc: datetime) -> str:
        """
        FIX #6: Format UTC datetime as local time using proper timezone-aware
        conversion. Correctly reflects AEST (UTC+10) or AEDT (UTC+11) depending
        on the time of year — no more hardcoded +11 offset.
        """
        local = dt_utc.astimezone(AUSTRALIA_TZ)
        tz_name = local.strftime("%Z")  # "AEST" or "AEDT"
        return local.strftime(f"%H:%M {tz_name}")

    # ── Date range ────────────────────────────────────────────────────────────
    now = datetime.now(timezone.utc).replace(microsecond=0)

    to_dt = parse_dt(to_date) if to_date else now
    if from_date:
        from_dt = parse_dt(from_date)
    else:
        # Default: start of today in local time (handles DST correctly)
        today_local = datetime.now(AUSTRALIA_TZ).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        from_dt = today_local.astimezone(timezone.utc)

    # ── Fetch IGNITION events ─────────────────────────────────────────────────
    params = {
        "types": "IGNITION",
        "from": from_dt.isoformat().replace("+00:00", "Z"),
        "to": to_dt.isoformat().replace("+00:00", "Z"),
        "vehicleId": vehicle_id,
        "pruning": "ALL",
    }

    raw = await _get("/events", params)

    if isinstance(raw, dict) and "error" in raw:
        return wrap_result(raw)

    events = raw.get("data", []) if isinstance(raw, dict) else raw
    if not isinstance(events, list):
        return wrap_result({"error": "Unexpected response format from /events", "raw": str(raw)[:200]})

    events = [e for e in events if e.get("vehicleId") == vehicle_id]
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
            off_ev = None
            for j in range(i + 1, len(events)):
                if (events[j].get("action") or "").upper() == "OFF":
                    off_ev = events[j]
                    i = j
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
                    "ignition_on": ev["timeAt"],
                    "ignition_off": off_ev["timeAt"],
                    "ignition_on_local": fmt_local(on_dt),
                    "ignition_off_local": fmt_local(off_dt),
                    "start_location": on_loc,
                    "end_location": off_loc,
                    "duration_mins": duration_mins,
                    "distance_km": distance_km,
                    "distance_source": distance_source,
                    "odometer_start": on_odo,
                    "odometer_end": off_odo,
                    "start_gps": {"lat": on_gps.get("Lat"), "lng": on_gps.get("Lng")},
                    "end_gps": {"lat": off_gps.get("Lat"), "lng": off_gps.get("Lng")},
                    "possible_idle": duration_mins < 2,
                })
            else:
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
            "first_ignition_on": first_on,
            "last_ignition_off": last_off,
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

    CAMERA Event Action Types
    ─────────────────────────
    TRAFFIC_LIGHT_VIOLATION     — Vehicle ran a red light or stop sign.
    SPEED_VIOLATION             — Vehicle exceeded the posted speed limit.
    FATIGUE                     — Driver drowsiness detected by AI camera.
    DISTRACTION                 — Driver not looking at road.
    FOLLOWING_DISTANCE          — Tailgating / insufficient gap to vehicle ahead.
    INTERNAL_CAMERA_OBSTRUCTION — Dashcam lens blocked or incorrectly mounted.
    DRIVER_INPUT                — Driver manually triggered the camera (panic button).

    Parameters
    ──────────
    action:      Optional. One of the action type strings above. Case-insensitive.
                 If omitted, all CAMERA events are returned.
    vehicle_id:  Optional. Scope to a single vehicle by TN360 vehicle ID.
    from_date:   ISO 8601 datetime string. Defaults to 24 hours ago.
    to_date:     ISO 8601 datetime string. Defaults to now.
    """
    now = datetime.now(timezone.utc).replace(microsecond=0)

    to_dt = datetime.fromisoformat(to_date).astimezone(timezone.utc) if to_date else now
    from_dt = datetime.fromisoformat(from_date).astimezone(timezone.utc) if from_date else to_dt - timedelta(hours=24)

    params = {
        "types": "CAMERA",
        "from": from_dt.isoformat().replace("+00:00", "Z"),
        "to": to_dt.isoformat().replace("+00:00", "Z"),
        "pruning": "ALL",
    }

    # FIX #4: Use `is not None` check
    if vehicle_id is not None:
        params["vehicleId"] = vehicle_id

    raw = await _get("/events", params)

    # Client-side action filtering — TN360 API does not support action= param
    if action and isinstance(raw, dict) and "data" in raw:
        action_upper = action.strip().upper()
        raw["data"] = [
            e for e in (raw.get("data") or [])
            if (e.get("action") or "").upper() == action_upper
        ]
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


# FIX #9: oauth_metadata now returns a more complete OAuth 2.0 authorization
# server metadata document. Stub fields are included so real OAuth clients
# don't fail on missing required fields. Update these URLs if you add real
# OAuth support — or remove this route entirely if it's not being used.
async def oauth_metadata(request):
    base = "https://tn360-mcp.onrender.com"
    return JSONResponse({
        "issuer": base,
        "authorization_endpoint": f"{base}/oauth/authorize",
        "token_endpoint": f"{base}/oauth/token",
        "response_types_supported": ["token"],
        "grant_types_supported": ["client_credentials"],
        "token_endpoint_auth_methods_supported": ["client_secret_post"],
        "scopes_supported": ["read"],
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
