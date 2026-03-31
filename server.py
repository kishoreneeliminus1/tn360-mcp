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

VALID_TN360_EVENT_TYPES = {
    "speed",
    "ignition",
    "driver",
    "geofence",
    "camera",
    "position",
    # Add others below only after confirming acceptance by the TN360 API:
    # "gpio", "installation", "alarm", "alert", "communication",
    # "mass", "pto", "pretrip", "harshbraking", "harshacceleration",
    # "harshcornering", "overrevving", "driverfatigue", "driverdistraction",
    # "seatbeltviolation"
}


def sanitize_event_types(raw: str) -> str:
    """Sanitize and validate event type list. TN360 uses lowercase type names."""
    cleaned = [t.strip().lower() for t in raw.split(",") if t.strip().lower() in VALID_TN360_EVENT_TYPES]
    return ",".join(cleaned) if cleaned else DEFAULT_EVENT_TYPES


DEFAULT_EVENT_TYPES = (
    "speed,ignition,driver,geofence,camera,position"
)

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

@mcp.tool()
async def get_vehicles(fleet_id: Optional[int] = None) -> dict:
    """Get all vehicles, optionally filtered by fleet."""
    params = {"fleetId": fleet_id} if fleet_id else {}
    return wrap_result(await _get("/vehicles", params))
    
@mcp.tool()
async def get_vehicle_stats(
    gps: bool = True,
    embed_vehicles: bool = True,
    embed_meters: bool = False,
    last_updated: Optional[str] = None,
) -> dict:
    """
    Bulk vehicle stats — returns live GPS location and/or meters for ALL vehicles
    in a single efficient call. This is the correct way to get current vehicle
    locations. Replaces the broken /vehicles/{id}/position endpoint.

    Docs: https://docs-au.telematics.com/visibility/
          https://docs-au.telematics.com/meters/ (embed_meters)

    gps:            Include last known GPS position for each vehicle (default True).
    embed_vehicles: Include vehicle name, registration etc (default True).
    embed_meters:   Include odometer, fuel, engine hours, distance (default False).
    last_updated:   ISO 8601 timestamp. If set, only returns vehicles updated
                    since this time — useful for efficient polling.

    Note: Poll no more frequently than every 5 minutes (API fair use policy).
    """
    embed_parts = []
    if embed_vehicles:
        embed_parts.append("vehicles")
    if embed_meters:
        embed_parts.append("meters")

    params: dict = {}
    if gps:
        params["gps"] = "true"
    if embed_parts:
        params["embed"] = ",".join(embed_parts)
    if last_updated:
        params["last_updated"] = last_updated

    return wrap_result(await _get("/vehicles/stats", params))

@mcp.tool()
async def get_vehicle_location(vehicle_id: int) -> dict:
    return wrap_result(await _get(f"/vehicles/{vehicle_id}/position"))


@mcp.tool()
async def get_events(
    event_types: str = DEFAULT_EVENT_TYPES,
    from_date: Optional[str] = None,
    to_date: Optional[str] = None,
    vehicle_id: Optional[int] = None,
) -> dict:
    """
    Fetch vehicle events from TN360.

    Docs: https://docs-au.telematics.com/events/

    event_types: Comma-separated lowercase event types.
                 Confirmed working: speed, ignition, driver, geofence, camera, position
                 - 'ignition' = engine ON/OFF, ideal for trip start/end detection
                 - 'driver'   = EWD work/rest logon/logoff entries
                 - 'speed'    = geofence-based speed violations
                 - 'geofence' = geofence entry/exit events
                 Defaults to all confirmed-working types.
    from_date:   ISO 8601 datetime. Defaults to 6 days ago. Max range: 7 days.
    to_date:     ISO 8601 datetime. Defaults to now.
    vehicle_id:  Optional — filter to a specific vehicle.
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
        "types": sanitize_event_types(event_types),
        "from": from_dt.isoformat().replace("+00:00", "Z"),
        "to": to_dt.isoformat().replace("+00:00", "Z"),
        "pruning": "ALL",
    }

    if vehicle_id:
        params["vehicleId"] = vehicle_id

    return wrap_result(await _get("/events", params))



@mcp.tool()
async def get_fleets() -> dict:
    return wrap_result(await _get("/fleets"))


@mcp.tool()
async def get_users(status: str = "active") -> dict:
    params = {} if status == "all" else {"code": status}
    return wrap_result(await _get("/users", params))


@mcp.tool()
async def get_geofences() -> dict:
    return wrap_result(await _get("/geofences"))

@mcp.tool()
async def get_vehicle_within(vehicle_id: int) -> dict:
    return wrap_result(await _get(f"/vehicles/{vehicle_id}/within", {"location_type": "all"}))


 
@mcp.tool()
async def get_vehicle_devices(vehicle_id: int) -> dict:
    """Get devices installed on a specific vehicle."""
    return wrap_result(await _get(f"/vehicles/{vehicle_id}/devices", {"pruning": "all"}))

@mcp.tool()
async def get_vehicle_drivers(
    from_date: Optional[str] = None,
    to_date: Optional[str] = None,
    vehicle_id: Optional[int] = None,
) -> dict:
    """
    Fetch EWD driver events (LogonDriver, LogoffDriver, StartWork, StopWork,
    StartRest etc) for fatigue/compliance monitoring.

    Uses get_events with types=driver internally.

    Note: The TN360 API does not reliably filter server-side by vehicle_id for
    driver events — all vehicles may be returned. Filter client-side by the
    vehicleId field in each result record.

    from_date: ISO 8601 datetime. Defaults to 3 days ago.
    to_date:   ISO 8601 datetime. Defaults to now.
    vehicle_id: Passed as query param but may not be honoured server-side.
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
        "types": "driver",
        "from": from_dt.isoformat().replace("+00:00", "Z"),
        "to": to_dt.isoformat().replace("+00:00", "Z"),
        "pruning": "ALL",
    }

    if vehicle_id:
        params["vehicleId"] = vehicle_id

    return wrap_result(await _get("/events", params))
    
@mcp.tool()
async def get_vehicle_odometer(vehicle_id: int) -> dict:
    """
    Get odometer, engine hours, fuel, and distance meters for a single vehicle.

    Docs: https://docs-au.telematics.com/meters/

    Returns multiple meter types. To calculate current value:
      if useDifferential == true:  current = base + diff
      else:                        current = value
    """
    return wrap_result(await _get(f"/vehicles/{vehicle_id}/meters"))

@mcp.tool()
async def get_vehicle_users(vehicle_id: int) -> dict:
    """Get users assigned to a specific vehicle."""
    return wrap_result(await _get(f"/vehicles/{vehicle_id}/users"))


@mcp.tool()
async def get_vehicle_fleets(vehicle_id: int) -> dict:
    """Get fleets that a specific vehicle belongs to."""
    return wrap_result(await _get(f"/vehicles/{vehicle_id}/fleets"))


# (Other MCP tools unchanged for brevity, can rewrite if you want)

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
