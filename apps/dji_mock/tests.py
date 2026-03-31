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
            "/__mock-dji__/api/v1/manage/workspaces/mock-workspace-001/devices/bound?domain=0",
            HTTP_X_AUTH_TOKEN=token,
        )
        self.assertEqual(devices_response.status_code, 200)
        body = devices_response.json()["data"]
        self.assertEqual(body["pagination"]["total"], 1)
        self.assertEqual(body["list"][0]["device_sn"], "MOCK-DRONE-001")

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
        dji_wayline_id = upload_response.json()["data"]["dji_wayline_id"]

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
