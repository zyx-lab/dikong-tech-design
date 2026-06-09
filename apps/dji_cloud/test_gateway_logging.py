from unittest.mock import patch

from django.test import SimpleTestCase, override_settings

from apps.dji_cloud.gateway import DjiGateway, GatewayResponse


class DjiGatewayExternalLoggingTests(SimpleTestCase):
    @override_settings(DJI_UPSTREAM_BASE_URL="https://dji.example.test", DJI_UPSTREAM_USERNAME="admin", DJI_UPSTREAM_PASSWORD="secret")
    def test_json_request_should_emit_external_call_events_with_unredacted_payloads(self):
        gateway = DjiGateway()

        def fake_request(method, path, *, data, headers, follow_redirects):
            self.assertEqual(method, "POST")
            self.assertEqual(path, "/api/v1/manage/login")
            return GatewayResponse(status_code=200, headers={"X-Upstream": "ok"}, data={"access_token": "raw-token"})

        with patch.object(gateway, "_request", side_effect=fake_request), patch(
            "apps.access.external_call_logging.log_json"
        ) as log_json:
            gateway._request_json(
                "POST",
                "/api/v1/manage/login",
                data={"username": "admin", "password": "secret"},
                authenticate=False,
                request_event="upstream_login_request",
                response_event="upstream_login_response",
            )

        events = [item.args[2] for item in log_json.call_args_list]
        self.assertEqual(events, ["external_call_started", "external_call_finished"])
        started = log_json.call_args_list[0].kwargs
        finished = log_json.call_args_list[1].kwargs
        self.assertEqual(started["service"], "dji_cloud")
        self.assertEqual(started["operation"], "upstream_login_request")
        self.assertEqual(started["request"]["body"]["password"], "secret")
        self.assertEqual(finished["response"]["body"]["access_token"], "raw-token")
        self.assertEqual(finished["call_id"], started["call_id"])
