"""
TN360 MCP Server
Exposes Teletrac Navman TN360 fleet telematics data to Claude via MCP.
"""
import os
import httpx
from datetime import datetime, timedelta, timezone
from typing import Optional
from fastmcp import FastMCP
from starlette.applications import Starlette
from starlette.routing import Route, Mount
from starlette.responses import JSONResponse

mcp = FastMCP("TN360 Fleet Server")

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


async def _get(path: str, params: dict | None = None) -> dict | list:
    url = f"{TN360_BASE_URL}/v1{path}"
    async with httpx.AsyncClient(timeout=15) as client:
        r = await client.get(url, headers=_headers(), params=params or {})
        r.raise_for_status()
        return r.json()


# ── MCP Tools ─────────────────────────────────────────────────────────────────

@mcp.tool()
async def get_vehicles(fleet_id: Optional[int] = None) -> dict:
    """List all vehicles in the TN360 account."""
    params = {}
    if fleet_id:
        params["fleetId"] = fleet_id
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
    event_types: str = "alarm,alert,camera,communication,driver,engine_management,fatigue,form,geofence,gpio,ignition,installation,job,mass,movement,position,pretrip,pto,runsheet,speed,summary,trip,vpm",
    hours_back: int = 24,
    vehicle_id: Optional[int] = None,
) -> dict:
    """Retrieve fleet events with full ISO8601 date+time window."""
    
    hours_back = min(hours_back, 168)  # safety cap: 7 days
    
    now = datetime.now(timezone.utc)
    from_dt = (now - timedelta(hours=hours_back)).isoformat().replace("+00:00", "Z")
    to_dt   = now.isoformat().replace("+00:00", "Z")

    params: dict = {
        "types": event_types,
        "from": from_dt,
        "to": to_dt
    }
    
    if vehicle_id:
        params["vehicleId"] = vehicle_id   # keep same key unless API requires plural
    
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


# ── App setup ─────────────────────────────────────────────────────────────────

async def health(request):
    return JSONResponse({"status": "ok"})


async def oauth_metadata(request):
    """OAuth 2.0 authorization server metadata (RFC 8414).
    Required to silence 404 errors from MCP clients probing this endpoint.
    """
    return JSONResponse({
        "issuer": "https://tn360-mcp.onrender.com",
        "response_types_supported": ["token"],
    })


# FIX: path="/mcp" on http_app + Mount("/") eliminates the 307 redirect
# that occurred when Mount("/mcp") caused Starlette to redirect POST /mcp → /mcp/
mcp_app = mcp.http_app(path="/mcp")

app = Starlette(
    lifespan=mcp_app.lifespan,
    routes=[
        Route("/health", health),
        Route("/.well-known/oauth-authorization-server", oauth_metadata),
        Mount("/", app=mcp_app),
    ],
)
