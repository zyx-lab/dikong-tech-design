from __future__ import annotations

import threading
from socketserver import ThreadingMixIn
from wsgiref.simple_server import WSGIRequestHandler, WSGIServer, make_server

from django.core.wsgi import get_wsgi_application
from django.test import override_settings

from apps.dji_mock.state import mock_dji_state


class _QuietWSGIRequestHandler(WSGIRequestHandler):
    def log_message(self, format, *args):
        return


class _ThreadingWSGIServer(ThreadingMixIn, WSGIServer):
    daemon_threads = True


class MockDjiUpstreamTestMixin:
    """Provide a reusable test-only HTTP server for the mock DJI upstream."""

    _mock_server = None
    _mock_server_thread = None
    _mock_server_url = ""
    _mock_settings = None

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._mock_settings = override_settings(ENABLE_DJI_MOCK_SERVER=True)
        cls._mock_settings.enable()
        try:
            server = make_server(
                "127.0.0.1",
                0,
                get_wsgi_application(),
                server_class=_ThreadingWSGIServer,
                handler_class=_QuietWSGIRequestHandler,
            )
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()

            cls._mock_server = server
            cls._mock_server_thread = thread
            cls._mock_server_url = f"http://127.0.0.1:{server.server_port}/__mock-dji__"
        except Exception:
            cls._mock_settings.disable()
            raise

    @classmethod
    def tearDownClass(cls):
        try:
            if cls._mock_server is not None:
                cls._mock_server.shutdown()
                cls._mock_server.server_close()
            if cls._mock_server_thread is not None:
                cls._mock_server_thread.join(timeout=5)
            if cls._mock_settings is not None:
                cls._mock_settings.disable()
        finally:
            super().tearDownClass()

    def setUp(self):
        super().setUp()
        mock_dji_state.reset()
