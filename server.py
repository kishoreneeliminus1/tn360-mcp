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
# EVENT TYPE FILTERING
# =========================================================================== #

VALID_TN360_EVENT_TYPES = {
    "ignition", "speed", "position", "geofence", "camera",
    "gpio", "installation", "alarm", "alert", "communication",
    "mass", "pto", "pretrip",
    "harshBraking", "harshAcceleration", "harshCornering",
    "overRevving", "driverFatigue", "driverDistraction",
    "seatbeltViolation",
}

def sanitize_event_types(raw: str) -> str:
    cleaned = []
    for t in raw.split(","):
        t = t.strip()
        if t in VALID_TN360_EVENT_TYPES:
            cleaned.append(t)
    return ",".join(cleaned)


DEFAULT_EVENT_TYPES = (
    "speed,position,geofence,camera,gpio,installation,alarm,alert,"
    "communication,mass,pto,pretrip,harshBraking,harshAcceleration,"
    "harshCornering,overRevving,driverFatigue,driverDistraction,seatbeltViolation"
)


# =========================================================================== #
# UNIVERSAL SAFE WRAPPER (CRITICAL FOR FASTMCP)
# =========================================================================== #

def wrap_result(raw: Any) -> dict:
    """
    Ensures tool output is FastMCP-safe:
    - Always returns {success, data, error, meta}
    - Never leaks invalid shapes to convert_result()
    """
    # TN360 errors are dicts with "error": ...
    if isinstance(raw, dict):
        if "error" in raw:
            return {
                "success": False,
                "data": None,
                "error": raw.get("error"),
                "meta": {k: v for k, v in raw.items() if k not in ("error",)}
            }
        return {
            "success": True,
            "data": raw,
            "error": None,
            "meta": {}
        }

    # Normal lists
    if isinstance(raw, list):
        return {
            "success": True,
            "data": raw,
            "error": None,
            "meta": {"count": len(raw)}
        }

    # Unknown shapes
    return {
        "success": False,
        "data": None,
        "error": f"Unexpected TN360 response type: {type(raw).__name__}",
        "meta": {"raw": str(raw)}
    }


# =========================================================================== #
# SMART GET WRAPPER
# =========================================================================== #

async def _get(path: str, params: dict | None = None) -> dict | list:
    url = f"{TN360_BASE_URL}/v1{path}"
    timeout = httpx.Timeout(60.0)
    params = params or {}

    for attempt in range(3):
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                r = await client.get(url, headers=_headers(), params=params)

                if 200 <= r.status_code < 300:
                    try:
                        return r.json()
                    except Exception:
                        return {
                            "error": "Invalid JSON from TN360",
                            "response_text": r.text,
                            "url": str(r.url)
                        }

                if r.status_code == 400:
                    return {"error": "400 Bad Request", "url": str(r.url), "params": params, "response": r.text}

                if r.status_code == 401:
                    return {"error": "401 Unauthorized – Invalid API key", "url": str(r.url)}

                if r.status_code == 403:
                    return {"error": "403 Forbidden", "url": str(r.url)}

                if r.status_code == 404:
                    return {"note": "No data found", "url": str(r.url), "params": params, "data": []}

                if r.status_code == 409:
                    return {"error": "409 Conflict", "url": str(r.url), "params": params}

                if r.status_code == 423:
                    return {"error": "423 Locked", "url": str(r.url)}

                if r.status_code == 429:
                    return {"error": "429 Rate Limited", "retry_after": r.headers.get("Retry-After")}

                if r.status_code >= 500:
                    return {"error": f"TN360 Server Error {r.status_code}", "url": str(r.url), "response": r.text}

                return {"error": f"Unexpected HTTP status {r.status_code}", "response": r.text, "url": str(r.url)}

        except httpx.ReadTimeout:
            if attempt == 2:
                return {"error": "ReadTimeout – TN360 did not respond", "url": url, "params": params}
            await asyncio.sleep(1.5 * (attempt + 1))

        except httpx.ConnectError:
            return {"error": "Connection error – TN360 unreachable", "url": url}

        except Exception as e:
            return {"error": f"Unexpected exception: {str(e)}", "url": url, "params": params}


# =========================================================================== #
# MCP TOOLS — NOW 100% SAFE
# =========================================================================== #

@mcp.tool()
async def get_vehicles(fleet_id: Optional[int] = None) -> dict:
    params = {"fleetId": fleet_id} if fleet_id else {}
    return wrap_result(await _get("/vehicles", params))


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

    # Default to last 6 days if no dates provided
    now = datetime.now(timezone.utc).replace(microsecond=0)

    if to_date:
        to_dt = datetime.fromisoformat(to_date).astimezone(timezone.utc)
    else:
        to_dt = now

    if from_date:
        from_dt = datetime.fromisoformat(from_date).astimezone(timezone.utc)
    else:
        from_dt = to_dt - timedelta(days=6)

    params = {
        "types": sanitize_event_types(event_types) or "camera,speed",
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
async def get_trips(
    vehicle_id: int,
    from_date: Optional[str] = None,
    to_date: Optional[str] = None,
) -> dict:

    now = datetime.now(timezone.utc).date()

    if to_date:
        to_dt = datetime.fromisoformat(to_date).date()
    else:
        to_dt = now

    if from_date:
        from_dt = datetime.fromisoformat(from_date).date()
    else:
        from_dt = to_dt - timedelta(days=6)

    params = {
        "from": from_dt.strftime("%Y-%m-%d"),
        "to": to_dt.strftime("%Y-%m-%d"),
    }

    return wrap_result(await _get(f"/vehicles/{vehicle_id}/trips", params))


@mcp.tool()
async def get_geofences() -> dict:
    return wrap_result(await _get("/geofences"))


@mcp.tool()
async def get_vehicle_odometer(vehicle_id: int) -> dict:
    return wrap_result(await _get(f"/vehicles/{vehicle_id}/meters"))


@mcp.tool()
async def get_vehicle_users(vehicle_id: int) -> dict:
    return wrap_result(await _get(f"/vehicles/{vehicle_id}/users"))


@mcp.tool()
async def get_vehicle_fleets(vehicle_id: int) -> dict:
    return wrap_result(await _get(f"/vehicles/{vehicle_id}/fleets"))


@mcp.tool()
async def get_vehicle_within(vehicle_id: int) -> dict:
    return wrap_result(await _get(f"/vehicles/{vehicle_id}/within", {"location_type": "all"}))


@mcp.tool()
async def get_vehicle_devices(vehicle_id: int) -> dict:
    return wrap_result(await _get(f"/vehicles/{vehicle_id}/devices", {"pruning": "all"}))


@mcp.tool()
async def get_vehicle_drivers(
    from_date: Optional[str] = None,
    to_date: Optional[str] = None,
    vehicle_id: Optional[int] = None,
) -> dict:

    now = datetime.now(timezone.utc).replace(microsecond=0)

    if to_date:
        to_dt = datetime.fromisoformat(to_date).astimezone(timezone.utc)
    else:
        to_dt = now

    if from_date:
        from_dt = datetime.fromisoformat(from_date).astimezone(timezone.utc)
    else:
        from_dt = to_dt - timedelta(days=6)

    params = {
        "types": "DRIVER",
        "from": from_dt.isoformat().replace("+00:00", "Z"),
        "to": to_dt.isoformat().replace("+00:00", "Z"),
        "pruning": "ALL",
    }

    if vehicle_id:
        params["vehicleId"] = vehicle_id

    return wrap_result(await _get("/events", params))

# =========================================================================== #
# PUT WRAPPER (TN360 Update Geofence)
# =========================================================================== #

async def _put(path: str, payload: dict) -> dict:
    url = f"{TN360_BASE_URL}/v1{path}"
    timeout = httpx.Timeout(60.0)

    for attempt in range(3):
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                r = await client.put(url, headers=_headers(), json=payload)

                if 200 <= r.status_code < 300:
                    try:
                        return r.json()
                    except Exception:
                        return {
                            "error": "Invalid JSON from TN360",
                            "response_text": r.text,
                            "url": str(r.url)
                        }

                if r.status_code == 400:
                    return {"error": "400 Bad Request", "url": str(r.url), "payload": payload, "response": r.text}

                if r.status_code == 401:
                    return {"error": "401 Unauthorized – Invalid API key", "url": str(r.url)}

                if r.status_code == 403:
                    return {"error": "403 Forbidden", "url": str(r.url)}

                if r.status_code == 404:
                    return {"error": "404 Not Found – Geofence does not exist", "url": str(r.url)}

                if r.status_code == 409:
                    return {"error": "409 Conflict", "url": str(r.url), "payload": payload}

                if r.status_code == 423:
                    return {"error": "423 Locked", "url": str(r.url)}

                if r.status_code == 429:
                    return {"error": "429 Rate Limited", "retry_after": r.headers.get("Retry-After")}

                if r.status_code >= 500:
                    return {"error": f"TN360 Server Error {r.status_code}", "url": str(r.url), "response": r.text}

                return {"error": f"Unexpected HTTP status {r.status_code}", "response": r.text, "url": str(r.url)}

        except httpx.ReadTimeout:
            if attempt == 2:
                return {"error": "ReadTimeout – TN360 did not respond", "url": url, "payload": payload}
            await asyncio.sleep(1.5 * (attempt + 1))

        except httpx.ConnectError:
            return {"error": "Connection error – TN360 unreachable", "url": url}

        except Exception as e:
            return {"error": f"Unexpected exception: {str(e)}", "url": url, "payload": payload}



# =========================================================================== #
# MCP TOOL: update_geofence
# =========================================================================== #

@mcp.tool()
async def update_geofence(
    geofence_id: int,
    name: Optional[str] = None,
    geotype: Optional[str] = None,
    coordinates: Optional[list] = None,
    thresholdSpeed: Optional[int] = None,
    properties: Optional[dict] = None
) -> dict:
    """
    Updates an existing TN360 geofence (PUT /geofences/{id}).

    Example: update threshold speed
    update_geofence(1234, thresholdSpeed=40)
    """

    # Build payload dynamically (TN360 allows partial updates)
    payload: dict = {}

    if name is not None:
        payload["name"] = name

    if geotype is not None:
        payload["type"] = geotype

    if coordinates is not None:
        payload["coordinates"] = coordinates

    # Merge custom properties
    merged_properties = properties.copy() if properties else {}
    if thresholdSpeed is not None:
        merged_properties["thresholdSpeed"] = thresholdSpeed

    if merged_properties:
        payload["properties"] = merged_properties

    # Perform PUT
    result = await _put(f"/geofences/{geofence_id}", payload)

    # Wrap TN360 output to be MCP‑safe
    return wrap_result(result)


# =========================================================================== #
# SYSTEM ROUTES
# =========================================================================== #

async def health(request):
    return JSONResponse({"status": "ok"})

async def oauth_metadata(request):
    return JSONResponse({
        "issuer": "https://tn360-mcp.onrender.com",
        "response_types_supported": ["token"],
    })


# =========================================================================== #
# STARLETTE APP
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
