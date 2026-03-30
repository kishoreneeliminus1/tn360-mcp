# ============================================================================ #
# TN360 MCP Server – Fully Integrated with DashCam Video Support
# ============================================================================ #

import os
import httpx
import asyncio
import base64
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional, Any

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
    if not TN360_API_KEY:
        raise RuntimeError("TN360_API_KEY environment variable is not set.")
    return {
        "Authorization": f"Bearer {TN360_API_KEY}",
        "Accept": "application/json",
        "Content-Type": "application/json",
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


# ============================================================================ #
# UNIVERSAL SAFE WRAPPER
# ============================================================================ #

def wrap_result(raw: Any) -> dict:
    if isinstance(raw, dict):
        if "error" in raw:
            return {
                "success": False,
                "data": None,
                "error": raw["error"],
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
# GET WRAPPER (WITH LOGGING)
# ============================================================================ #

async def _get(path: str, params: dict | None = None) -> dict | list:
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
# POST WRAPPER (WITH LOGGING)
# ============================================================================ #

async def _post(path: str, payload: dict) -> dict:
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
                        return {"error": "Invalid JSON from TN360", "response_text": r.text}

                return {"error": f"HTTP {r.status_code}", "response": r.text}

        except Exception as e:
            logging.error(f"[HTTP POST] ERROR: {e}")
            if attempt == 2:
                return {"error": str(e)}
            await asyncio.sleep(1.25 * (attempt + 1))


# ============================================================================ #
# PUT WRAPPER (WITH LOGGING)
# ============================================================================ #

async def _put(path: str, payload: dict) -> dict:
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

    now = datetime.now(timezone.utc).replace(microsecond=0)

    to_dt = datetime.fromisoformat(to_date).astimezone(timezone.utc) if to_date else now
    from_dt = datetime.fromisoformat(from_date).astimezone(timezone.utc) if from_date else to_dt - timedelta(days=6)

    params = {
        "types": sanitize_event_types(event_types) or "camera,speed,position,gpio",
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

    to_dt = datetime.fromisoformat(to_date).date() if to_date else now
    from_dt = datetime.fromisoformat(from_date).date() if from_date else to_dt - timedelta(days=6)

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


# ============================================================================ #
# DASHCAM VIDEO TOOLS
# ============================================================================ #

@mcp.tool()
async def request_dashcam_video(
    device_id: int,
    start_timestamp: str,
    end_timestamp: str
) -> dict:

    payload = {
        "startTimestamp": start_timestamp,
        "endTimestamp": end_timestamp
    }

    result = await _post(f"/devices/{device_id}/requestVideo", payload)
    return wrap_result(result)


@mcp.tool()
async def get_dashcam_video_status(request_id: str) -> dict:
    result = await _get(f"/video/requests/{request_id}")
    return wrap_result(result)


@mcp.tool()
async def download_dashcam_video(request_id: str) -> dict:
    status = await _get(f"/video/requests/{request_id}")

    if "error" in status:
        return wrap_result(status)

    video_url = status.get("videoUrl")
    if not video_url:
        return wrap_result({"error": "Video not ready", "status": status})

    async with httpx.AsyncClient(timeout=httpx.Timeout(120.0)) as client:
        r = await client.get(video_url)

        if r.status_code != 200:
            return wrap_result({"error": f"Download failed: {r.status_code}", "response": r.text})

        encoded = base64.b64encode(r.content).decode("utf-8")
        return {
            "success": True,
            "data": {"video_base64": encoded},
            "error": None,
            "meta": {"size_bytes": len(r.content)}
        }


# ============================================================================ #
# GEOFENCE TOOL
# ============================================================================ #

@mcp.tool()
async def update_geofence(
    geofence_id: int,
    name: Optional[str] = None,
    geotype: Optional[str] = None,
    coordinates: Optional[list] = None,
    thresholdSpeed: Optional[int] = None,
    properties: Optional[dict] = None
) -> dict:

    payload: dict = {}

    if name is not None:
        payload["name"] = name

    if geotype is not None:
        payload["type"] = geotype

    if coordinates is not None:
        payload["coordinates"] = coordinates

    merged = properties.copy() if properties else {}
    if thresholdSpeed is not None:
        merged["thresholdSpeed"] = thresholdSpeed

    if merged:
        payload["properties"] = merged

    result = await _put(f"/geofences/{geofence_id}", payload)
    return wrap_result(result)


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
