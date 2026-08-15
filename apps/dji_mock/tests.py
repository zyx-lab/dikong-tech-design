import json

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import SimpleTestCase, override_settings

from apps.dji_mock.state import mock_dji_state


@override_settings(ENABLE_DJI_MOCK_SERVER=True)
class DjiMockServerTests(SimpleTestCase):
    def setUp(self):
        super().setUp()
        mock_dji_state.reset()

    def test_mock_server_should_return_404_when_disabled(self):
        with override_settings(ENABLE_DJI_MOCK_SERVER=False):
            response = self.client.post(
                "/__mock-dji__/api/v1/manage/login",
                data=json.dumps({"username": "admin", "password": "admin"}),
                content_type="application/json",
            )
        self.assertEqual(response.status_code, 404)

    def test_login_and_bound_devices_should_follow_minimal_http_contract(self):
        login_response = self.client.post(
            "/__mock-dji__/api/v1/manage/login",
            data=json.dumps({"username": "admin", "password": "admin"}),
            content_type="application/json",
        )
        self.assertEqual(login_response.status_code, 200)
        payload = login_response.json()["data"]
        self.assertEqual(payload["workspace_id"], "mock-workspace-001")
        token = payload["access_token"]
        mock_dji_state.bound_device_sns = {"MOCK-DRONE-001"}

        devices_response = self.client.get(
            "/__mock-dji__/api/v1/manage/workspaces/mock-workspace-001/devices/bound?domain=0&page=1&page_size=20",
            HTTP_X_AUTH_TOKEN=token,
        )
        self.assertEqual(devices_response.status_code, 200)
        body = devices_response.json()["data"]
        self.assertEqual(body["pagination"]["total"], 1)
        self.assertEqual(body["list"][0]["device_sn"], "MOCK-DRONE-001")
        self.assertIn("device_name", body["list"][0])
        self.assertNotIn("model", body["list"][0])

    def test_bound_devices_should_require_domain_query_param(self):
        response = self.client.get(
            "/__mock-dji__/api/v1/manage/workspaces/mock-workspace-001/devices/bound",
            HTTP_X_AUTH_TOKEN=mock_dji_state.access_token,
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["code"], "B0001")
        self.assertEqual(response.json()["data"], {"domain": ["该字段是必填项。"]})

    def test_protected_endpoints_should_require_valid_token(self):
        response = self.client.get("/__mock-dji__/api/v1/manage/users/current")
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json()["code"], "A0401")

    def test_wayline_job_and_media_endpoints_should_offer_stateful_minimal_behaviour(self):
        token = mock_dji_state.access_token

        upload_response = self.client.post(
            "/__mock-dji__/api/v1/wayline/workspaces/mock-workspace-001/waylines/files/upload",
            data={
                "name": "Mock Route A",
                "file": SimpleUploadedFile("route-a.kmz", b"mock-kmz", content_type="application/octet-stream"),
            },
            HTTP_X_AUTH_TOKEN=token,
        )
        self.assertEqual(upload_response.status_code, 200)
        upload_data = upload_response.json()["data"]
        self.assertEqual(upload_data["name"], "Mock Route A")
        self.assertEqual(upload_data["workspace_id"], "mock-workspace-001")
        self.assertEqual(
            upload_data["download_url"],
            f"/api/v1/wayline/workspaces/mock-workspace-001/waylines/{upload_data['wayline_id']}/url",
        )
        dji_wayline_id = upload_data.get("wayline_id") or upload_data.get("dji_wayline_id")
        self.assertEqual(upload_data["wayline_id"], dji_wayline_id)

        waylines_response = self.client.get(
            "/__mock-dji__/api/v1/wayline/workspaces/mock-workspace-001/waylines",
            HTTP_X_AUTH_TOKEN=token,
        )
        self.assertEqual(waylines_response.status_code, 200)
        self.assertEqual(waylines_response.json()["data"]["list"][0]["wayline_id"], dji_wayline_id)

        job_response = self.client.post(
            "/__mock-dji__/api/v1/wayline/workspaces/mock-workspace-001/flight-tasks",
            data=json.dumps(
                {
                    "name": "Mission A",
                    "fileId": dji_wayline_id,
                    "dockSn": "DOCK-001",
                    "waylineType": 0,
                    "taskType": 0,
                    "rthAltitude": 30,
                    "outOfControlAction": 0,
                }
            ),
            content_type="application/json",
            HTTP_X_AUTH_TOKEN=token,
        )
        self.assertEqual(job_response.status_code, 200)
        job_id = job_response.json()["data"]["dji_job_id"]

        jobs_response = self.client.get(
            "/__mock-dji__/api/v1/wayline/workspaces/mock-workspace-001/jobs",
            HTTP_X_AUTH_TOKEN=token,
        )
        self.assertEqual(jobs_response.status_code, 200)
        self.assertEqual(jobs_response.json()["data"]["list"][0]["job_id"], job_id)

        cancel_response = self.client.delete(
            f"/__mock-dji__/api/v1/wayline/workspaces/mock-workspace-001/jobs?job_id={job_id}",
            HTTP_X_AUTH_TOKEN=token,
        )
        self.assertEqual(cancel_response.status_code, 200)
        self.assertEqual(cancel_response.json()["data"]["cancelled_job_ids"], [job_id])

        invalid_job_response = self.client.post(
            "/__mock-dji__/api/v1/wayline/workspaces/mock-workspace-001/flight-tasks",
            data=json.dumps({"name": "Mission Missing Route"}),
            content_type="application/json",
            HTTP_X_AUTH_TOKEN=token,
        )
        self.assertEqual(invalid_job_response.status_code, 400)
        self.assertEqual(invalid_job_response.json()["code"], "B0001")

        media_response = self.client.get(
            "/__mock-dji__/api/v1/media/workspaces/mock-workspace-001/files",
            HTTP_X_AUTH_TOKEN=token,
        )
        self.assertEqual(media_response.status_code, 200)
        self.assertEqual(media_response.json()["data"]["list"][0]["file_id"], "mock-file-001")

        download_response = self.client.get(
            "/__mock-dji__/api/v1/media/workspaces/mock-workspace-001/files/mock-file-001/url",
            HTTP_X_AUTH_TOKEN=token,
            follow=False,
        )
        self.assertEqual(download_response.status_code, 302)
        self.assertEqual(download_response["Location"], "/__mock-dji__/_downloads/media/mock-file-001")

        playback_response = self.client.get(
            "/__mock-dji__/api/v1/media/workspaces/mock-workspace-001/files/mock-file-001/playback-url",
            HTTP_X_AUTH_TOKEN=token,
            follow=False,
        )
        self.assertEqual(playback_response.status_code, 302)
        self.assertEqual(playback_response["Location"], "/__mock-dji__/_downloads/media/mock-file-001")

        preview_response = self.client.get(
            "/__mock-dji__/api/v1/media/workspaces/mock-workspace-001/files/mock-file-001/preview-url",
            HTTP_X_AUTH_TOKEN=token,
            follow=False,
        )
        self.assertEqual(preview_response.status_code, 302)
        self.assertEqual(preview_response["Location"], "/__mock-dji__/_downloads/media/mock-file-001")

    def test_wayline_upload_should_require_non_empty_file(self):
        token = mock_dji_state.access_token

        missing_file_response = self.client.post(
            "/__mock-dji__/api/v1/wayline/workspaces/mock-workspace-001/waylines/files/upload",
            data={"name": "Missing File Route"},
            HTTP_X_AUTH_TOKEN=token,
        )
        self.assertEqual(missing_file_response.status_code, 400)
        self.assertEqual(missing_file_response.json()["code"], "B0001")
        self.assertEqual(missing_file_response.json()["data"], {"file": ["该字段是必填项。"]})
        self.assertEqual(mock_dji_state.waylines, {})

        empty_file_response = self.client.post(
            "/__mock-dji__/api/v1/wayline/workspaces/mock-workspace-001/waylines/files/upload",
            data={
                "name": "Empty File Route",
                "file": SimpleUploadedFile("empty.kmz", b"", content_type="application/octet-stream"),
            },
            HTTP_X_AUTH_TOKEN=token,
        )
        self.assertEqual(empty_file_response.status_code, 400)
        self.assertEqual(empty_file_response.json()["code"], "B0001")
        self.assertEqual(empty_file_response.json()["data"], {"file": ["该字段是必填项。"]})
        self.assertEqual(mock_dji_state.waylines, {})

    def test_live_endpoints_should_align_with_realistic_response_shape(self):
        token = mock_dji_state.access_token

        capacity_response = self.client.get(
            "/__mock-dji__/api/v1/manage/live/capacity",
            HTTP_X_AUTH_TOKEN=token,
        )
        self.assertEqual(capacity_response.status_code, 200)
        capacity_data = capacity_response.json()["data"]
        self.assertIsInstance(capacity_data, list)
        self.assertGreaterEqual(len(capacity_data), 1)
        self.assertEqual(capacity_data[0]["sn"], "MOCK-DRONE-001")
        self.assertIn("cameras_list", capacity_data[0])

        start_response = self.client.post(
            "/__mock-dji__/api/v1/manage/live/streams/start",
            data=json.dumps(
                {
                    "device_sn": "MOCK-DRONE-001",
                    "video_id": "MOCK-DRONE-001/88-0-0/normal-0",
                    "url_type": 1,
                    "video_quality": 0,
                }
            ),
            content_type="application/json",
            HTTP_X_AUTH_TOKEN=token,
        )
        self.assertEqual(start_response.status_code, 200)
        start_data = start_response.json()["data"]
        self.assertIn("rtmp_url", start_data)
        self.assertIn("webrtc_url", start_data)
        self.assertIn("whep_url", start_data)
        self.assertIn("hls_url", start_data)

        stop_response = self.client.post(
            "/__mock-dji__/api/v1/manage/live/streams/stop",
            data=json.dumps({"video_id": "MOCK-DRONE-001/88-0-0/normal-0"}),
            content_type="application/json",
            HTTP_X_AUTH_TOKEN=token,
        )
        self.assertEqual(stop_response.status_code, 200)
        self.assertEqual(stop_response.json()["data"], {})

        camera_change_response = self.client.post(
            "/__mock-dji__/api/v1/manage/live/streams/camera-change",
            data=json.dumps(
                {
                    "device_sn": "MOCK-DRONE-001",
                    "video_id": "MOCK-DRONE-001/88-0-0/normal-0",
                    "camera_position": 1,
                }
            ),
            content_type="application/json",
            HTTP_X_AUTH_TOKEN=token,
        )
        self.assertEqual(camera_change_response.status_code, 200)
        self.assertEqual(camera_change_response.json()["data"], {})

        not_found_response = self.client.post(
            "/__mock-dji__/api/v1/manage/live/streams/start",
            data=json.dumps({"video_id": "UNKNOWN-SN/88-0-0/normal-0", "url_type": 1}),
            content_type="application/json",
            HTTP_X_AUTH_TOKEN=token,
        )
        self.assertEqual(not_found_response.status_code, 200)
        self.assertEqual(not_found_response.json()["code"], "D0001")
        self.assertIsNone(not_found_response.json()["data"])

    def test_payload_control_endpoints_should_record_requests_and_support_business_failure(self):
        token = mock_dji_state.access_token

        authority_response = self.client.post(
            "/__mock-dji__/api/v1/control/devices/MOCK-GATEWAY-001/authority/payload",
            data=json.dumps({"payload_index": "88-0-0"}),
            content_type="application/json",
            HTTP_X_AUTH_TOKEN=token,
        )
        self.assertEqual(authority_response.status_code, 200)
        self.assertEqual(authority_response.json()["code"], "00000")
        self.assertEqual(mock_dji_state.payload_authority_requests[0]["payload"], {"payload_index": "88-0-0"})

        command_response = self.client.post(
            "/__mock-dji__/api/v1/control/devices/MOCK-GATEWAY-001/payload/commands",
            data=json.dumps({"cmd": "camera_photo_take", "data": {"payload_index": "88-0-0"}}),
            content_type="application/json",
            HTTP_X_AUTH_TOKEN=token,
        )
        self.assertEqual(command_response.status_code, 200)
        self.assertEqual(command_response.json()["code"], "00000")
        self.assertEqual(mock_dji_state.payload_command_requests[0]["payload"]["cmd"], "camera_photo_take")

        mock_dji_state.set_payload_command_error(
            gateway_sn="MOCK-GATEWAY-001",
            cmd="gimbal_reset",
            msg="The device is offline.",
        )
        failed_command_response = self.client.post(
            "/__mock-dji__/api/v1/control/devices/MOCK-GATEWAY-001/payload/commands",
            data=json.dumps({"cmd": "gimbal_reset", "data": {"payload_index": "88-0-0", "reset_mode": 0}}),
            content_type="application/json",
            HTTP_X_AUTH_TOKEN=token,
        )
        self.assertEqual(failed_command_response.status_code, 200)
        self.assertEqual(failed_command_response.json()["code"], "E0001")
        self.assertEqual(failed_command_response.json()["msg"], "The device is offline.")
