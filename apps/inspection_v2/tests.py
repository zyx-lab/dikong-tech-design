from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from apps.access.models import DirectoryStatus, Tenant, TenantStatus
from apps.iam_v2.models import (
    Department,
    FixedRole,
    ResourceShareGroup,
    ResourceShareGroupTargetDepartment,
    V2AccountProfile,
    V2AccountRoleAssignment,
)
from apps.inspection_v2.models import CloudMediaFile, MissionStatus
from apps.resource_v2.models import (
    BindingStatus,
    DjiConnection,
    DroneResource,
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

    def create_mission_by_api(self, user, *, route_id, drone_id, pilot_id, name="一号任务"):
        self.authenticate(user)
        response = self.client.post(
            "/api/v2/inspection/missions",
            {
                "name": name,
                "routeId": route_id,
                "droneId": drone_id,
                "pilotId": pilot_id,
                "remark": "首版闭环任务",
            },
            format="json",
        )
        self.assertEqual(response.status_code, 201, getattr(response, "data", response.content))
        return response.data["data"]

    def test_mission_lifecycle_should_create_session_record_and_cloud_media(self):
        route = self.create_route_by_api(self.owner_dispatcher)
        mission = self.create_mission_by_api(
            self.owner_dispatcher,
            route_id=route["id"],
            drone_id=self.drone.id,
            pilot_id=self.owner_pilot.id,
        )

        start_response = self.client.post(f"/api/v2/inspection/missions/{mission['id']}/start", {}, format="json")
        self.assertEqual(start_response.status_code, 200, getattr(start_response, "data", start_response.content))
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
        mission = self.create_mission_by_api(
            self.owner_dispatcher,
            route_id=route["id"],
            drone_id=self.drone.id,
            pilot_id=self.owner_pilot.id,
            name="实时权限任务",
        )
        self.client.post(f"/api/v2/inspection/missions/{mission['id']}/start", {}, format="json")

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
        mission = self.create_mission_by_api(
            self.owner_dispatcher,
            route_id=route["id"],
            drone_id=self.drone.id,
            pilot_id=self.owner_pilot.id,
            name="直播任务",
        )
        inactive_response = self.client.post(
            "/api/v2/inspection/live/start",
            {"droneId": self.drone.id, "video_id": f"{self.drone.device_sn}/88-0-0/normal-0"},
            format="json",
        )
        self.assertEqual(inactive_response.status_code, 409, getattr(inactive_response, "data", inactive_response.content))

        self.client.post(f"/api/v2/inspection/missions/{mission['id']}/start", {}, format="json")
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
