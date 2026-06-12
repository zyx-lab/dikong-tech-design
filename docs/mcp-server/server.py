import asyncio
import os
from dataclasses import dataclass
from typing import Any
from urllib.parse import urljoin

import httpx
from fastmcp import FastMCP


DEFAULT_API_BASE_URL = "http://127.0.0.1:8000"
DEFAULT_SCHEMA_PATH = "/api/v2/docs/schema/"
DEFAULT_MCP_HOST = "127.0.0.1"
DEFAULT_MCP_PORT = 8001
DEFAULT_MCP_TRANSPORT = "http"
REQUEST_TIMEOUT_SECONDS = 30.0


@dataclass(frozen=True)
class ServerConfig:
    api_base_url: str
    openapi_schema_url: str
    mcp_host: str
    mcp_port: int
    mcp_transport: str


def load_config() -> ServerConfig:
    api_base_url = os.getenv("DIKONG_API_BASE_URL", DEFAULT_API_BASE_URL).rstrip("/")
    schema_url = os.getenv(
        "DIKONG_OPENAPI_SCHEMA_URL",
        urljoin(f"{api_base_url}/", DEFAULT_SCHEMA_PATH.lstrip("/")),
    )

    return ServerConfig(
        api_base_url=api_base_url,
        openapi_schema_url=schema_url,
        mcp_host=os.getenv("DIKONG_MCP_HOST", DEFAULT_MCP_HOST),
        mcp_port=int(os.getenv("DIKONG_MCP_PORT", str(DEFAULT_MCP_PORT))),
        mcp_transport=os.getenv("DIKONG_MCP_TRANSPORT", DEFAULT_MCP_TRANSPORT),
    )


async def fetch_openapi_spec(config: ServerConfig) -> dict[str, Any]:
    async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT_SECONDS) as client:
        response = await client.get(config.openapi_schema_url)
        response.raise_for_status()
        return response.json()


def build_mcp_server(config: ServerConfig, openapi_spec: dict[str, Any]) -> FastMCP:
    api_client = httpx.AsyncClient(base_url=config.api_base_url, timeout=REQUEST_TIMEOUT_SECONDS)
    return FastMCP.from_openapi(
        openapi_spec=openapi_spec,
        client=api_client,
        name="Dikong API v2",
    )


def run_server(mcp: FastMCP, config: ServerConfig) -> None:
    if config.mcp_transport == "http":
        mcp.run(transport="http", host=config.mcp_host, port=config.mcp_port)
        return

    if config.mcp_transport == "stdio":
        mcp.run()
        return

    raise ValueError("DIKONG_MCP_TRANSPORT must be 'http' or 'stdio'")


def main() -> None:
    config = load_config()
    openapi_spec = asyncio.run(fetch_openapi_spec(config))
    mcp = build_mcp_server(config, openapi_spec)
    run_server(mcp, config)


if __name__ == "__main__":
    main()
