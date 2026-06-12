# Dikong MCP Server

FastMCP server generated from the local API v2 OpenAPI schema.

## Usage

### 1. Start the API server

Start the Django API first. This MCP server reads the OpenAPI schema from:

```text
http://127.0.0.1:8000/api/v2/docs/schema/
```

You can confirm the schema is reachable with:

```bash
curl -fsS http://127.0.0.1:8000/api/v2/docs/schema/ | head
```

### 2. Start the MCP server

From this directory, run:

```bash
uv run python server.py
```

The server uses HTTP transport by default and listens on:

```text
http://127.0.0.1:8001/mcp
```

Keep this process running while an MCP client is connected.

### 3. Connect an MCP client

Configure your MCP client to use the HTTP endpoint:

```text
http://127.0.0.1:8001/mcp
```

The server is generated from the API v2 OpenAPI schema. With the current local schema, it exposes the API operations as MCP tools.

### 4. Run in the background

For local background use:

```bash
nohup uv run python server.py > mcp-server.log 2>&1 &
echo $! > mcp-server.pid
```

Stop the background server with:

```bash
kill "$(cat mcp-server.pid)"
```

## Configuration

The server has no API authentication configured. Runtime settings can be overridden with environment variables:

| Variable | Default |
| --- | --- |
| `DIKONG_API_BASE_URL` | `http://127.0.0.1:8000` |
| `DIKONG_OPENAPI_SCHEMA_URL` | `http://127.0.0.1:8000/api/v2/docs/schema/` |
| `DIKONG_MCP_HOST` | `127.0.0.1` |
| `DIKONG_MCP_PORT` | `8001` |
| `DIKONG_MCP_TRANSPORT` | `http` |

Example:

```bash
DIKONG_MCP_HOST=0.0.0.0 DIKONG_MCP_PORT=9001 uv run python server.py
```

## Verify

Run unit tests:

```bash
uv run python -m unittest -v
```

Verify the HTTP MCP endpoint from Python:

```bash
uv run python - <<'PY'
import asyncio
from fastmcp import Client

async def main():
    async with Client("http://127.0.0.1:8001/mcp") as client:
        tools = await client.list_tools()
    print(f"tools={len(tools)}")
    print(f"first_tool={tools[0].name if tools else 'none'}")

asyncio.run(main())
PY
```
