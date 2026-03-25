"""
TN360 MCP Server
Exposes Teletrac Navman TN360 fleet telematics data to Claude via MCP.
"""
import os
import httpx
from datetime import datetime, timedelta
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
    return await _get(f"/vehicles/{vehicle_id}/position")

@mcp.tool()
async def get_events(
    event_types: str = "ignition,speeding,harsh_braking",
    hours_back: int = 24,
    vehicle_id: Optional[int] = None,
) -> dict:
    """Retrieve fleet events for a time window."""
    hours_back = min(hours_back, 168)
    from_dt = (datetime.utcnow() - timedelta(hours=hours_back)).strftime("%Y-%m-%d")
    to_dt   = datetime.utcnow().strftime("%Y-%m-%d")
    params: dict = {"types": event_types, "from": from_dt, "to": to_dt}
    if vehicle_id:
        params["vehicleId"] = vehicle_id
    data = await _get("/events", params)
    return {"events": data, "count": len(data) if isinstance(data, list) else None}

@mcp.tool()
async def get_fleets() -> dict:
    """List all virtual fleet groups in the TN360 account."""
    return await _get("/fleets")

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
    from_dt = (datetime.utcnow() - timedelta(days=days_back)).strftime("%Y-%m-%d")
    to_dt   = datetime.utcnow().strftime("%Y-%m-%d")
    data    = await _get(f"/vehicles/{vehicle_id}/trips", {"from": from_dt, "to": to_dt})
    return {"trips": data, "vehicle_id": vehicle_id}

@mcp.tool()
async def get_geofences() -> dict:
    """List all geofences configured in the TN360 account."""
    return await _get("/geofences")

@mcp.tool()
async def get_vehicle_odometer(vehicle_id: int) -> dict:
    """Get the current odometer reading for a vehicle."""
    return await _get(f"/vehicles/{vehicle_id}/odometer")

# ── App setup ─────────────────────────────────────────────────────────────────

async def health(request):
    return JSONResponse({"status": "ok"})

mcp_app = mcp.http_app(path="/")

app = Starlette(
    lifespan=mcp_app.lifespan,
    routes=[
        Route("/health", health),
        Mount("/mcp/", app=mcp_app),
    ],
)
