# TN360 MCP Server

A Python MCP server that gives Claude access to your Teletrac Navman TN360 fleet telematics data. Built with [FastMCP](https://gofastmcp.com) and deployable to [Render](https://render.com) in minutes.

---

## What Claude can do with this server

| Tool | Description |
|---|---|
| `get_vehicles` | List all vehicles, optionally filtered by fleet |
| `get_vehicle_location` | Current GPS position, speed, and heading |
| `get_events` | Ignition, speeding, harsh braking, geofence events |
| `get_fleets` | All virtual fleet groups |
| `get_drivers` | Registered drivers and their current vehicle |
| `get_trips` | Trip history for a vehicle (up to 30 days) |
| `get_geofences` | All configured geofence zones |
| `get_vehicle_odometer` | Current odometer reading |

Example prompts you can send Claude once connected:

- *"Show me all vehicles that had a speeding event today"*
- *"Where is vehicle 12345 right now?"*
- *"List all active drivers and their assigned vehicles"*
- *"How many km did fleet 'Sydney Metro' drive this week?"*

---

## Step 1 — Get your TN360 API key

TN360 uses a two-step OAuth flow to obtain your API key:

```bash
# 1. Authenticate with OpenID Connect to get a Bearer token
curl -d 'client_id=YOUR_CLIENT_ID' \
     -d 'client_secret=YOUR_SECRET' \
     -d 'username=YOUR_USERNAME' \
     -d 'password=YOUR_PASSWORD' \
     -d 'grant_type=password' \
     'https://id-au.telematics.com/auth/realms/TN360DB/protocol/openid-connect/token'

# 2. Exchange the Bearer token for your TN360 API key
curl -H "Authorization: Bearer <access_token_from_step_1>" \
     https://api-au.telematics.com/v1/auth/sso
# The response contains: "keys": [{ "key": "YOUR_API_KEY_HERE" }]
```

Copy the `key` value from the `keys` array — this is your `TN360_API_KEY`.

> **Note:** If you are in the UK or NZ, replace `api-au` with `api-uk` or `api-nz` in both the server's `TN360_BASE_URL` and the curl commands above.

---

## Step 2 — Deploy to Render

### Option A — Render Blueprint (recommended)

1. Push this folder to a GitHub repo.
2. Go to [render.com](https://render.com) → **New → Blueprint**.
3. Connect your GitHub repo — Render will detect `render.yaml` automatically.
4. In the Render dashboard, set the secret environment variable:
   - `TN360_API_KEY` = the key you retrieved in Step 1
5. Click **Apply** — your server will build and go live at:
   `https://tn360-mcp-server.onrender.com`

### Option B — Manual Render Web Service

1. **New → Web Service** → connect your repo.
2. Set runtime: **Python 3**, build command: `pip install -r requirements.txt`, start command: `python server.py`.
3. Add environment variables:
   - `TN360_API_KEY` — your API key (mark as secret)
   - `TN360_BASE_URL` — `https://api-au.telematics.com`
4. Deploy.

---

## Step 3 — Connect Claude to your MCP server

### Claude.ai (Remote MCP — claude.ai/settings/integrations)

1. Go to **claude.ai → Settings → Integrations**.
2. Add a new integration with URL:
   ```
   https://tn360-mcp-server.onrender.com/mcp
   ```
3. Claude will now have access to all TN360 tools in your conversations.

### Claude Desktop (local config)

Add to `~/Library/Application Support/Claude/claude_desktop_config.json` (macOS):

```json
{
  "mcpServers": {
    "tn360": {
      "url": "https://tn360-mcp-server.onrender.com/mcp"
    }
  }
}
```

Restart Claude Desktop. You should see a hammer icon indicating the TN360 tools are available.

---

## Local development

```bash
# Install dependencies
pip install -r requirements.txt

# Set your API key
export TN360_API_KEY=your_api_key_here
export TN360_BASE_URL=https://api-au.telematics.com

# Run the server
python server.py
# Server available at http://localhost:8000/mcp

# Test with MCP Inspector
npx @modelcontextprotocol/inspector http://localhost:8000/mcp
```

---

## Project structure

```
tn360-mcp/
├── server.py          # MCP server — all tools defined here
├── requirements.txt   # Python dependencies
├── render.yaml        # Render deployment blueprint
└── README.md          # This file
```

---

## Adding more TN360 tools

The TN360 REST API has many more endpoints. To add a new tool, just add a decorated function to `server.py`:

```python
@mcp.tool()
async def get_maintenance_alerts(vehicle_id: int) -> dict:
    """Get upcoming maintenance alerts for a vehicle."""
    data = await _get(f"/vehicles/{vehicle_id}/maintenance")
    return data
```

See the full TN360 API reference at [docs-au.telematics.com](https://docs-au.telematics.com).
