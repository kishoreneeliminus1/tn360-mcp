# =========================================================================== #
# MCP Tools (FastMCP Safe)
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
    hours_back: int = 24,
    vehicle_id: Optional[int] = None,
) -> dict:

    hours_back = min(hours_back, 168)

    now = datetime.now(timezone.utc).replace(microsecond=0)
    start = (now - timedelta(hours_back)).replace(microsecond=0)

    params = {
        "types": sanitize_event_types(event_types) or "ignition,speed",
        "from": start.isoformat().replace("+00:00", "Z"),
        "to": now.isoformat().replace("+00:00", "Z"),
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
async def get_trips(vehicle_id: int, days_back: int = 7) -> dict:
    days_back = min(days_back, 30)
    now = datetime.now(timezone.utc)
    params = {
        "from": (now - timedelta(days_back)).strftime("%Y-%m-%d"),
        "to": now.strftime("%Y-%m-%d"),
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
    hours_back: int = 24,
    vehicle_id: Optional[int] = None,
) -> dict:

    hours_back = min(hours_back, 168)
    now = datetime.now(timezone.utc).replace(microsecond=0)
    start = (now - timedelta(hours_back)).replace(microsecond=0)

    params = {
        "types": "DRIVER",
        "from": start.isoformat().replace("+00:00", "Z"),
        "to": now.isoformat().replace("+00:00", "Z"),
        "pruning": "ALL",
    }

    if vehicle_id:
        params["vehicleId"] = vehicle_id

    return wrap_result(await _get("/events", params))
