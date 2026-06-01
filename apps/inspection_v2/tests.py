from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from apps.access.models import DirectoryStatus, Tenant, TenantStatus
from apps.dji_bff.gateway import DjiGatewayUpstreamError
from apps.iam_v2.models import (
    Department,
    FixedRole,
    ResourceShareGroup,
    ResourceShareGroupTargetDepartment,
    V2AccountProfile,
    V2AccountRoleAssignment,
)
from apps.inspection_v2.models import CloudMediaFile, FlightSession, InspectionMission, MissionCloudExecution, MissionStatus
from apps.inspection_v2.services import apply_cloud_execution_event, apply_osd_telemetry
from apps.inspection_v2.management.commands.run_v2_dji_worker import V2DjiWorker
from apps.resource_v2.models import (
    BindingStatus,
    DjiConnection,
    DroneResource,
    GatewayResource,
    ResourceBinding,
    ResourceSharePermission,
    ResourceType,
)
from apps.workforce_v2.models import PilotProfile, PilotQualification

User = get_user_model()


def create_v2_actor(*, username: str, role_code: str | None, department: Department):
    user = User.objects.create_user(username=username, password="pass1234", status=1)
    profile = V2AccountProfile.objects.create(user=user, department=department)
    if role_code is not None:
        V2AccountRoleAssignment.objects.create(account_profile=profile, role_code=role_code, assigned_by_user=user)
    return user, profile


class InspectionV2ApiTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.tenant = Tenant.objects.create(code="inspection_v2_tenant", name="巡检 v2 租户", status=TenantStatus.ACTIVE)
        self.root = Department.objects.create(tenant=self.tenant, name="总部")
        self.owner_department = Department.objects.create(tenant=self.tenant, name="资源队", parent=self.root)
        self.other_department = Department.objects.create(tenant=self.tenant, name="任务队", parent=self.root)
        self.owner_dispatcher, _ = create_v2_actor(
            username="owner_dispatcher",
            role_code=FixedRole.TASK_MONITOR_DISPATCHER,
            department=self.owner_department,
        )
        self.owner_admin, _ = create_v2_actor(
            username="owner_admin",
            role_code=FixedRole.DEPARTMENT_ADMIN,
            department=self.owner_department,
        )
        self.other_dispatcher, _ = create_v2_actor(
            username="other_dispatcher",
            role_code=FixedRole.TASK_MONITOR_DISPATCHER,
            department=self.other_department,
        )
        self.owner_pilot_user, self.owner_pilot_account = create_v2_actor(
            username="owner_pilot",
            role_code=FixedRole.PILOT,
            department=self.owner_department,
        )
        self.owner_pilot = self.create_pilot(self.owner_pilot_account, "资源队飞手")
        self.other_pilot_user, self.other_pilot_account = create_v2_actor(
            username="other_pilot",
            role_code=FixedRole.PILOT,
            department=self.other_department,
        )
        self.other_pilot = self.create_pilot(self.other_pilot_account, "任务队飞手")
        self.drone = self.bind_drone(self.owner_department, self.owner_admin, "V2-DRONE-001")

    def authenticate(self, user):
        self.client.force_authenticate(user)

    def create_pilot(self, account_profile, name):
        pilot = PilotProfile.objects.create(account_profile=account_profile, display_name=name)
        PilotQualification.objects.create(
            pilot=pilot,
            qualification_type="多旋翼巡检",
            certificate_no=f"CERT-{pilot.id}",
            issued_at=timezone.now().date(),
            expires_at=timezone.now().date().replace(year=timezone.now().date().year + 1),
            status=DirectoryStatus.ACTIVE,
        )
        return pilot

    def bind_drone(self, department, actor, device_sn):
        connection = DjiConnection.objects.create(
            owner_department=department,
            name=f"{device_sn} connection",
            base_url="https://dji.example.test",
            username="admin",
            password="secret",
            workspace_id="workspace-001",
            access_token="token",
            created_by_user=actor,
        )
        drone = DroneResource.objects.create(device_sn=device_sn, name=f"{device_sn} 无人机", model="M30")
        ResourceBinding.objects.create(
            resource_type=ResourceType.DRONE,
            resource_object_id=drone.id,
            owner_department=department,
            dji_connection=connection,
            status=BindingStatus.ACTIVE,
            bound_by_user=actor,
        )
        return drone

    def bind_gateway(self, department, actor, device_sn, *, connection):
        gateway = GatewayResource.objects.create(device_sn=device_sn, name=f"{device_sn} 执行端", model="RC Plus")
        ResourceBinding.objects.create(
            resource_type=ResourceType.GATEWAY,
            resource_object_id=gateway.id,
            owner_department=department,
            dji_connection=connection,
            status=BindingStatus.ACTIVE,
            bound_by_user=actor,
        )
        return gateway

    def route_payload(self, name="一号航线"):
        return {
            "name": name,
            "defaultAltitude": "120.00",
            "defaultSpeed": "8.50",
            "waypoints": [
                {
                    "sequence": 1,
                    "latitude": "31.23040000",
                    "longitude": "121.47370000",
                    "altitude": "120.00",
                    "speed": "8.50",
                    "heading": "90.00",
                    "hoverSeconds": 3,
                },
                {
                    "sequence": 2,
                    "latitude": "31.23140000",
                    "longitude": "121.47470000",
                    "altitude": "125.00",
                    "speed": "8.50",
                    "heading": "180.00",
                    "hoverSeconds": 0,
                },
            ],
        }

    def create_route_by_api(self, user, name="一号航线"):
        self.authenticate(user)
        response = self.client.post("/api/v2/inspection/routes", self.route_payload(name), format="json")
        self.assertEqual(response.status_code, 201, getattr(response, "data", response.content))
        return response.data["data"]

    def create_mission_by_api(self, user, *, route_id, drone_id, pilot_id, executor_id=None, name="一号任务"):
        self.authenticate(user)
        payload = {
            "name": name,
            "routeId": route_id,
            "droneId": drone_id,
            "pilotId": pilot_id,
            "remark": "首版闭环任务",
        }
        if executor_id is not None:
            payload["executorId"] = executor_id
        response = self.client.post(
            "/api/v2/inspection/missions",
            payload,
            format="json",
        )
        self.assertEqual(response.status_code, 201, getattr(response, "data", response.content))
        return response.data["data"]

    def upload_route_kmz_by_api(self, route_id, connection):
        self.authenticate(self.owner_dispatcher)
        kmz_file = SimpleUploadedFile("route.kmz", b"fake-kmz-content", content_type="application/vnd.google-earth.kmz")
        with patch(
            "apps.inspection_v2.views.DjiConnectionGateway.upload_route",
            return_value={"dji_wayline_id": "wayline-kmz-001", "download_url": "/waylines/wayline-kmz-001/url"},
        ):
            response = self.client.post(
                f"/api/v2/inspection/routes/{route_id}/kmz",
                {"djiConnectionId": connection.id, "kmzFile": kmz_file},
                format="multipart",
            )
        self.assertEqual(response.status_code, 200, getattr(response, "data", response.content))
        return response.data["data"]

    def prepare_route_for_cloud_execution(self, route, *, gateway_sn="GATEWAY-TEST-001"):
        connection = DjiConnection.objects.get(owner_department=self.owner_department)
        connection.workspace_id = f"workspace-{gateway_sn.lower()}"
        connection.save(update_fields=["workspace_id", "updated_at"])
        executor = self.bind_gateway(self.owner_department, self.owner_admin, gateway_sn, connection=connection)
        self.upload_route_kmz_by_api(route["id"], connection)
        return connection, executor

    def start_cloud_mission_by_api(self, mission_id, *, dji_job_id):
        with patch("apps.inspection_v2.services.DjiConnectionGateway.create_mission", return_value={"dji_job_id": dji_job_id}):
            response = self.client.post(f"/api/v2/inspection/missions/{mission_id}/start", {}, format="json")
        self.assertEqual(response.status_code, 200, getattr(response, "data", response.content))
        return response

    def test_mission_lifecycle_should_create_session_record_and_cloud_media(self):
        route = self.create_route_by_api(self.owner_dispatcher)
        _connection, executor = self.prepare_route_for_cloud_execution(route, gateway_sn="GATEWAY-LIFECYCLE-001")
        mission = self.create_mission_by_api(
            self.owner_dispatcher,
            route_id=route["id"],
            drone_id=self.drone.id,
            executor_id=executor.id,
            pilot_id=self.owner_pilot.id,
        )

        start_response = self.start_cloud_mission_by_api(mission["id"], dji_job_id="dji-job-lifecycle")
        self.assertEqual(start_response.data["data"]["status"], MissionStatus.RUNNING)

        active_response = self.client.get("/api/v2/inspection/active-flights")
        self.assertEqual(active_response.status_code, 200, getattr(active_response, "data", active_response.content))
        self.assertEqual(active_response.data["data"]["total"], 1)
        session_id = active_response.data["data"]["list"][0]["id"]

        summary_response = self.client.get("/api/v2/resource/summary")
        self.assertEqual(summary_response.status_code, 200, getattr(summary_response, "data", summary_response.content))
        self.assertEqual(summary_response.data["data"]["drones"]["occupied"], 1)
        self.assertEqual(summary_response.data["data"]["pilots"]["total"], 1)
        self.assertEqual(
            summary_response.data["data"]["departments"][0],
            {
                "departmentId": self.owner_department.id,
                "departmentName": self.owner_department.name,
                "departmentPath": self.owner_department.path,
                "drones": 1,
                "docks": 0,
                "gateways": 1,
                "payloads": 0,
                "pilots": 1,
            },
        )

        telemetry_response = self.client.post(
            "/api/v2/inspection/telemetry/snapshots",
            {
                "sessionId": session_id,
                "latitude": "31.23040000",
                "longitude": "121.47370000",
                "altitude": "120.00",
                "speed": "9.10",
                "heading": "91.00",
                "batteryPercent": 88,
            },
            format="json",
        )
        self.assertEqual(telemetry_response.status_code, 200, getattr(telemetry_response, "data", telemetry_response.content))
        self.assertEqual(telemetry_response.data["data"]["batteryPercent"], 88)

        captured_at = timezone.now().isoformat()
        with patch(
            "apps.inspection_v2.services.DjiConnectionGateway.list_media_files",
            return_value=[
                {
                    "file_id": "cloud-photo-001",
                    "device_sn": self.drone.device_sn,
                    "fileName": "inspection.jpg",
                    "mediaType": "photo",
                    "capturedAt": captured_at,
                    "thumbnailUrl": "https://media.example.test/inspection-thumb.jpg",
                    "downloadUrl": "https://media.example.test/inspection.jpg",
                }
            ],
        ):
            complete_response = self.client.post(f"/api/v2/inspection/missions/{mission['id']}/complete", {}, format="json")

        self.assertEqual(complete_response.status_code, 200, getattr(complete_response, "data", complete_response.content))
        self.assertEqual(complete_response.data["data"]["photoCount"], 1)
        self.assertEqual(CloudMediaFile.objects.filter(cloud_file_id="cloud-photo-001").count(), 1)

        media_response = self.client.get("/api/v2/inspection/media-files")
        self.assertEqual(media_response.status_code, 200, getattr(media_response, "data", media_response.content))
        self.assertEqual(media_response.data["data"]["total"], 1)

    def test_route_kmz_upload_should_publish_to_selected_dji_connection(self):
        route = self.create_route_by_api(self.owner_dispatcher, name="KMZ 航线")
        connection = DjiConnection.objects.get(owner_department=self.owner_department)
        connection.workspace_id = "workspace-kmz-001"
        connection.save(update_fields=["workspace_id", "updated_at"])

        self.authenticate(self.owner_dispatcher)
        kmz_file = SimpleUploadedFile("route.kmz", b"fake-kmz-content", content_type="application/vnd.google-earth.kmz")
        with patch(
            "apps.inspection_v2.views.DjiConnectionGateway.upload_route",
            return_value={"dji_wayline_id": "wayline-kmz-001", "download_url": "/waylines/wayline-kmz-001/url"},
        ) as upload_route:
            response = self.client.post(
                f"/api/v2/inspection/routes/{route['id']}/kmz",
                {"djiConnectionId": connection.id, "kmzFile": kmz_file},
                format="multipart",
            )

        self.assertEqual(response.status_code, 200, getattr(response, "data", response.content))
        self.assertEqual(response.data["data"]["djiFileId"], "wayline-kmz-001")
        self.assertEqual(response.data["data"]["workspaceId"], "workspace-kmz-001")
        upload_route.assert_called_once()

    def test_mission_start_should_create_dji_immediate_job_from_route_kmz_and_executor(self):
        route = self.create_route_by_api(self.owner_dispatcher, name="执行航线")
        connection = DjiConnection.objects.get(owner_department=self.owner_department)
        connection.workspace_id = "workspace-job-001"
        connection.save(update_fields=["workspace_id", "updated_at"])
        executor = self.bind_gateway(self.owner_department, self.owner_admin, "GATEWAY-JOB-001", connection=connection)
        self.upload_route_kmz_by_api(route["id"], connection)
        mission = self.create_mission_by_api(
            self.owner_dispatcher,
            route_id=route["id"],
            drone_id=self.drone.id,
            executor_id=executor.id,
            pilot_id=self.owner_pilot.id,
        )

        with patch(
            "apps.inspection_v2.services.DjiConnectionGateway.create_mission",
            return_value={"dji_job_id": "dji-job-001"},
        ) as create_job:
            start_response = self.client.post(f"/api/v2/inspection/missions/{mission['id']}/start", {}, format="json")

        self.assertEqual(start_response.status_code, 200, getattr(start_response, "data", start_response.content))
        self.assertEqual(start_response.data["data"]["status"], MissionStatus.RUNNING)
        self.assertEqual(start_response.data["data"]["cloudExecution"]["djiJobId"], "dji-job-001")
        execution = MissionCloudExecution.objects.get(mission_id=mission["id"])
        self.assertEqual(execution.dji_job_id, "dji-job-001")
        self.assertEqual(execution.executor_sn, "GATEWAY-JOB-001")
        create_job.assert_called_once()

    def test_mission_start_upstream_failure_should_keep_local_task_pending(self):
        route = self.create_route_by_api(self.owner_dispatcher, name="执行失败航线")
        _connection, executor = self.prepare_route_for_cloud_execution(route, gateway_sn="GATEWAY-FAIL-001")
        mission = self.create_mission_by_api(
            self.owner_dispatcher,
            route_id=route["id"],
            drone_id=self.drone.id,
            executor_id=executor.id,
            pilot_id=self.owner_pilot.id,
        )

        with patch(
            "apps.inspection_v2.services.DjiConnectionGateway.create_mission",
            side_effect=DjiGatewayUpstreamError(
                "DJI upstream business error",
                status_code=502,
                data={"code": "E0001", "msg": "210003 device does not support flight task"},
            ),
        ) as create_job:
            response = self.client.post(f"/api/v2/inspection/missions/{mission['id']}/start", {}, format="json")

        self.assertEqual(response.status_code, 502, getattr(response, "data", response.content))
        persisted = InspectionMission.objects.get(pk=mission["id"])
        self.assertEqual(persisted.status, MissionStatus.PENDING)
        self.assertFalse(FlightSession.objects.filter(mission_id=mission["id"]).exists())
        self.assertFalse(MissionCloudExecution.objects.filter(mission_id=mission["id"]).exists())
        create_job.assert_called_once()

    def test_cloud_execution_success_event_should_finish_mission_and_archive_media(self):
        route = self.create_route_by_api(self.owner_dispatcher, name="事件航线")
        connection = DjiConnection.objects.get(owner_department=self.owner_department)
        connection.workspace_id = "workspace-event-001"
        connection.save(update_fields=["workspace_id", "updated_at"])
        executor = self.bind_gateway(self.owner_department, self.owner_admin, "GATEWAY-EVENT-001", connection=connection)
        self.upload_route_kmz_by_api(route["id"], connection)
        mission = self.create_mission_by_api(
            self.owner_dispatcher,
            route_id=route["id"],
            drone_id=self.drone.id,
            executor_id=executor.id,
            pilot_id=self.owner_pilot.id,
        )
        with patch("apps.inspection_v2.services.DjiConnectionGateway.create_mission", return_value={"dji_job_id": "dji-job-event"}):
            self.client.post(f"/api/v2/inspection/missions/{mission['id']}/start", {}, format="json")

        captured_at = timezone.now().isoformat()
        with patch(
            "apps.inspection_v2.services.DjiConnectionGateway.list_media_files",
            return_value=[
                {
                    "file_id": "event-photo-001",
                    "device_sn": self.drone.device_sn,
                    "fileName": "event.jpg",
                    "mediaType": "photo",
                    "capturedAt": captured_at,
                    "downloadUrl": "https://media.example.test/event.jpg",
                }
            ],
        ):
            result = apply_cloud_execution_event(
                dji_job_id="dji-job-event",
                status="ok",
                payload={"data": {"output": {"progress": 100, "result_code": 0}}},
            )

        self.assertEqual(result["missionStatus"], MissionStatus.COMPLETED)
        self.assertEqual(result["media"]["photoCount"], 1)
        self.assertTrue(CloudMediaFile.objects.filter(cloud_file_id="event-photo-001").exists())
        self.assertEqual(MissionCloudExecution.objects.get(dji_job_id="dji-job-event").progress_percent, 100)

    def test_osd_event_should_update_running_flight_telemetry(self):
        route = self.create_route_by_api(self.owner_dispatcher, name="OSD 航线")
        connection = DjiConnection.objects.get(owner_department=self.owner_department)
        connection.workspace_id = "workspace-osd-001"
        connection.save(update_fields=["workspace_id", "updated_at"])
        executor = self.bind_gateway(self.owner_department, self.owner_admin, "GATEWAY-OSD-001", connection=connection)
        self.upload_route_kmz_by_api(route["id"], connection)
        mission = self.create_mission_by_api(
            self.owner_dispatcher,
            route_id=route["id"],
            drone_id=self.drone.id,
            executor_id=executor.id,
            pilot_id=self.owner_pilot.id,
        )
        with patch("apps.inspection_v2.services.DjiConnectionGateway.create_mission", return_value={"dji_job_id": "dji-job-osd"}):
            self.client.post(f"/api/v2/inspection/missions/{mission['id']}/start", {}, format="json")

        result = apply_osd_telemetry(
            device_sn=self.drone.device_sn,
            payload={
                "timestamp": int(timezone.now().timestamp() * 1000),
                "data": {
                    "latitude": 31.2304,
                    "longitude": 121.4737,
                    "height": 120.5,
                    "horizontal_speed": 8.2,
                    "attitude_head": 91.0,
                    "battery": {"capacity_percent": 87},
                },
            },
        )

        self.assertEqual(result["updated"], 1)
        active_response = self.client.get("/api/v2/inspection/active-flights")
        telemetry = active_response.data["data"]["list"][0]["telemetry"]
        self.assertEqual(telemetry["batteryPercent"], 87)
        self.assertEqual(telemetry["latitude"], "31.23040000")

    def test_cancel_running_cloud_mission_should_cancel_dji_job_before_local_close(self):
        route = self.create_route_by_api(self.owner_dispatcher, name="取消航线")
        connection = DjiConnection.objects.get(owner_department=self.owner_department)
        connection.workspace_id = "workspace-cancel-001"
        connection.save(update_fields=["workspace_id", "updated_at"])
        executor = self.bind_gateway(self.owner_department, self.owner_admin, "GATEWAY-CANCEL-001", connection=connection)
        self.upload_route_kmz_by_api(route["id"], connection)
        mission = self.create_mission_by_api(
            self.owner_dispatcher,
            route_id=route["id"],
            drone_id=self.drone.id,
            executor_id=executor.id,
            pilot_id=self.owner_pilot.id,
        )
        with patch("apps.inspection_v2.services.DjiConnectionGateway.create_mission", return_value={"dji_job_id": "dji-job-cancel"}):
            self.client.post(f"/api/v2/inspection/missions/{mission['id']}/start", {}, format="json")

        with patch("apps.inspection_v2.services.DjiConnectionGateway.cancel_mission", return_value={}) as cancel_job:
            cancel_response = self.client.post(
                f"/api/v2/inspection/missions/{mission['id']}/cancel",
                {"reason": "调度取消"},
                format="json",
            )

        self.assertEqual(cancel_response.status_code, 200, getattr(cancel_response, "data", cancel_response.content))
        self.assertEqual(cancel_response.data["data"]["status"], MissionStatus.CANCELED)
        cancel_job.assert_called_once_with("dji-job-cancel")

    def test_v2_dji_worker_should_dispatch_osd_and_flighttask_progress_messages(self):
        worker = V2DjiWorker()

        with patch("apps.inspection_v2.management.commands.run_v2_dji_worker.apply_osd_telemetry") as osd_handler:
            worker.handle_message(
                "thing/product/DRONE-WORKER-001/osd",
                {"data": {"latitude": 31.1}},
            )
        osd_handler.assert_called_once_with(device_sn="DRONE-WORKER-001", payload={"data": {"latitude": 31.1}})

        with patch("apps.inspection_v2.management.commands.run_v2_dji_worker.apply_cloud_execution_event") as progress_handler:
            worker.handle_message(
                "thing/product/DRONE-WORKER-001/events",
                {"method": "flighttask_progress", "data": {"job_id": "job-worker-001", "status": "ok", "progress": 100}},
            )
        progress_handler.assert_called_once_with(
            dji_job_id="job-worker-001",
            status="ok",
            payload={"method": "flighttask_progress", "data": {"job_id": "job-worker-001", "status": "ok", "progress": 100}},
        )

    def test_v2_dji_worker_command_should_support_once_mode(self):
        with patch(
            "apps.inspection_v2.management.commands.run_v2_dji_worker.V2DjiWorker.run_once",
            return_value={"connections": 0},
        ) as run_once:
            call_command("run_v2_dji_worker", "--once")
        run_once.assert_called_once()

    def test_pilot_should_only_see_own_execution_data_and_cannot_cancel_task(self):
        second_pilot_user, second_pilot_account = create_v2_actor(
            username="owner_second_pilot",
            role_code=FixedRole.PILOT,
            department=self.owner_department,
        )
        second_pilot = self.create_pilot(second_pilot_account, "资源队二号飞手")
        first_route = self.create_route_by_api(self.owner_dispatcher, name="飞手一号航线")
        first_mission = self.create_mission_by_api(
            self.owner_dispatcher,
            route_id=first_route["id"],
            drone_id=self.drone.id,
            pilot_id=self.owner_pilot.id,
            name="飞手一号任务",
        )
        second_route = self.create_route_by_api(self.owner_dispatcher, name="飞手二号航线")
        second_mission = self.create_mission_by_api(
            self.owner_dispatcher,
            route_id=second_route["id"],
            drone_id=self.drone.id,
            pilot_id=second_pilot.id,
            name="飞手二号任务",
        )

        self.authenticate(self.owner_pilot_user)
        missions_response = self.client.get("/api/v2/inspection/missions")
        routes_response = self.client.get("/api/v2/inspection/routes")
        cancel_response = self.client.post(
            f"/api/v2/inspection/missions/{first_mission['id']}/cancel",
            {"reason": "飞手不能取消任务"},
            format="json",
        )

        self.assertEqual(missions_response.status_code, 200, getattr(missions_response, "data", missions_response.content))
        self.assertEqual([item["id"] for item in missions_response.data["data"]["list"]], [first_mission["id"]])
        self.assertEqual(routes_response.status_code, 200, getattr(routes_response, "data", routes_response.content))
        self.assertEqual([item["id"] for item in routes_response.data["data"]["list"]], [first_route["id"]])
        self.assertEqual(cancel_response.status_code, 403, getattr(cancel_response, "data", cancel_response.content))

        self.authenticate(second_pilot_user)
        second_missions_response = self.client.get("/api/v2/inspection/missions")
        self.assertEqual([item["id"] for item in second_missions_response.data["data"]["list"]], [second_mission["id"]])

    def test_active_flights_should_be_limited_to_dispatchers_and_assigned_pilot(self):
        route = self.create_route_by_api(self.owner_dispatcher, name="实时权限航线")
        _connection, executor = self.prepare_route_for_cloud_execution(route, gateway_sn="GATEWAY-ACTIVE-001")
        mission = self.create_mission_by_api(
            self.owner_dispatcher,
            route_id=route["id"],
            drone_id=self.drone.id,
            executor_id=executor.id,
            pilot_id=self.owner_pilot.id,
            name="实时权限任务",
        )
        self.start_cloud_mission_by_api(mission["id"], dji_job_id="dji-job-active")

        self.authenticate(self.owner_admin)
        admin_response = self.client.get("/api/v2/inspection/active-flights")
        self.assertEqual(admin_response.status_code, 403, getattr(admin_response, "data", admin_response.content))

        self.authenticate(self.other_pilot_user)
        other_pilot_response = self.client.get("/api/v2/inspection/active-flights")
        self.assertEqual(other_pilot_response.status_code, 200, getattr(other_pilot_response, "data", other_pilot_response.content))
        self.assertEqual(other_pilot_response.data["data"]["total"], 0)

        self.authenticate(self.owner_pilot_user)
        assigned_pilot_response = self.client.get("/api/v2/inspection/active-flights")
        self.assertEqual(assigned_pilot_response.status_code, 200, getattr(assigned_pilot_response, "data", assigned_pilot_response.content))
        self.assertEqual(assigned_pilot_response.data["data"]["total"], 1)

    def test_shared_resource_should_require_use_permission_for_new_mission(self):
        group = ResourceShareGroup.objects.create(owner_department=self.owner_department, name="任务共享")
        ResourceShareGroupTargetDepartment.objects.create(share_group=group, department=self.other_department)
        share = ResourceSharePermission.objects.create(
            share_group=group,
            resource_type=ResourceType.DRONE,
            resource_object_id=self.drone.id,
            permissions=["view", "monitor"],
        )
        route = self.create_route_by_api(self.other_dispatcher, name="共享资源航线")
        self.authenticate(self.other_dispatcher)
        denied_response = self.client.post(
            "/api/v2/inspection/missions",
            {
                "name": "缺少 use 的任务",
                "routeId": route["id"],
                "droneId": self.drone.id,
                "pilotId": self.other_pilot.id,
            },
            format="json",
        )
        self.assertEqual(denied_response.status_code, 403, getattr(denied_response, "data", denied_response.content))

        share.permissions = ["view", "monitor", "use"]
        share.save(update_fields=["permissions", "updated_at"])
        allowed_response = self.client.post(
            "/api/v2/inspection/missions",
            {
                "name": "具备 use 的任务",
                "routeId": route["id"],
                "droneId": self.drone.id,
                "pilotId": self.other_pilot.id,
            },
            format="json",
        )
        self.assertEqual(allowed_response.status_code, 201, getattr(allowed_response, "data", allowed_response.content))
        self.assertEqual(allowed_response.data["data"]["creatorDepartmentId"], self.other_department.id)
        self.assertEqual(allowed_response.data["data"]["primaryResourceOwnerDepartmentId"], self.owner_department.id)

    def test_live_start_should_require_active_flight_and_proxy_dji_control(self):
        route = self.create_route_by_api(self.owner_dispatcher, name="直播航线")
        _connection, executor = self.prepare_route_for_cloud_execution(route, gateway_sn="GATEWAY-LIVE-001")
        mission = self.create_mission_by_api(
            self.owner_dispatcher,
            route_id=route["id"],
            drone_id=self.drone.id,
            executor_id=executor.id,
            pilot_id=self.owner_pilot.id,
            name="直播任务",
        )
        inactive_response = self.client.post(
            "/api/v2/inspection/live/start",
            {"droneId": self.drone.id, "video_id": f"{self.drone.device_sn}/88-0-0/normal-0"},
            format="json",
        )
        self.assertEqual(inactive_response.status_code, 409, getattr(inactive_response, "data", inactive_response.content))

        self.start_cloud_mission_by_api(mission["id"], dji_job_id="dji-job-live")
        with patch(
            "apps.inspection_v2.views.DjiConnectionGateway.start_live",
            return_value={"webrtc_url": "https://live.example.test/webrtc", "rtmp_url": "rtmp://live.example.test/app"},
        ) as start_live:
            live_response = self.client.post(
                "/api/v2/inspection/live/start",
                {"droneId": self.drone.id, "video_id": f"{self.drone.device_sn}/88-0-0/normal-0", "url_type": 1},
                format="json",
            )

        self.assertEqual(live_response.status_code, 200, getattr(live_response, "data", live_response.content))
        self.assertEqual(live_response.data["data"]["webrtc_url"], "https://live.example.test/webrtc")
        start_live.assert_called_once()
