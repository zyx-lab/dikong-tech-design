import os
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

import server


class ServerConfigTests(unittest.TestCase):
    def setUp(self):
        self.env_patch = patch.dict(os.environ, {}, clear=True)
        self.env_patch.start()

    def tearDown(self):
        self.env_patch.stop()

    def test_defaults_target_local_api_and_http_mcp_port(self):
        config = server.load_config()

        self.assertEqual(config.api_base_url, "http://127.0.0.1:8000")
        self.assertEqual(config.openapi_schema_url, "http://127.0.0.1:8000/api/v2/docs/schema/")
        self.assertEqual(config.mcp_host, "127.0.0.1")
        self.assertEqual(config.mcp_port, 8001)
        self.assertEqual(config.mcp_transport, "http")

    def test_environment_overrides_defaults(self):
        with patch.dict(
            os.environ,
            {
                "DIKONG_API_BASE_URL": "http://api.local:9000/",
                "DIKONG_OPENAPI_SCHEMA_URL": "http://schema.local/openapi.json",
                "DIKONG_MCP_HOST": "0.0.0.0",
                "DIKONG_MCP_PORT": "9001",
                "DIKONG_MCP_TRANSPORT": "stdio",
            },
            clear=True,
        ):
            config = server.load_config()

        self.assertEqual(config.api_base_url, "http://api.local:9000")
        self.assertEqual(config.openapi_schema_url, "http://schema.local/openapi.json")
        self.assertEqual(config.mcp_host, "0.0.0.0")
        self.assertEqual(config.mcp_port, 9001)
        self.assertEqual(config.mcp_transport, "stdio")


class ServerBuildTests(unittest.IsolatedAsyncioTestCase):
    async def test_fetch_openapi_spec_reads_schema_without_auth_headers(self):
        config = server.ServerConfig(
            api_base_url="http://127.0.0.1:8000",
            openapi_schema_url="http://127.0.0.1:8000/api/v2/docs/schema/",
            mcp_host="127.0.0.1",
            mcp_port=8001,
            mcp_transport="http",
        )
        response = MagicMock()
        response.json.return_value = {"openapi": "3.0.3", "paths": {}}
        response.raise_for_status.return_value = None

        with patch("server.httpx.AsyncClient") as async_client_cls:
            client = AsyncMock()
            client.get.return_value = response
            async_client_cls.return_value.__aenter__.return_value = client

            spec = await server.fetch_openapi_spec(config)

        self.assertEqual(spec["openapi"], "3.0.3")
        async_client_cls.assert_called_once_with(timeout=30.0)
        client.get.assert_awaited_once_with(config.openapi_schema_url)

    def test_build_mcp_server_uses_openapi_spec_and_plain_api_client(self):
        config = server.ServerConfig(
            api_base_url="http://127.0.0.1:8000",
            openapi_schema_url="http://127.0.0.1:8000/api/v2/docs/schema/",
            mcp_host="127.0.0.1",
            mcp_port=8001,
            mcp_transport="http",
        )
        openapi_spec = {"openapi": "3.0.3", "info": {"title": "Dikong"}, "paths": {}}

        with patch("server.FastMCP") as fastmcp_cls, patch("server.httpx.AsyncClient") as async_client_cls:
            built_server = server.build_mcp_server(config, openapi_spec)

        self.assertEqual(built_server, fastmcp_cls.from_openapi.return_value)
        async_client_cls.assert_called_once_with(base_url=config.api_base_url, timeout=30.0)
        fastmcp_cls.from_openapi.assert_called_once()
        _, kwargs = fastmcp_cls.from_openapi.call_args
        self.assertEqual(kwargs["openapi_spec"], openapi_spec)
        self.assertEqual(kwargs["client"], async_client_cls.return_value)
        self.assertEqual(kwargs["name"], "Dikong API v2")

    def test_run_server_uses_http_transport_options(self):
        config = server.ServerConfig(
            api_base_url="http://127.0.0.1:8000",
            openapi_schema_url="http://127.0.0.1:8000/api/v2/docs/schema/",
            mcp_host="127.0.0.1",
            mcp_port=8001,
            mcp_transport="http",
        )
        mcp = MagicMock()

        server.run_server(mcp, config)

        mcp.run.assert_called_once_with(transport="http", host="127.0.0.1", port=8001)


if __name__ == "__main__":
    unittest.main()
