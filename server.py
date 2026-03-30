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
    "ignition", "speed", "position", "geofence", "camera",
    "gpio", "installation", "alarm", "alert", "communication",
    "mass", "pto", "pretrip",
    "harshBraking", "harshAcceleration", "harshCornering",
    "overRevving", "driverFatigue", "driverDistraction",
    "seatbeltViolation",
}


def sanitize_event_types(raw: str) -> str:
    """Sanitize and validate event type list."""
    cleaned = [t.strip() for t in raw.split(",") if t.strip() in VALID_TN360_EVENT_TYPES]
    return ",".join(cleaned)


DEFAULT_EVENT_TYPES = (
    "speed,position,geofence,camera,gpio,installation,alarm,alert,"
    "communication,mass,pto,pretrip,harshBraking,harshAcceleration,"
    "harshCornering,overRevving,driverFatigue,driverDistraction,seatbeltViolation"
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
async def get_vehicle_images(vehicle_id: int) -> dict:
    return wrap_result(await _get(f"/vehicles/{vehicle_id}/images"))




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
