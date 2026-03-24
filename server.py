"""
TN360 MCP Server
Exposes Teletrac Navman TN360 fleet telematics data to Claude via MCP.
Deploy on Render using Streamable HTTP transport.
"""

import os
import httpx
from datetime import datetime, timedelta
from typing import Optional
from fastmcp import FastMCP

# ── Server setup ──────────────────────────────────────────────────────────────
mcp = FastMCP(
    "TN360 Fleet Server",
    stateless_http=True,
    json_response=True,
)

TN360_BASE_URL = os.environ.get("TN360_BASE_URL", "https://api-au.telematics.com")
TN360_API_KEY  = os.environ.get("TN360_API_KEY", "")   # Set in Render env vars


def _headers() -> dict:
    """Return auth headers for every TN360 request."""
    if not TN360_API_KEY:
        raise RuntimeError("TN360_API_KEY environment variable is not set.")
    return {
        "Authorization": f"Bearer {TN360_API_KEY}",
        "Accept": "application/json",
        "Content-Type": "application/json",
    }


async def _get(path: str, params: dict | None = None) -> dict | list:
    """Shared async GET helper with error handling."""
    url = f"{TN360_BASE_URL}/v1{path}"
    async with httpx.AsyncClient(timeout=15) as client:
        r = await client.get(url, headers=_headers(), params=params or {})
        r.raise_for_status()
        return r.json()


# ── Tools ─────────────────────────────────────────────────────────────────────

@mcp.tool()
async def get_vehicles(fleet_id: Optional[int] = None) -> dict:
    """
    List all vehicles in the TN360 account.

    Args:
        fleet_id: Optional fleet ID to filter vehicles by fleet.

    Returns:
        JSON list of vehicle objects with id, name, registration, status.
    """
    params = {}
    if fleet_id:
        params["fleetId"] = fleet_id
    data = await _get("/vehicles", params)
    return {"vehicles": data, "count": len(data) if isinstance(data, list) else None}


@mcp.tool()
async def get_vehicle_location(vehicle_id: int) -> dict:
    """
    Get the current GPS location and status of a specific vehicle.

    Args:
        vehicle_id: The TN360 numeric vehicle ID.

    Returns:
        Vehicle position including lat, lng, speed, heading, and address.
    """
    data = await _get(f"/vehicles/{vehicle_id}/position")
    return data


@mcp.tool()
async def get_events(
    event_types: str = "ignition,speeding,harsh_braking",
    hours_back: int = 24,
    vehicle_id: Optional[int] = None,
) -> dict:
    """
    Retrieve fleet events (ignition, speeding, harsh braking, etc.) for a time window.

    Args:
        event_types: Comma-separated event types. Common values:
                     ignition, driver, speeding, harsh_braking, geofence, fatigue.
        hours_back:  How many hours back from now to query (max 168 = 7 days).
        vehicle_id:  Filter to a single vehicle (optional).

    Returns:
        List of event objects with type, action, location, timestamp, GPS coords.
    """
    hours_back = min(hours_back, 168)  # API cap: 7 days
    from_dt = (datetime.utcnow() - timedelta(hours=hours_back)).strftime("%Y-%m-%d")
    to_dt   = datetime.utcnow().strftime("%Y-%m-%d")

    params: dict = {"types": event_types, "from": from_dt, "to": to_dt}
    if vehicle_id:
        params["vehicleId"] = vehicle_id

    data = await _get("/events", params)
    return {"events": data, "count": len(data) if isinstance(data, list) else None}


@mcp.tool()
async def get_fleets() -> dict:
    """
    List all virtual fleet groups in the TN360 account.

    Returns:
        List of fleet objects with id, name, and vehicle counts.
    """
    data = await _get("/fleets")
    return {"fleets": data}


@mcp.tool()
async def get_drivers(status: str = "active") -> dict:
    """
    List drivers registered in the TN360 platform.

    Args:
        status: Filter by driver status — 'active', 'inactive', or 'all'.

    Returns:
        List of driver objects with id, name, licence, and current vehicle.
    """
    params = {} if status == "all" else {"status": status}
    data = await _get("/drivers", params)
    return {"drivers": data, "count": len(data) if isinstance(data, list) else None}


@mcp.tool()
async def get_trips(
    vehicle_id: int,
    days_back: int = 7,
) -> dict:
    """
    Retrieve completed trip history for a vehicle.

    Args:
        vehicle_id: The TN360 numeric vehicle ID.
        days_back:  Number of days of history to fetch (max 30).

    Returns:
        List of trip summaries including start/end locations, distance, and duration.
    """
    days_back = min(days_back, 30)
    from_dt = (datetime.utcnow() - timedelta(days=days_back)).strftime("%Y-%m-%d")
    to_dt   = datetime.utcnow().strftime("%Y-%m-%d")
    params  = {"from": from_dt, "to": to_dt}
    data    = await _get(f"/vehicles/{vehicle_id}/trips", params)
    return {"trips": data, "vehicle_id": vehicle_id}


@mcp.tool()
async def get_geofences() -> dict:
    """
    List all geofences configured in the TN360 account.

    Returns:
        List of geofence objects with id, name, coordinates, and alert settings.
    """
    data = await _get("/geofences")
    return {"geofences": data}


@mcp.tool()
async def get_vehicle_odometer(vehicle_id: int) -> dict:
    """
    Get the current odometer reading for a vehicle.

    Args:
        vehicle_id: The TN360 numeric vehicle ID.

    Returns:
        Odometer value in kilometres and the timestamp it was recorded.
    """
    data = await _get(f"/vehicles/{vehicle_id}/odometer")
    return data


# ── Health endpoint + run ─────────────────────────────────────────────────────

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    mcp.run(transport="streamable-http", host="0.0.0.0", port=port)
