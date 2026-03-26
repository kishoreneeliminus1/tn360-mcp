"""
TN360 MCP Server
Exposes Teletrac Navman TN360 fleet telematics data to Claude via MCP.
"""
import os
import httpx
import asyncio
from datetime import datetime, timedelta, timezone
from typing import Optional
from fastmcp import FastMCP
from starlette.applications import Starlette
from starlette.routing import Route, Mount
from starlette.responses import JSONResponse

mcp = FastMCP("TN360 Fleet Server")

# --------------------------------------------------------------------------- #
# TN360 API CONFIG
# --------------------------------------------------------------------------- #

TN360_BASE_URL = os.environ.get("TN360_BASE_URL", "https://api-au.telematics.com")
TN360_API_KEY  = os.environ.get("TN360_API_KEY", "")

def _headers() -> dict:
    if not TN360_API_KEY:
        raise RuntimeError("TN360_API_KEY environment variable is not set.")
    return {
        "Authorization": f"Bearer {TN360_API_KEY}",
        "Accept": "application/json",
        "Content-Type": "application/json",
    }

# --------------------------------------------------------------------------- #
# VALID EVENT TYPES (TN360 AU — verified from Events.yaml and docs)
# --------------------------------------------------------------------------- #
VALID_TN360_EVENT_TYPES = {
    # Core & common
    "ignition", "speed", "position", "geofence", "camera",
    "gpio", "installation", "alarm", "alert", "communication",
    "mass", "pto", "pretrip",

    # Behavioural (enabled per tenant)
    "harshBraking", "harshAcceleration", "harshCornering",
    "overRevving", "driverFatigue", "driverDistraction",
    "seatbeltViolation",
}

def sanitize_event_types(raw: str) -> str:
    """Return only valid, AU‑approved event types."""
    cleaned = []
    for t in raw.split(","):
        t = t.strip()
        if t in VALID_TN360_EVENT_TYPES:
            cleaned.append(t)
    return ",".join(cleaned)

# --------------------------------------------------------------------------- #
# Robust HTTP GET with retry + long timeout + error handling
# --------------------------------------------------------------------------- #

async def _get(path: str, params: dict | None = None) -> dict | list:
    url = f"{TN360_BASE_URL}/v1{path}"

    timeout = httpx.Timeout(60.0)
    params = params or {}

    # Retry loop to handle read timeouts, TN360 slowness, transient network issues
    for attempt in range(3):
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                r = await client.get(url, headers=_headers(), params=params)
                r.raise_for_status()
                return r.json()

        except httpx.ReadTimeout:
            if attempt == 2:
                raise
            await asyncio.sleep(2.0 * (attempt + 1))

        except httpx.HTTPStatusError:
            # Pass through HTTP errors (400, 401, 403, 500, etc.)
            raise

# --------------------------------------------------------------------------- #
# MCP Tools
# --------------------------------------------------------------------------- #

@mcp.tool()
async def get_vehicles(fleet_id: Optional[int] = None) -> dict:
    """List all vehicles in the TN360 account."""
    params = {"fleetId": fleet_id} if fleet_id else {}
    data = await _get("/vehicles", params)
    return {"vehicles": data, "count": len(data) if isinstance(data, list) else None}

@mcp.tool()
async def get_vehicle_location(vehicle_id: int) -> dict:
    """Get the current GPS location and status of a specific vehicle."""
    try:
        return await _get(f"/vehicles/{vehicle_id}/position")
    except httpx.HTTPStatusError as e:
        return {"error": str(e), "vehicle_id": vehicle_id}

@mcp.tool()
async def get_events(
    event_types: str = (
        "ignition,speed,position,geofence,camera,gpio,installation,alarm,alert,"
        "communication,mass,pto,pretrip,harshBraking,harshAcceleration,"
        "harshCornering,overRevving,driverFatigue,driverDistraction,seatbeltViolation"
    ),
    hours_back: int = 24,
    vehicle_id: Optional[int] = None,
) -> dict:
    """Retrieve fleet events with full ISO8601 date+time window."""

    # Clamp to TN360 max (7 days)
    hours_back = min(hours_back, 168)

    now = datetime.now(timezone.utc)
    from_dt = (now - timedelta(hours=hours_back)).isoformat().replace("+00:00", "Z")
    to_dt   = now.isoformat().replace("+00:00", "Z")

    # Clean invalid event types to avoid 400/417 errors
    filtered_types = sanitize_event_types(event_types)

    params = {
        "types": filtered_types,
        "from": from_dt,
        "to": to_dt,
        "pruning": "ALL"
    }

    if vehicle_id:
        params["vehicleId"] = vehicle_id

    data = await _get("/events", params)

    return {
        "events": data,
        "count": len(data) if isinstance(data, list) else None
    }

@mcp.tool()
async def get_fleets() -> dict:
    """List all virtual fleet groups in the TN360 account."""
    data = await _get("/fleets")
    return {"fleets": data, "count": len(data) if isinstance(data, list) else None}

@mcp.tool()
async def get_drivers(status: str = "active") -> dict:
    """List drivers registered in the TN360 platform."""
    params = {} if status == "all" else {"status": status}
    data = await _get("/drivers", params)
    return {"drivers": data, "count": len(data) if isinstance(data, list) else None}

@mcp.tool()
async def get_trips(vehicle_id: int, days_back: int = 7) -> dict:
    """Retrieve completed trip history for a vehicle."""
    days_back = min(days_back, 30)
    now = datetime.now(timezone.utc)
    from_dt = (now - timedelta(days=days_back)).strftime("%Y-%m-%d")
    to_dt   = now.strftime("%Y-%m-%d")
    data    = await _get(f"/vehicles/{vehicle_id}/trips", {"from": from_dt, "to": to_dt})
    return {"trips": data, "vehicle_id": vehicle_id}

@mcp.tool()
async def get_geofences() -> dict:
    """List all geofences configured in the TN360 account."""
    data = await _get("/geofences")
    return {"geofences": data, "count": len(data) if isinstance(data, list) else None}

@mcp.tool()
async def get_vehicle_odometer(vehicle_id: int) -> dict:
    """Get the current odometer reading for a vehicle."""
    try:
        return await _get(f"/vehicles/{vehicle_id}/odometer")
    except httpx.HTTPStatusError as e:
        return {"error": str(e), "vehicle_id": vehicle_id}

# --------------------------------------------------------------------------- #
# HEALTH + OAUTH
# --------------------------------------------------------------------------- #

async def health(request):
    return JSONResponse({"status": "ok"})

async def oauth_metadata(request):
    return JSONResponse({
        "issuer": "https://tn360-mcp.onrender.com",
        "response_types_supported": ["token"],
    })

# --------------------------------------------------------------------------- #
# APP INITIALISATION
# --------------------------------------------------------------------------- #

mcp_app = mcp.http_app(path="/mcp")

app = Starlette(
    lifespan=mcp_app.lifespan,
    routes=[
        Route("/health", health),
        Route("/.well-known/oauth-authorization-server", oauth_metadata),
        Mount("/", app=mcp_app),
    ],
)
