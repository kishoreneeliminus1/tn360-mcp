"""
TN360 MCP Server
Exposes Teletrac Navman TN360 fleet telematics data to Claude via MCP.
"""

import os
import httpx
import asyncio
from datetime import datetime, timedelta, timezone
from typing import Optional, Any

from fastmcp import FastMCP
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Route, Mount


mcp = FastMCP("TN360 Fleet Server")


# =========================================================================== #
# CONFIG
# =========================================================================== #

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


# =========================================================================== #
# VALID EVENT TYPES (AU Region)
# =========================================================================== #
VALID_TN360_EVENT_TYPES = {
    # Core + common
    "ignition", "speed", "position", "geofence", "camera",
    "gpio", "installation", "alarm", "alert", "communication",
    "mass", "pto", "pretrip",

    # Behavioural (may depend on tenant capabilities)
    "harshBraking", "harshAcceleration", "harshCornering",
    "overRevving", "driverFatigue", "driverDistraction",
    "seatbeltViolation",
}


def sanitize_event_types(raw: str) -> str:
    """Validate and return only allowed AU event types."""
    cleaned = []
    for t in raw.split(","):
        t = t.strip()
        if t in VALID_TN360_EVENT_TYPES:
            cleaned.append(t)
    return ",".join(cleaned)


DEFAULT_EVENT_TYPES = (
    "ignition,speed,position,geofence,camera,gpio,installation,alarm,alert,"
    "communication,mass,pto,pretrip,harshBraking,harshAcceleration,"
    "harshCornering,overRevving,driverFatigue,driverDistraction,seatbeltViolation"
)


# =========================================================================== #
# CENTRAL SMART GET HANDLER (Option A)
# =========================================================================== #

async def _get(path: str, params: dict | None = None) -> dict | list:
    """
    Single smart handler for ALL TN360 endpoints:
    - Retries on timeouts
    - Handles all HTTP status codes
    - Always returns structured JSON
    - Never raises uncaught exceptions (MCP safe)
    """

    url = f"{TN360_BASE_URL}/v1{path}"
    timeout = httpx.Timeout(60.0)
    params = params or {}

    for attempt in range(3):
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                r = await client.get(url, headers=_headers(), params=params)

                # ------------------------
                # SUCCESS
                # ------------------------
                if 200 <= r.status_code < 300:
                    try:
                        return r.json()
                    except Exception:
                        return {
                            "error": "Invalid JSON from TN360",
                            "response_text": r.text,
                            "url": str(r.url)
                        }

                # ------------------------
                # SMART HANDLING FOR KNOWN TN360 RESPONSES
                # ------------------------

                if r.status_code == 400:
                    return {
                        "error": "400 Bad Request – The request parameters were invalid.",
                        "url": str(r.url),
                        "params": params,
                        "response": r.text,
                    }

                if r.status_code == 401:
                    return {
                        "error": "401 Unauthorized – Invalid API Key or expired credential.",
                        "url": str(r.url),
                    }

                if r.status_code == 403:
                    return {
                        "error": "403 Forbidden – No permission for this resource.",
                        "url": str(r.url),
                    }

                if r.status_code == 404:
                    return {
                        "note": "No data found for this request.",
                        "url": str(r.url),
                        "params": params,
                        "data": [],
                    }

                if r.status_code == 409:
                    return {
                        "error": "409 Conflict – The requested resource is in conflict.",
                        "url": str(r.url),
                        "params": params,
                    }

                if r.status_code == 423:
                    return {
                        "error": "423 Locked – The requested TN360 entity is locked.",
                        "url": str(r.url),
                    }

                if r.status_code == 429:
                    return {
                        "error": "429 Rate Limited – Too many requests.",
                        "retry_after": r.headers.get("Retry-After"),
                    }

                if r.status_code >= 500:
                    return {
                        "error": f"TN360 Server Error ({r.status_code})",
                        "url": str(r.url),
                        "response": r.text,
                    }

                # Unknown status
                return {
                    "error": f"Unexpected HTTP status {r.status_code}",
                    "response": r.text,
                    "url": str(r.url)
                }

        except httpx.ReadTimeout:
            if attempt == 2:
                return {
                    "error": "ReadTimeout – TN360 did not respond in time.",
                    "url": url,
                    "params": params,
                }
            await asyncio.sleep(1.5 * (attempt + 1))

        except httpx.ConnectError:
            return {
                "error": "Connection error – could not reach TN360.",
                "url": url,
            }

        except Exception as e:
            return {
                "error": f"Unexpected exception: {str(e)}",
                "url": url,
                "params": params,
            }


# =========================================================================== #
# MCP Tools
# =========================================================================== #

@mcp.tool()
async def get_vehicles(fleet_id: Optional[int] = None) -> dict:
    params = {"fleetId": fleet_id} if fleet_id else {}
    data = await _get("/vehicles", params)
    return {"vehicles": data, "count": len(data) if isinstance(data, list) else None}


@mcp.tool()
async def get_vehicle_location(vehicle_id: int) -> dict:
    return await _get(f"/vehicles/{vehicle_id}/position")


@mcp.tool()
async def get_events(
    event_types: str = DEFAULT_EVENT_TYPES,
    hours_back: int = 24,
    vehicle_id: Optional[int] = None,
) -> dict:

    hours_back = min(hours_back, 168)

    now = datetime.now(timezone.utc).replace(microsecond=0)
    start = (now - timedelta(hours=hours_back)).replace(microsecond=0)

    from_dt = start.isoformat().replace("+00:00", "Z")
    to_dt   = now.isoformat().replace("+00:00", "Z")

    filtered_types = sanitize_event_types(event_types)

    # Safety fallback: always have valid event types
    if not filtered_types:
        filtered_types = "ignition,speed"

    params = {
        "types": filtered_types,
        "from": from_dt,
        "to": to_dt,
        "pruning": "ALL",
    }

    if vehicle_id:
        params["vehicleId"] = vehicle_id

    data = await _get("/events", params)
    return {"events": data, "count": len(data) if isinstance(data, list) else None}


@mcp.tool()
async def get_fleets() -> dict:
    data = await _get("/fleets")
    return {"fleets": data, "count": len(data) if isinstance(data, list) else None}


@mcp.tool()
async def get_drivers(status: str = "active") -> dict:
    params = {} if status == "all" else {"status": status}
    data = await _get("/drivers", params)
    return {"drivers": data, "count": len(data) if isinstance(data, list) else None}


@mcp.tool()
async def get_trips(vehicle_id: int, days_back: int = 7) -> dict:
    days_back = min(days_back, 30)
    now = datetime.now(timezone.utc)
    from_dt = (now - timedelta(days=days_back)).strftime("%Y-%m-%d")
    to_dt   = now.strftime("%Y-%m-%d")
    data    = await _get(f"/vehicles/{vehicle_id}/trips",
                         {"from": from_dt, "to": to_dt})
    return {"trips": data, "vehicle_id": vehicle_id}


@mcp.tool()
async def get_geofences() -> dict:
    data = await _get("/geofences")
    return {"geofences": data, "count": len(data) if isinstance(data, list) else None}


@mcp.tool()
async def get_vehicle_odometer(vehicle_id: int) -> dict:
    return await _get(f"/vehicles/{vehicle_id}/odometer")


# =========================================================================== #
# HEALTH + OAUTH
# =========================================================================== #

async def health(request):
    return JSONResponse({"status": "ok"})


async def oauth_metadata(request):
    return JSONResponse({
        "issuer": "https://tn360-mcp.onrender.com",
        "response_types_supported": ["token"],
    })


# =========================================================================== #
# APP
# =========================================================================== #

mcp_app = mcp.http_app(path="/mcp")

app = Starlette(
    lifespan=mcp_app.lifespan,
    routes=[
        Route("/health", health),
        Route("/.well-known/oauth-authorization-server", oauth_metadata),
        Mount("/", app=mcp_app),
    ],
)
