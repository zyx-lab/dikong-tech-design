from unittest.mock import patch

from django.test import SimpleTestCase

from apps.dji_cloud.gateway import DjiCloudSession, DjiGateway, DjiGatewayConfigurationError, GatewayResponse


class DjiGatewayExternalLoggingTests(SimpleTestCase):
    def test_gateway_without_platform_should_not_configure_upstream_or_credentials(self):
        gateway = DjiGateway()

        self.assertEqual(gateway.base_url, "")
        with self.assertRaisesMessage(DjiGatewayConfigurationError, "DJI upstream baseUrl 未配置"):
            gateway._headers()

        explicit_gateway = DjiGateway(base_url="https://explicit.example.test")
        with self.assertRaisesMessage(DjiGatewayConfigurationError, "DjiGateway requires platform credentials"):
            explicit_gateway._configured_credentials()

    def test_json_request_should_emit_external_call_events_with_unredacted_payloads(self):
        gateway = DjiGateway(base_url="https://dji.example.test")

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

    def test_create_dock_flight_task_should_post_to_flight_tasks_endpoint(self):
        gateway = DjiGateway(base_url="https://dji.example.test")

        with patch.object(
            gateway,
            "_ensure_authenticated",
            return_value=DjiCloudSession(workspace_id="workspace-dock-task", access_token="token"),
        ), patch.object(
            gateway,
            "_request_json",
            return_value=GatewayResponse(status_code=200, headers={}, data={"job_id": "job-dock-001"}),
        ) as request_json:
            result = gateway.create_dock_flight_task(
                mission_name="机场任务",
                file_id="wayline-001",
                dock_sn="DOCK-SN-001",
                wayline_type=3,
                task_type=0,
                wayline_precision_type=0,
                rth_mode=0,
                rth_altitude=30,
                exit_wayline_when_rc_lost=0,
                out_of_control_action=0,
            )

        self.assertEqual(result, {"dji_job_id": "job-dock-001"})
        request_json.assert_called_once_with(
            "POST",
            "/api/v1/wayline/workspaces/workspace-dock-task/flight-tasks",
            data={
                "name": "机场任务",
                "file_id": "wayline-001",
                "dock_sn": "DOCK-SN-001",
                "wayline_type": 3,
                "task_type": 0,
                "wayline_precision_type": 0,
                "rth_mode": 0,
                "rth_altitude": 30,
                "exit_wayline_when_rc_lost": 0,
                "out_of_control_action": 0,
            },
        )

    def test_create_mission_should_remain_compatibility_wrapper_for_dock_task(self):
        gateway = DjiGateway(base_url="https://dji.example.test")

        with patch.object(gateway, "create_dock_flight_task", return_value={"dji_job_id": "job-wrapper-001"}) as create_task:
            result = gateway.create_mission(mission_name="兼容任务", file_id="wayline-001", dock_sn="DOCK-SN-001")

        self.assertEqual(result, {"dji_job_id": "job-wrapper-001"})
        create_task.assert_called_once_with(
            mission_name="兼容任务",
            file_id="wayline-001",
            dock_sn="DOCK-SN-001",
            wayline_type=None,
            task_type=None,
            wayline_precision_type=None,
            rth_mode=None,
            rth_altitude=None,
            exit_wayline_when_rc_lost=None,
            out_of_control_action=None,
        )
