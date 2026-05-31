from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

from apps.access.models import DirectoryStatus, Tenant, TenantStatus
from apps.access.test_support import ensure_tenant_role_binding
from apps.dji_bff.gateway import GatewayResponse
from apps.dji_bff.models import DjiDeviceIndex
from apps.drone.models import Drone
from apps.flight_record.models import FlightRecord, FlightRecordStatus
from apps.iam_v2.models import (
    Department,
    FixedRole,
    ResourceShareGroup,
    ResourceShareGroupTargetDepartment,
    V2AccountProfile,
    V2AccountRoleAssignment,
)
from apps.media_file.models import MediaFile, MediaType
from apps.mission.models import Mission, MissionStatus
from apps.resource_v2.gateway import DjiConnectionGateway
from apps.resource_v2.models import (
    BindingActionType,
    BindingStatus,
    DjiConnection,
    DockResource,
    DroneResource,
    ResourceBinding,
    ResourceBindingHistory,
    ResourceSharePermission,
    ResourceType,
    V2AuditLog,
)
from apps.route.models import Route

User = get_user_model()


def create_v2_actor(*, username: str, role_code: str | None, department: Department, is_platform_admin: bool = False):
    user = User.objects.create_user(username=username, password="pass1234", status=1, is_platform_admin=is_platform_admin)
    profile = V2AccountProfile.objects.create(user=user, department=department)
    if role_code is not None:
        V2AccountRoleAssignment.objects.create(account_profile=profile, role_code=role_code, assigned_by_user=user)
    return user


class FakeDiscoveryGateway:
    calls = []

    def __init__(self, connection):
        self.connection = connection

    def discover(self):
        self.calls.append(self.connection.id)
        return {
            "drones": [
                {
                    "device_sn": "DRONE-SN-001",
                    "name": "巡检无人机",
                    "model": "Matrice 30T",
                    "online_status": True,
                    "firmware_version": "v1.0.0",
                    "firmware_status": "1",
                    "last_payload": {"device_sn": "DRONE-SN-001"},
                }
            ],
            "docks": [
                {
                    "device_sn": "DOCK-SN-001",
                    "name": "机场一号",
                    "model": "Dock 2",
                    "online_status": True,
                    "firmware_version": "v1.0.0",
                    "firmware_status": "1",
                    "last_payload": {"device_sn": "DOCK-SN-001"},
                }
            ],
        }


class ResourceV2ApiTests(TestCase):
    def setUp(self):
        super().setUp()
        FakeDiscoveryGateway.calls = []
        self.client = APIClient()
        self.tenant = Tenant.objects.create(code="resource_v2_tenant", name="资源租户", status=TenantStatus.ACTIVE)
        self.root = Department.objects.create(tenant=self.tenant, name="总部")
        self.child = Department.objects.create(tenant=self.tenant, name="飞行队", parent=self.root)
        self.other = Department.objects.create(tenant=self.tenant, name="保障队", parent=self.root)
        self.root_admin = create_v2_actor(
            username="root_admin",
            role_code=FixedRole.DEPARTMENT_ADMIN,
            department=self.root,
        )
        self.child_admin = create_v2_actor(
            username="child_admin",
            role_code=FixedRole.DEPARTMENT_ADMIN,
            department=self.child,
        )
        self.other_admin = create_v2_actor(
            username="other_admin",
            role_code=FixedRole.DEPARTMENT_ADMIN,
            department=self.other,
        )
        self.other_dispatcher = create_v2_actor(
            username="other_dispatcher",
            role_code=FixedRole.TASK_MONITOR_DISPATCHER,
            department=self.other,
        )
        self.no_role_user = create_v2_actor(
            username="no_role_user",
            role_code=None,
            department=self.child,
        )
        self.legacy_platform_admin = create_v2_actor(
            username="legacy_platform_admin",
            role_code=None,
            department=self.child,
            is_platform_admin=True,
        )
        self.platform_super = create_v2_actor(
            username="platform_super",
            role_code=FixedRole.PLATFORM_SUPER_ADMIN,
            department=self.root,
        )

    def authenticate(self, user):
        self.client.force_authenticate(user)

    def create_legacy_pilot(self, username: str):
        pilot_user = User.objects.create_user(username=username, password="pass1234", status=1)
        _tenant, member, _role = ensure_tenant_role_binding(
            pilot_user,
            tenant=self.tenant,
            role_code="pilot_operator",
            role_name="飞手",
        )
        return member

    def bind_v2_drone(self, *, department: Department, actor, device_sn: str):
        connection = DjiConnection.objects.create(
            owner_department=department,
            name=f"{device_sn} DJI",
            base_url=f"https://{device_sn.lower()}.example.test",
            username="adminPC1",
            password="secret",
            created_by_user=actor,
        )
        resource = DroneResource.objects.create(device_sn=device_sn, name=f"{device_sn} 资源", model="M30")
        ResourceBinding.objects.create(
            resource_type=ResourceType.DRONE,
            resource_object_id=resource.id,
            owner_department=department,
            dji_connection=connection,
            status=BindingStatus.ACTIVE,
            bound_by_user=actor,
        )
        return resource

    def create_legacy_business_set(self, *, device_sn: str, name_prefix: str, pilot):
        drone = Drone.objects.create(
            tenant=self.tenant,
            code=f"{name_prefix}-DRONE",
            name=f"{name_prefix}无人机",
            model="M30",
            device_sn=device_sn,
        )
        route = Route.objects.create(tenant=self.tenant, name=f"{name_prefix}航线")
        mission = Mission.objects.create(
            tenant=self.tenant,
            name=f"{name_prefix}任务",
            route=route,
            drone=drone,
            pilot=pilot,
            status=MissionStatus.PENDING,
        )
        flight_record = FlightRecord.objects.create(
            tenant=self.tenant,
            flight_no=f"FR-{name_prefix}",
            mission=mission,
            drone=drone,
            pilot=pilot,
            status=FlightRecordStatus.COMPLETED,
        )
        media_file = MediaFile.objects.create(
            tenant=self.tenant,
            flight_record=flight_record,
            mission=mission,
            device_sn=device_sn,
            media_type=MediaType.PHOTO,
            file_name=f"{name_prefix}.jpg",
            file_url=f"https://media.example.test/{name_prefix}.jpg",
            thumbnail_url=f"https://media.example.test/{name_prefix}.thumb.jpg",
        )
        return {
            "drone": drone,
            "route": route,
            "mission": mission,
            "flight_record": flight_record,
            "media_file": media_file,
        }

    def assert_business_read_totals(self, expected_total: int):
        endpoints = [
            "/api/v2/resource/routes",
            "/api/v2/resource/missions",
            "/api/v2/resource/flight-records",
            "/api/v2/resource/media-files",
        ]
        for endpoint in endpoints:
            with self.subTest(endpoint=endpoint):
                response = self.client.get(endpoint)
                self.assertEqual(response.status_code, 200, getattr(response, "data", response.content))
                self.assertEqual(response.data["data"]["total"], expected_total)

    def test_v2_resource_endpoints_should_require_fixed_role_operation_permissions(self):
        self.authenticate(self.no_role_user)

        endpoints = [
            "/api/v2/resource/drones",
            "/api/v2/resource/docks",
            "/api/v2/resource/routes",
            "/api/v2/resource/missions",
            "/api/v2/resource/flight-records",
            "/api/v2/resource/media-files",
            "/api/v2/resource/dji-connections",
            "/api/v2/resource/audit-logs",
        ]
        for endpoint in endpoints:
            with self.subTest(endpoint=endpoint):
                response = self.client.get(endpoint)
                self.assertEqual(response.status_code, 403, getattr(response, "data", response.content))

        self.authenticate(self.legacy_platform_admin)
        response = self.client.get("/api/v2/resource/drones")
        self.assertEqual(response.status_code, 403, getattr(response, "data", response.content))

    def test_business_role_should_read_visible_resources_but_not_management_surfaces(self):
        self.bind_v2_drone(department=self.other, actor=self.other_admin, device_sn="BIZ-ROLE-VISIBLE")
        pilot = self.create_legacy_pilot("business_role_pilot")
        visible = self.create_legacy_business_set(device_sn="BIZ-ROLE-VISIBLE", name_prefix="business-role", pilot=pilot)
        V2AuditLog.objects.create(
            action="other_department_action",
            actor_user=self.other_admin,
            actor_department=self.other,
            target_type="manual",
        )
        self.authenticate(self.other_dispatcher)

        drones_response = self.client.get("/api/v2/resource/drones")
        missions_response = self.client.get("/api/v2/resource/missions")
        connections_response = self.client.get("/api/v2/resource/dji-connections")
        audit_response = self.client.get("/api/v2/resource/audit-logs")

        self.assertEqual(drones_response.status_code, 200, getattr(drones_response, "data", drones_response.content))
        self.assertEqual(missions_response.status_code, 200, getattr(missions_response, "data", missions_response.content))
        self.assertEqual(drones_response.data["data"]["total"], 1)
        self.assertEqual(missions_response.data["data"]["list"][0]["id"], visible["mission"].id)
        self.assertEqual(connections_response.status_code, 403, getattr(connections_response, "data", connections_response.content))
        self.assertEqual(audit_response.status_code, 403, getattr(audit_response, "data", audit_response.content))

    def test_business_read_apis_should_filter_by_v2_visible_drone_resources(self):
        self.bind_v2_drone(department=self.child, actor=self.child_admin, device_sn="BIZ-VISIBLE-DRONE")
        self.bind_v2_drone(department=self.other, actor=self.other_admin, device_sn="BIZ-HIDDEN-DRONE")
        pilot = self.create_legacy_pilot("business_pilot")
        visible = self.create_legacy_business_set(device_sn="BIZ-VISIBLE-DRONE", name_prefix="visible", pilot=pilot)
        hidden = self.create_legacy_business_set(device_sn="BIZ-HIDDEN-DRONE", name_prefix="hidden", pilot=pilot)
        orphan_route = Route.objects.create(tenant=self.tenant, name="孤立航线")

        self.authenticate(self.child_admin)
        routes_response = self.client.get("/api/v2/resource/routes")
        missions_response = self.client.get("/api/v2/resource/missions")
        records_response = self.client.get("/api/v2/resource/flight-records")
        media_response = self.client.get("/api/v2/resource/media-files")

        self.assertEqual(routes_response.status_code, 200, getattr(routes_response, "data", routes_response.content))
        self.assertEqual(missions_response.status_code, 200, getattr(missions_response, "data", missions_response.content))
        self.assertEqual(records_response.status_code, 200, getattr(records_response, "data", records_response.content))
        self.assertEqual(media_response.status_code, 200, getattr(media_response, "data", media_response.content))
        self.assertEqual([item["id"] for item in routes_response.data["data"]["list"]], [visible["route"].id])
        self.assertEqual([item["id"] for item in missions_response.data["data"]["list"]], [visible["mission"].id])
        self.assertEqual([item["id"] for item in records_response.data["data"]["list"]], [visible["flight_record"].id])
        self.assertEqual([item["id"] for item in media_response.data["data"]["list"]], [visible["media_file"].id])
        self.assertNotIn("downloadUrl", media_response.data["data"]["list"][0])
        self.assertNotIn("playbackUrl", media_response.data["data"]["list"][0])
        self.assertNotIn("previewUrl", media_response.data["data"]["list"][0])

        visible_detail_response = self.client.get(f"/api/v2/resource/missions/{visible['mission'].id}")
        hidden_detail_response = self.client.get(f"/api/v2/resource/missions/{hidden['mission'].id}")
        orphan_route_response = self.client.get(f"/api/v2/resource/routes/{orphan_route.id}")
        self.assertEqual(visible_detail_response.status_code, 200, getattr(visible_detail_response, "data", visible_detail_response.content))
        self.assertEqual(hidden_detail_response.status_code, 404, getattr(hidden_detail_response, "data", hidden_detail_response.content))
        self.assertEqual(orphan_route_response.status_code, 404, getattr(orphan_route_response, "data", orphan_route_response.content))

        self.authenticate(self.root_admin)
        parent_response = self.client.get("/api/v2/resource/missions")
        self.assertEqual(parent_response.status_code, 200, getattr(parent_response, "data", parent_response.content))
        self.assertEqual(
            [item["id"] for item in parent_response.data["data"]["list"]],
            [hidden["mission"].id, visible["mission"].id],
        )

        self.authenticate(self.other_admin)
        sibling_response = self.client.get("/api/v2/resource/missions")
        self.assertEqual(sibling_response.status_code, 200, getattr(sibling_response, "data", sibling_response.content))
        self.assertEqual([item["id"] for item in sibling_response.data["data"]["list"]], [hidden["mission"].id])

        self.authenticate(self.platform_super)
        platform_route_response = self.client.get("/api/v2/resource/routes")
        self.assertEqual(platform_route_response.status_code, 200, getattr(platform_route_response, "data", platform_route_response.content))
        platform_route_ids = {item["id"] for item in platform_route_response.data["data"]["list"]}
        self.assertTrue({visible["route"].id, hidden["route"].id, orphan_route.id}.issubset(platform_route_ids))

    def test_business_read_apis_should_include_shared_resources_and_drop_unbound_resources(self):
        resource = self.bind_v2_drone(department=self.child, actor=self.child_admin, device_sn="BIZ-SHARED-DRONE")
        pilot = self.create_legacy_pilot("shared_business_pilot")
        shared = self.create_legacy_business_set(device_sn="BIZ-SHARED-DRONE", name_prefix="shared", pilot=pilot)
        group = ResourceShareGroup.objects.create(owner_department=self.child, name="业务共享")
        ResourceShareGroupTargetDepartment.objects.create(share_group=group, department=self.other)
        ResourceSharePermission.objects.create(
            share_group=group,
            resource_type=ResourceType.DRONE,
            resource_object_id=resource.id,
            permissions=["view", "monitor"],
        )

        self.authenticate(self.other_dispatcher)
        routes_response = self.client.get("/api/v2/resource/routes")
        missions_response = self.client.get("/api/v2/resource/missions")
        records_response = self.client.get("/api/v2/resource/flight-records")
        media_response = self.client.get("/api/v2/resource/media-files")

        self.assertEqual(routes_response.status_code, 200, getattr(routes_response, "data", routes_response.content))
        self.assertEqual(missions_response.status_code, 200, getattr(missions_response, "data", missions_response.content))
        self.assertEqual(records_response.status_code, 200, getattr(records_response, "data", records_response.content))
        self.assertEqual(media_response.status_code, 200, getattr(media_response, "data", media_response.content))
        self.assertEqual(routes_response.data["data"]["list"][0]["id"], shared["route"].id)
        self.assertEqual(missions_response.data["data"]["list"][0]["id"], shared["mission"].id)
        self.assertEqual(records_response.data["data"]["list"][0]["id"], shared["flight_record"].id)
        self.assertEqual(media_response.data["data"]["list"][0]["id"], shared["media_file"].id)

        ResourceBinding.objects.filter(
            resource_type=ResourceType.DRONE,
            resource_object_id=resource.id,
            status=BindingStatus.ACTIVE,
        ).update(status=BindingStatus.UNBOUND)

        no_routes_response = self.client.get("/api/v2/resource/routes")
        no_missions_response = self.client.get("/api/v2/resource/missions")
        no_records_response = self.client.get("/api/v2/resource/flight-records")
        no_media_response = self.client.get("/api/v2/resource/media-files")
        self.assertEqual(no_routes_response.data["data"]["total"], 0)
        self.assertEqual(no_missions_response.data["data"]["total"], 0)
        self.assertEqual(no_records_response.data["data"]["total"], 0)
        self.assertEqual(no_media_response.data["data"]["total"], 0)

    def test_business_read_apis_should_drop_shared_visibility_when_share_group_changes(self):
        resource = self.bind_v2_drone(department=self.child, actor=self.child_admin, device_sn="BIZ-SHARE-GATE")
        pilot = self.create_legacy_pilot("share_gate_business_pilot")
        self.create_legacy_business_set(device_sn="BIZ-SHARE-GATE", name_prefix="share-gate", pilot=pilot)
        group = ResourceShareGroup.objects.create(owner_department=self.child, name="业务共享门禁")
        ResourceShareGroupTargetDepartment.objects.create(share_group=group, department=self.other)
        resource_share = ResourceSharePermission.objects.create(
            share_group=group,
            resource_type=ResourceType.DRONE,
            resource_object_id=resource.id,
            permissions=["view", "monitor"],
        )

        self.authenticate(self.other_dispatcher)
        self.assert_business_read_totals(1)

        self.authenticate(self.child_admin)
        disable_response = self.client.put(
            f"/api/v2/resource/share-groups/{group.id}",
            {"name": group.name, "status": DirectoryStatus.DISABLED},
            format="json",
        )
        self.assertEqual(disable_response.status_code, 200, getattr(disable_response, "data", disable_response.content))

        self.authenticate(self.other_dispatcher)
        self.assert_business_read_totals(0)

        self.authenticate(self.child_admin)
        enable_response = self.client.put(
            f"/api/v2/resource/share-groups/{group.id}",
            {"name": group.name, "status": DirectoryStatus.ACTIVE},
            format="json",
        )
        self.assertEqual(enable_response.status_code, 200, getattr(enable_response, "data", enable_response.content))

        self.authenticate(self.other_dispatcher)
        self.assert_business_read_totals(1)

        self.authenticate(self.child_admin)
        delete_share_response = self.client.delete(
            f"/api/v2/resource/share-groups/{group.id}/resources/{resource_share.id}"
        )
        self.assertEqual(delete_share_response.status_code, 200, getattr(delete_share_response, "data", delete_share_response.content))

        self.authenticate(self.other_dispatcher)
        self.assert_business_read_totals(0)

        resource_share = ResourceSharePermission.objects.create(
            share_group=group,
            resource_type=ResourceType.DRONE,
            resource_object_id=resource.id,
            permissions=["view", "monitor"],
        )
        self.assertIsNotNone(resource_share.id)

        self.authenticate(self.other_dispatcher)
        self.assert_business_read_totals(1)

        self.authenticate(self.child_admin)
        delete_target_response = self.client.delete(
            f"/api/v2/resource/share-groups/{group.id}/departments/{self.other.id}"
        )
        self.assertEqual(delete_target_response.status_code, 200, getattr(delete_target_response, "data", delete_target_response.content))

        self.authenticate(self.other_dispatcher)
        self.assert_business_read_totals(0)

    def test_business_read_apis_should_support_v2_filters_and_reject_write_methods(self):
        self.bind_v2_drone(department=self.child, actor=self.child_admin, device_sn="BIZ-FILTER-A")
        self.bind_v2_drone(department=self.child, actor=self.child_admin, device_sn="BIZ-FILTER-B")
        pilot = self.create_legacy_pilot("filter_business_pilot")
        alpha = self.create_legacy_business_set(device_sn="BIZ-FILTER-A", name_prefix="alpha", pilot=pilot)
        self.create_legacy_business_set(device_sn="BIZ-FILTER-B", name_prefix="beta", pilot=pilot)

        self.authenticate(self.child_admin)
        routes_response = self.client.get("/api/v2/resource/routes", {"keywords": "alpha"})
        missions_response = self.client.get(
            "/api/v2/resource/missions",
            {"routeId": alpha["route"].id, "deviceSn": "BIZ-FILTER-A", "status": MissionStatus.PENDING},
        )
        records_response = self.client.get(
            "/api/v2/resource/flight-records",
            {"missionId": alpha["mission"].id, "deviceSn": "BIZ-FILTER-A", "flightNo": "FR-alpha"},
        )
        media_response = self.client.get(
            "/api/v2/resource/media-files",
            {
                "flightRecordId": alpha["flight_record"].id,
                "missionId": alpha["mission"].id,
                "deviceSn": "BIZ-FILTER-A",
                "mediaType": MediaType.PHOTO,
                "fileName": "alpha",
            },
        )

        self.assertEqual([item["id"] for item in routes_response.data["data"]["list"]], [alpha["route"].id])
        self.assertEqual([item["id"] for item in missions_response.data["data"]["list"]], [alpha["mission"].id])
        self.assertEqual([item["id"] for item in records_response.data["data"]["list"]], [alpha["flight_record"].id])
        self.assertEqual([item["id"] for item in media_response.data["data"]["list"]], [alpha["media_file"].id])

        write_response = self.client.post("/api/v2/resource/missions", {"name": "不支持"}, format="json")
        self.assertEqual(write_response.status_code, 405, getattr(write_response, "data", write_response.content))

    def test_department_admin_should_manage_own_connection_and_audit_plaintext_credential_view(self):
        self.authenticate(self.child_admin)

        create_response = self.client.post(
            "/api/v2/resource/dji-connections",
            {
                "name": "飞行队 DJI",
                "baseUrl": "https://dji.example.test/",
                "username": "adminPC1",
                "password": "secret",
                "loginFlag": 1,
            },
            format="json",
        )

        self.assertEqual(create_response.status_code, 201, getattr(create_response, "data", create_response.content))
        payload = create_response.data["data"]
        self.assertEqual(payload["baseUrl"], "https://dji.example.test")
        self.assertNotIn("password", payload)
        connection = DjiConnection.objects.get(owner_department=self.child, name="飞行队 DJI")
        self.assertEqual(connection.password, "secret")

        list_response = self.client.get("/api/v2/resource/dji-connections")
        self.assertEqual(list_response.status_code, 200, getattr(list_response, "data", list_response.content))
        self.assertNotIn("password", list_response.data["data"]["list"][0])

        default_detail_response = self.client.get(f"/api/v2/resource/dji-connections/{connection.id}")
        self.assertEqual(default_detail_response.status_code, 200, getattr(default_detail_response, "data", default_detail_response.content))
        self.assertNotIn("password", default_detail_response.data["data"])

        detail_response = self.client.get(f"/api/v2/resource/dji-connections/{connection.id}?includeCredentials=true")

        self.assertEqual(detail_response.status_code, 200, getattr(detail_response, "data", detail_response.content))
        self.assertEqual(detail_response.data["data"]["password"], "secret")
        self.assertTrue(
            V2AuditLog.objects.filter(
                action="view_plaintext_dji_credentials",
                actor_department=self.child,
                target_type="dji_connection",
                target_id=str(connection.id),
            ).exists()
        )

        V2AuditLog.objects.create(
            action="other_department_action",
            actor_user=self.other_admin,
            actor_department=self.other,
            target_type="manual",
        )
        audit_response = self.client.get("/api/v2/resource/audit-logs")
        self.assertEqual(audit_response.status_code, 200, getattr(audit_response, "data", audit_response.content))
        returned_actions = {item["action"] for item in audit_response.data["data"]["list"]}
        self.assertIn("view_plaintext_dji_credentials", returned_actions)
        self.assertNotIn("other_department_action", returned_actions)

        denied_create_response = self.client.post(
            "/api/v2/resource/dji-connections",
            {
                "ownerDepartmentId": self.other.id,
                "name": "越权 DJI",
                "baseUrl": "https://other.example.test",
                "username": "other",
                "password": "secret",
            },
            format="json",
        )
        self.assertEqual(
            denied_create_response.status_code,
            403,
            getattr(denied_create_response, "data", denied_create_response.content),
        )

        self.authenticate(self.other_admin)
        denied_response = self.client.put(
            f"/api/v2/resource/dji-connections/{connection.id}",
            {"name": "越权修改", "baseUrl": "https://other.example.test", "username": "x", "password": "y"},
            format="json",
        )

        self.assertEqual(denied_response.status_code, 403, getattr(denied_response, "data", denied_response.content))

    def test_platform_super_admin_should_maintain_all_department_connections(self):
        self.authenticate(self.platform_super)

        create_response = self.client.post(
            "/api/v2/resource/dji-connections",
            {
                "ownerDepartmentId": self.child.id,
                "name": "跨部门 DJI",
                "baseUrl": "https://dji.example.test/",
                "username": "adminPC1",
                "password": "secret",
            },
            format="json",
        )

        self.assertEqual(create_response.status_code, 201, getattr(create_response, "data", create_response.content))
        connection = DjiConnection.objects.get(pk=create_response.data["data"]["id"])
        self.assertEqual(connection.owner_department_id, self.child.id)

        update_response = self.client.put(
            f"/api/v2/resource/dji-connections/{connection.id}",
            {
                "ownerDepartmentId": self.other.id,
                "name": "转归属 DJI",
                "baseUrl": "https://other.example.test/",
                "username": "other",
                "password": "changed",
            },
            format="json",
        )

        self.assertEqual(update_response.status_code, 200, getattr(update_response, "data", update_response.content))
        connection.refresh_from_db()
        self.assertEqual(connection.owner_department_id, self.other.id)
        self.assertEqual(connection.password, "changed")
        self.assertEqual(update_response.data["data"]["ownerDepartmentId"], self.other.id)
        self.assertTrue(
            V2AuditLog.objects.filter(
                action="create_dji_connection",
                actor_department=self.root,
                resource_owner_department=self.child,
                target_id=str(connection.id),
            ).exists()
        )
        self.assertTrue(
            V2AuditLog.objects.filter(
                action="update_dji_connection",
                actor_department=self.root,
                resource_owner_department=self.other,
                target_id=str(connection.id),
            ).exists()
        )

    @patch("apps.resource_v2.views.DjiConnectionGateway", FakeDiscoveryGateway)
    def test_discover_bind_conflict_visibility_share_and_unbind(self):
        self.authenticate(self.child_admin)
        connection = DjiConnection.objects.create(
            owner_department=self.child,
            name="飞行队 DJI",
            base_url="https://dji.example.test",
            username="adminPC1",
            password="secret",
            created_by_user=self.child_admin,
        )

        discover_response = self.client.post(f"/api/v2/resource/dji-connections/{connection.id}/discover", {}, format="json")

        self.assertEqual(discover_response.status_code, 200, getattr(discover_response, "data", discover_response.content))
        self.assertEqual(FakeDiscoveryGateway.calls, [connection.id])
        drone = DroneResource.objects.get(device_sn="DRONE-SN-001")
        dock = DockResource.objects.get(device_sn="DOCK-SN-001")
        self.assertFalse(DjiDeviceIndex.objects.filter(device_sn="DRONE-SN-001").exists())
        self.assertEqual(discover_response.data["data"]["drones"][0]["id"], drone.id)
        self.assertEqual(discover_response.data["data"]["docks"][0]["id"], dock.id)
        self.assertEqual(discover_response.data["data"]["docks"][0]["deviceSn"], "DOCK-SN-001")

        bind_response = self.client.post(
            "/api/v2/resource/bindings",
            {"resourceType": ResourceType.DRONE, "resourceId": drone.id, "djiConnectionId": connection.id},
            format="json",
        )

        self.assertEqual(bind_response.status_code, 201, getattr(bind_response, "data", bind_response.content))
        binding = ResourceBinding.objects.get(resource_type=ResourceType.DRONE, resource_object_id=drone.id)
        self.assertEqual(binding.owner_department_id, self.child.id)
        self.assertTrue(
            ResourceBindingHistory.objects.filter(
                action_type=BindingActionType.BIND,
                resource_type=ResourceType.DRONE,
                resource_object_id=drone.id,
                new_department=self.child,
            ).exists()
        )

        dock_bind_response = self.client.post(
            "/api/v2/resource/bindings",
            {"resourceType": ResourceType.DOCK, "resourceId": dock.id, "djiConnectionId": connection.id},
            format="json",
        )
        self.assertEqual(dock_bind_response.status_code, 201, getattr(dock_bind_response, "data", dock_bind_response.content))
        docks_response = self.client.get("/api/v2/resource/docks")
        self.assertEqual(docks_response.status_code, 200, getattr(docks_response, "data", docks_response.content))
        self.assertEqual(docks_response.data["data"]["total"], 1)
        self.assertEqual(docks_response.data["data"]["list"][0]["deviceSn"], "DOCK-SN-001")

        self.authenticate(self.other_admin)
        conflict_connection = DjiConnection.objects.create(
            owner_department=self.other,
            name="保障队 DJI",
            base_url="https://other.example.test",
            username="other",
            password="secret",
            created_by_user=self.other_admin,
        )
        conflict_response = self.client.post(
            "/api/v2/resource/bindings",
            {"resourceType": ResourceType.DRONE, "resourceId": drone.id, "djiConnectionId": conflict_connection.id},
            format="json",
        )

        self.assertEqual(conflict_response.status_code, 409, getattr(conflict_response, "data", conflict_response.content))
        self.assertEqual(conflict_response.data["data"]["occupyingDepartment"]["id"], self.child.id)

        self.authenticate(self.root_admin)
        parent_visible_response = self.client.get("/api/v2/resource/drones")

        self.assertEqual(parent_visible_response.status_code, 200, getattr(parent_visible_response, "data", parent_visible_response.content))
        self.assertEqual(parent_visible_response.data["data"]["total"], 1)
        self.assertNotIn("bind", parent_visible_response.data["data"]["list"][0]["effectivePermissions"])
        self.assertNotIn("unbind", parent_visible_response.data["data"]["list"][0]["effectivePermissions"])

        root_drone = DroneResource.objects.create(device_sn="ROOT-DRONE-001", name="总部无人机", model="M30")
        root_connection = DjiConnection.objects.create(
            owner_department=self.root,
            name="总部 DJI",
            base_url="https://root.example.test",
            username="root",
            password="secret",
            created_by_user=self.root_admin,
        )
        ResourceBinding.objects.create(
            resource_type=ResourceType.DRONE,
            resource_object_id=root_drone.id,
            owner_department=self.root,
            dji_connection=root_connection,
            status=BindingStatus.ACTIVE,
            bound_by_user=self.root_admin,
        )

        self.authenticate(self.child_admin)
        child_visible_response = self.client.get("/api/v2/resource/drones")
        self.assertEqual(child_visible_response.status_code, 200, getattr(child_visible_response, "data", child_visible_response.content))
        child_visible_sns = {item["deviceSn"] for item in child_visible_response.data["data"]["list"]}
        self.assertIn("DRONE-SN-001", child_visible_sns)
        self.assertNotIn("ROOT-DRONE-001", child_visible_sns)

        group = ResourceShareGroup.objects.create(owner_department=self.child, name="保障共享")
        ResourceShareGroupTargetDepartment.objects.create(share_group=group, department=self.other)
        ResourceSharePermission.objects.create(
            share_group=group,
            resource_type=ResourceType.DRONE,
            resource_object_id=drone.id,
            permissions=["view", "monitor"],
        )

        self.authenticate(self.other_dispatcher)
        shared_response = self.client.get("/api/v2/resource/drones")

        self.assertEqual(shared_response.status_code, 200, getattr(shared_response, "data", shared_response.content))
        self.assertEqual(shared_response.data["data"]["total"], 1)
        self.assertEqual(shared_response.data["data"]["list"][0]["effectivePermissions"], ["view", "monitor"])

        self.authenticate(self.child_admin)
        unbind_response = self.client.delete(f"/api/v2/resource/bindings/{binding.id}")

        self.assertEqual(unbind_response.status_code, 200, getattr(unbind_response, "data", unbind_response.content))
        binding.refresh_from_db()
        self.assertEqual(binding.status, BindingStatus.UNBOUND)
        self.assertTrue(
            V2AuditLog.objects.filter(
                action="unbind_resource",
                actor_department=self.child,
                resource_owner_department=self.child,
                resource_type=ResourceType.DRONE,
                resource_object_id=str(drone.id),
            ).exists()
        )
        self.assertTrue(
            ResourceBindingHistory.objects.filter(
                action_type=BindingActionType.UNBIND,
                resource_type=ResourceType.DRONE,
                resource_object_id=drone.id,
                previous_department=self.child,
            ).exists()
        )

        rebind_response = self.client.post(
            "/api/v2/resource/bindings",
            {"resourceType": ResourceType.DRONE, "resourceId": drone.id, "djiConnectionId": connection.id},
            format="json",
        )
        self.assertEqual(rebind_response.status_code, 201, getattr(rebind_response, "data", rebind_response.content))
        rebound = ResourceBinding.objects.get(pk=rebind_response.data["data"]["id"])
        self.assertNotEqual(rebound.id, binding.id)
        self.assertEqual(rebound.status, BindingStatus.ACTIVE)

    def test_platform_super_admin_should_unbind_any_resource_and_query_global_audit_logs(self):
        connection = DjiConnection.objects.create(
            owner_department=self.child,
            name="飞行队 DJI",
            base_url="https://dji.example.test",
            username="adminPC1",
            password="secret",
            created_by_user=self.child_admin,
        )
        drone = DroneResource.objects.create(device_sn="SUPER-UNBIND-DRONE", name="超管解绑无人机", model="M30")
        binding = ResourceBinding.objects.create(
            resource_type=ResourceType.DRONE,
            resource_object_id=drone.id,
            owner_department=self.child,
            dji_connection=connection,
            status=BindingStatus.ACTIVE,
            bound_by_user=self.child_admin,
        )
        V2AuditLog.objects.create(
            action="other_department_action",
            actor_user=self.other_admin,
            actor_department=self.other,
            resource_owner_department=self.other,
            target_type="manual",
        )

        self.authenticate(self.other_admin)
        denied_unbind_response = self.client.delete(f"/api/v2/resource/bindings/{binding.id}")
        self.assertEqual(
            denied_unbind_response.status_code,
            403,
            getattr(denied_unbind_response, "data", denied_unbind_response.content),
        )
        binding.refresh_from_db()
        self.assertEqual(binding.status, BindingStatus.ACTIVE)

        self.authenticate(self.platform_super)
        unbind_response = self.client.delete(f"/api/v2/resource/bindings/{binding.id}")

        self.assertEqual(unbind_response.status_code, 200, getattr(unbind_response, "data", unbind_response.content))
        binding.refresh_from_db()
        self.assertEqual(binding.status, BindingStatus.UNBOUND)

        audit_response = self.client.get("/api/v2/resource/audit-logs")
        self.assertEqual(audit_response.status_code, 200, getattr(audit_response, "data", audit_response.content))
        returned_actions = {item["action"] for item in audit_response.data["data"]["list"]}
        self.assertIn("other_department_action", returned_actions)
        self.assertIn("unbind_resource", returned_actions)

        filtered_response = self.client.get(
            f"/api/v2/resource/audit-logs?resourceType={ResourceType.DRONE}&resourceObjectId={drone.id}"
        )
        self.assertEqual(filtered_response.status_code, 200, getattr(filtered_response, "data", filtered_response.content))
        self.assertEqual(filtered_response.data["data"]["total"], 1)
        self.assertEqual(filtered_response.data["data"]["list"][0]["action"], "unbind_resource")

    def test_dji_connection_gateway_should_store_session_on_v2_connection_and_discover_resources(self):
        connection = DjiConnection.objects.create(
            owner_department=self.child,
            name="飞行队 DJI",
            base_url="https://dji.example.test",
            username="adminPC1",
            password="secret",
            created_by_user=self.child_admin,
        )
        calls = []

        def fake_request(method, path, *, data, headers, follow_redirects):
            calls.append((method, path, dict(headers)))
            if path == "/api/v1/manage/login":
                return GatewayResponse(
                    status_code=200,
                    headers={},
                    data={
                        "workspace_id": "workspace-v2-001",
                        "access_token": "mock-access-token",
                        "user_id": "dji-user-001",
                        "username": "dji-admin",
                        "user_type": "1",
                        "mqtt_username": "mqtt-user",
                        "mqtt_password": "mqtt-pass",
                        "mqtt_addr": "tcp://mqtt.example.test:1883",
                    },
                )
            if "domain=0" in path:
                return GatewayResponse(
                    status_code=200,
                    headers={},
                    data={
                        "list": [{"device_sn": "GATEWAY-DRONE-001", "device_name": "网关无人机"}],
                        "pagination": {"page": 1, "page_size": 100, "total": 1},
                    },
                )
            if "domain=3" in path:
                return GatewayResponse(
                    status_code=200,
                    headers={},
                    data={
                        "list": [{"device_sn": "GATEWAY-DOCK-001", "device_name": "网关机场"}],
                        "pagination": {"page": 1, "page_size": 100, "total": 1},
                    },
                )
            raise AssertionError(f"unexpected upstream request: {method} {path}")

        with patch.object(DjiConnectionGateway, "_request", side_effect=fake_request), patch.object(
            DjiConnectionGateway,
            "_validate_workspace",
            return_value=True,
        ):
            discovered = DjiConnectionGateway(connection).discover()

        connection.refresh_from_db()
        self.assertEqual(connection.workspace_id, "workspace-v2-001")
        self.assertEqual(connection.access_token, "mock-access-token")
        self.assertEqual(connection.mqtt_username, "mqtt-user")
        self.assertEqual(connection.status, "ACTIVE")
        self.assertEqual(discovered["drones"][0]["device_sn"], "GATEWAY-DRONE-001")
        self.assertEqual(discovered["docks"][0]["device_sn"], "GATEWAY-DOCK-001")
        self.assertFalse(DjiDeviceIndex.objects.filter(device_sn__in=["GATEWAY-DRONE-001", "GATEWAY-DOCK-001"]).exists())
        self.assertTrue(any(path == "/api/v1/manage/login" for _method, path, _headers in calls))
        self.assertTrue(any("domain=0" in path for _method, path, _headers in calls))
        self.assertTrue(any("domain=3" in path for _method, path, _headers in calls))

    def test_share_group_api_should_manage_targets_resources_visibility_and_audit(self):
        connection = DjiConnection.objects.create(
            owner_department=self.child,
            name="飞行队 DJI",
            base_url="https://dji.example.test",
            username="adminPC1",
            password="secret",
            created_by_user=self.child_admin,
        )
        drone = DroneResource.objects.create(device_sn="SHARED-DRONE-001", name="共享无人机", model="M30")
        binding = ResourceBinding.objects.create(
            resource_type=ResourceType.DRONE,
            resource_object_id=drone.id,
            owner_department=self.child,
            dji_connection=connection,
            status=BindingStatus.ACTIVE,
            bound_by_user=self.child_admin,
        )
        self.authenticate(self.child_admin)

        create_response = self.client.post(
            "/api/v2/resource/share-groups",
            {"name": "保障共享"},
            format="json",
        )

        self.assertEqual(create_response.status_code, 201, getattr(create_response, "data", create_response.content))
        group_id = create_response.data["data"]["id"]
        self.assertEqual(create_response.data["data"]["ownerDepartmentId"], self.child.id)
        self.assertTrue(
            V2AuditLog.objects.filter(
                action="create_share_group",
                actor_department=self.child,
                resource_owner_department=self.child,
                target_type="resource_share_group",
                target_id=str(group_id),
            ).exists()
        )

        update_response = self.client.put(
            f"/api/v2/resource/share-groups/{group_id}",
            {"name": "保障共享组", "status": DirectoryStatus.ACTIVE},
            format="json",
        )
        self.assertEqual(update_response.status_code, 200, getattr(update_response, "data", update_response.content))
        self.assertEqual(update_response.data["data"]["name"], "保障共享组")

        target_response = self.client.post(
            f"/api/v2/resource/share-groups/{group_id}/departments",
            {"departmentId": self.other.id},
            format="json",
        )
        self.assertEqual(target_response.status_code, 201, getattr(target_response, "data", target_response.content))
        self.assertEqual(target_response.data["data"]["departmentId"], self.other.id)

        duplicate_target_response = self.client.post(
            f"/api/v2/resource/share-groups/{group_id}/departments",
            {"departmentId": self.other.id},
            format="json",
        )
        self.assertEqual(
            duplicate_target_response.status_code,
            409,
            getattr(duplicate_target_response, "data", duplicate_target_response.content),
        )

        share_response = self.client.post(
            f"/api/v2/resource/share-groups/{group_id}/resources",
            {"resourceType": ResourceType.DRONE, "resourceId": drone.id, "permissions": ["monitor", "view", "view"]},
            format="json",
        )
        self.assertEqual(share_response.status_code, 201, getattr(share_response, "data", share_response.content))
        resource_share_id = share_response.data["data"]["id"]
        self.assertEqual(share_response.data["data"]["resourceType"], ResourceType.DRONE)
        self.assertEqual(share_response.data["data"]["resourceId"], drone.id)
        self.assertEqual(share_response.data["data"]["permissions"], ["view", "monitor"])

        self.authenticate(self.other_dispatcher)
        visible_response = self.client.get("/api/v2/resource/drones")
        self.assertEqual(visible_response.status_code, 200, getattr(visible_response, "data", visible_response.content))
        self.assertEqual(visible_response.data["data"]["total"], 1)
        self.assertEqual(visible_response.data["data"]["list"][0]["deviceSn"], "SHARED-DRONE-001")
        self.assertEqual(visible_response.data["data"]["list"][0]["effectivePermissions"], ["view", "monitor"])

        self.authenticate(self.child_admin)
        replace_permissions_response = self.client.put(
            f"/api/v2/resource/share-groups/{group_id}/resources/{resource_share_id}",
            {"permissions": ["dispatch_task", "view", "monitor"]},
            format="json",
        )
        self.assertEqual(
            replace_permissions_response.status_code,
            200,
            getattr(replace_permissions_response, "data", replace_permissions_response.content),
        )
        self.assertEqual(replace_permissions_response.data["data"]["permissions"], ["view", "monitor", "dispatch_task"])

        self.authenticate(self.other_dispatcher)
        narrowed_response = self.client.get("/api/v2/resource/drones")
        self.assertEqual(narrowed_response.status_code, 200, getattr(narrowed_response, "data", narrowed_response.content))
        self.assertEqual(
            narrowed_response.data["data"]["list"][0]["effectivePermissions"],
            ["view", "monitor", "dispatch_task"],
        )

        self.authenticate(self.child_admin)
        delete_share_response = self.client.delete(
            f"/api/v2/resource/share-groups/{group_id}/resources/{resource_share_id}"
        )
        self.assertEqual(delete_share_response.status_code, 200, getattr(delete_share_response, "data", delete_share_response.content))
        self.assertEqual(delete_share_response.data["data"]["id"], resource_share_id)

        self.authenticate(self.other_dispatcher)
        no_visible_response = self.client.get("/api/v2/resource/drones")
        self.assertEqual(no_visible_response.status_code, 200, getattr(no_visible_response, "data", no_visible_response.content))
        self.assertEqual(no_visible_response.data["data"]["total"], 0)

        self.authenticate(self.child_admin)
        delete_target_response = self.client.delete(
            f"/api/v2/resource/share-groups/{group_id}/departments/{self.other.id}"
        )
        self.assertEqual(delete_target_response.status_code, 200, getattr(delete_target_response, "data", delete_target_response.content))
        self.assertEqual(delete_target_response.data["data"]["departmentId"], self.other.id)

        expected_actions = {
            "create_share_group",
            "update_share_group",
            "add_share_group_target_department",
            "remove_share_group_target_department",
            "share_resource_to_group",
            "update_resource_share_permissions",
            "remove_resource_sharing",
        }
        logged_actions = set(
            V2AuditLog.objects.filter(actor_department=self.child, resource_owner_department=self.child).values_list(
                "action",
                flat=True,
            )
        )
        self.assertTrue(expected_actions.issubset(logged_actions))
        self.assertFalse(ResourceSharePermission.objects.filter(pk=resource_share_id).exists())
        self.assertTrue(ResourceBinding.objects.filter(pk=binding.id, status=BindingStatus.ACTIVE).exists())

    def test_share_group_api_should_enforce_admin_ownership_and_validation(self):
        self.authenticate(self.child_admin)
        group = ResourceShareGroup.objects.create(owner_department=self.child, name="飞行队共享")
        child_connection = DjiConnection.objects.create(
            owner_department=self.child,
            name="飞行队 DJI",
            base_url="https://dji.example.test",
            username="adminPC1",
            password="secret",
            created_by_user=self.child_admin,
        )
        child_drone = DroneResource.objects.create(device_sn="CHILD-SHARE-DRONE", name="飞行队无人机", model="M30")
        ResourceBinding.objects.create(
            resource_type=ResourceType.DRONE,
            resource_object_id=child_drone.id,
            owner_department=self.child,
            dji_connection=child_connection,
            status=BindingStatus.ACTIVE,
            bound_by_user=self.child_admin,
        )
        other_connection = DjiConnection.objects.create(
            owner_department=self.other,
            name="保障队 DJI",
            base_url="https://other.example.test",
            username="other",
            password="secret",
            created_by_user=self.other_admin,
        )
        other_drone = DroneResource.objects.create(device_sn="OTHER-SHARE-DRONE", name="保障队无人机", model="M30")
        ResourceBinding.objects.create(
            resource_type=ResourceType.DRONE,
            resource_object_id=other_drone.id,
            owner_department=self.other,
            dji_connection=other_connection,
            status=BindingStatus.ACTIVE,
            bound_by_user=self.other_admin,
        )
        unbound_drone = DroneResource.objects.create(device_sn="UNBOUND-SHARE-DRONE", name="未绑定无人机", model="M30")
        disabled_department = Department.objects.create(
            tenant=self.tenant,
            name="停用部门",
            parent=self.root,
            status=DirectoryStatus.DISABLED,
        )
        other_tenant = Tenant.objects.create(code="other_v2_tenant", name="其他租户", status=TenantStatus.ACTIVE)
        cross_tenant_department = Department.objects.create(tenant=other_tenant, name="其他租户总部")

        self.authenticate(self.other_admin)
        denied_update_response = self.client.put(
            f"/api/v2/resource/share-groups/{group.id}",
            {"name": "越权修改", "status": DirectoryStatus.ACTIVE},
            format="json",
        )
        self.assertEqual(denied_update_response.status_code, 403, getattr(denied_update_response, "data", denied_update_response.content))

        self.authenticate(self.other_dispatcher)
        denied_create_response = self.client.post("/api/v2/resource/share-groups", {"name": "非法共享"}, format="json")
        self.assertEqual(denied_create_response.status_code, 403, getattr(denied_create_response, "data", denied_create_response.content))

        self.authenticate(self.platform_super)
        platform_list_response = self.client.get("/api/v2/resource/share-groups")
        self.assertEqual(platform_list_response.status_code, 200, getattr(platform_list_response, "data", platform_list_response.content))
        self.assertEqual(platform_list_response.data["data"]["total"], 1)
        platform_create_response = self.client.post("/api/v2/resource/share-groups", {"name": "超管代建"}, format="json")
        self.assertEqual(platform_create_response.status_code, 403, getattr(platform_create_response, "data", platform_create_response.content))

        self.authenticate(self.child_admin)
        disabled_target_response = self.client.post(
            f"/api/v2/resource/share-groups/{group.id}/departments",
            {"departmentId": disabled_department.id},
            format="json",
        )
        self.assertEqual(disabled_target_response.status_code, 400, getattr(disabled_target_response, "data", disabled_target_response.content))

        cross_tenant_target_response = self.client.post(
            f"/api/v2/resource/share-groups/{group.id}/departments",
            {"departmentId": cross_tenant_department.id},
            format="json",
        )
        self.assertEqual(
            cross_tenant_target_response.status_code,
            400,
            getattr(cross_tenant_target_response, "data", cross_tenant_target_response.content),
        )

        for permissions in ([], ["unbind"], ["unknown"]):
            invalid_permissions_response = self.client.post(
                f"/api/v2/resource/share-groups/{group.id}/resources",
                {"resourceType": ResourceType.DRONE, "resourceId": child_drone.id, "permissions": permissions},
                format="json",
            )
            self.assertEqual(
                invalid_permissions_response.status_code,
                400,
                getattr(invalid_permissions_response, "data", invalid_permissions_response.content),
            )

        other_resource_response = self.client.post(
            f"/api/v2/resource/share-groups/{group.id}/resources",
            {"resourceType": ResourceType.DRONE, "resourceId": other_drone.id, "permissions": ["view"]},
            format="json",
        )
        self.assertEqual(other_resource_response.status_code, 403, getattr(other_resource_response, "data", other_resource_response.content))

        unbound_resource_response = self.client.post(
            f"/api/v2/resource/share-groups/{group.id}/resources",
            {"resourceType": ResourceType.DRONE, "resourceId": unbound_drone.id, "permissions": ["view"]},
            format="json",
        )
        self.assertEqual(
            unbound_resource_response.status_code,
            400,
            getattr(unbound_resource_response, "data", unbound_resource_response.content),
        )

        share_response = self.client.post(
            f"/api/v2/resource/share-groups/{group.id}/resources",
            {"resourceType": ResourceType.DRONE, "resourceId": child_drone.id, "permissions": ["view"]},
            format="json",
        )
        self.assertEqual(share_response.status_code, 201, getattr(share_response, "data", share_response.content))
        duplicate_share_response = self.client.post(
            f"/api/v2/resource/share-groups/{group.id}/resources",
            {"resourceType": ResourceType.DRONE, "resourceId": child_drone.id, "permissions": ["view"]},
            format="json",
        )
        self.assertEqual(duplicate_share_response.status_code, 409, getattr(duplicate_share_response, "data", duplicate_share_response.content))
