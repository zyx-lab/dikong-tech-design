from django.contrib.auth import get_user_model
from django.db import IntegrityError
from django.test import TestCase
from rest_framework.test import APIClient
from unittest.mock import patch

from apps.access.models import EmploymentStatus, ScopeType, Tenant, TenantStatus
from apps.access.test_support import (
    ensure_staff_profile,
    ensure_tenant_member_position,
    ensure_tenant_role_binding,
    grant_role_permissions,
)
from apps.dji_bff.gateway import DjiGatewayUpstreamError
from apps.dji_bff.models import DjiDeviceIndex
from apps.dji_mock.state import mock_dji_state
from apps.dji_mock.test_support import MockDjiUpstreamTestMixin
from apps.drone.models import Drone, DroneStatus
from apps.drone.serializers import DroneClaimSerializer
from apps.drone_assignment.models import DroneAssignment, DroneAssignmentStatus
from apps.flight_record.models import FlightRecord
from apps.mission.models import Mission, MissionStatus
from apps.route.models import Route

User = get_user_model()


class DroneApiTests(MockDjiUpstreamTestMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.client = APIClient()
        self.user = User.objects.create_user(username="drone_admin", password="pass1234", status=1)
        ensure_staff_profile(self.user, name="无人机管理员", employment_status=EmploymentStatus.ACTIVE)
        self.tenant, self.member, self.role = ensure_tenant_role_binding(
            self.user,
            tenant_code="drone_test_tenant",
            role_code="drone_test_role",
            role_name="无人机测试角色",
        )
        grant_role_permissions(
            self.role,
            {
                "drone.view_drone": ScopeType.ALL,
                "drone.manage_drone": ScopeType.ALL,
            },
        )
        self.client.force_authenticate(self.user)
        self.client.credentials(HTTP_X_TENANT_CODE=self.tenant.code)

    def test_available_should_only_return_unclaimed_devices(self):
        DjiDeviceIndex.objects.create(device_sn="SN-001", last_payload={"name": "共享设备1"})
        DjiDeviceIndex.objects.create(device_sn="SN-002", last_payload={"name": "共享设备2"})
        Drone.objects.create(
            tenant=self.tenant,
            code="DJ-001",
            name="已认领设备",
            model="M30",
            device_sn="SN-001",
            created_by_tenant_member_id=self.member.id,
        )

        response = self.client.get("/api/v1/drones/available")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["data"]["total"], 1)
        self.assertEqual(response.data["data"]["list"][0]["device_sn"], "SN-002")

    def test_claim_should_fill_name_and_model_from_device_index(self):
        DjiDeviceIndex.objects.create(
            device_sn="SN-CLAIM-001",
            last_payload={"name": "共享巡检机", "model": "Matrice 30"},
        )

        response = self.client.post(
            "/api/v1/drones",
            {"code": "DJ-CLAIM-001", "device_sn": "SN-CLAIM-001"},
            format="json",
        )

        self.assertEqual(response.status_code, 201)
        drone = Drone.objects.get(device_sn="SN-CLAIM-001")
        self.assertEqual(drone.name, "共享巡检机")
        self.assertEqual(drone.model, "Matrice 30")
        self.assertEqual(drone.created_by_tenant_member_id, self.member.id)

    def test_put_should_update_partial_local_fields(self):
        drone = Drone.objects.create(
            tenant=self.tenant,
            code="DJ-UPD-001",
            name="原名称",
            model="M30",
            device_sn="SN-UPD-001",
            org_id=10,
            created_by_tenant_member_id=self.member.id,
        )

        response = self.client.put(
            f"/api/v1/drones/{drone.id}",
            {"name": "更新后名称"},
            format="json",
        )

        self.assertEqual(response.status_code, 200, response.data)
        drone.refresh_from_db()
        self.assertEqual(drone.name, "更新后名称")
        self.assertEqual(drone.code, "DJ-UPD-001")
        self.assertEqual(drone.model, "M30")
        self.assertEqual(drone.org_id, 10)

    def test_patch_should_return_method_not_allowed(self):
        drone = Drone.objects.create(
            tenant=self.tenant,
            code="DJ-PATCH-001",
            name="禁止PATCH设备",
            model="M30",
            device_sn="SN-PATCH-001",
            created_by_tenant_member_id=self.member.id,
        )

        response = self.client.patch(
            f"/api/v1/drones/{drone.id}",
            {"name": "不应成功"},
            format="json",
        )

        self.assertEqual(response.status_code, 405, response.data)

    def test_claim_should_reject_device_claimed_by_other_tenant(self):
        other_tenant = Tenant.objects.create(code="drone_other_tenant", name="其他租户", status=TenantStatus.ACTIVE)
        DjiDeviceIndex.objects.create(device_sn="SN-CONFLICT-001", last_payload={})
        Drone.objects.create(
            tenant=other_tenant,
            code="DJ-OTHER-001",
            name="其他租户已认领设备",
            model="M30",
            device_sn="SN-CONFLICT-001",
        )

        response = self.client.post(
            "/api/v1/drones",
            {"code": "DJ-CLAIM-002", "device_sn": "SN-CONFLICT-001"},
            format="json",
        )

        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.data["code"], "C0101")
        self.assertIn("device_sn", response.data["data"])

    def test_device_sn_should_be_globally_unique_across_tenants(self):
        other_tenant = Tenant.objects.create(code="drone_global_unique_tenant", name="全局唯一租户", status=TenantStatus.ACTIVE)
        Drone.objects.create(
            tenant=self.tenant,
            code="DJ-GLOBAL-001",
            name="先认领设备",
            model="M30",
            device_sn="SN-GLOBAL-001",
            created_by_tenant_member_id=self.member.id,
        )

        with self.assertRaises(IntegrityError):
            Drone.objects.create(
                tenant=other_tenant,
                code="DJ-GLOBAL-002",
                name="重复认领设备",
                model="M30",
                device_sn="SN-GLOBAL-001",
            )

    def test_claim_should_return_duplicate_when_db_unique_conflict_happens(self):
        DjiDeviceIndex.objects.create(device_sn="SN-RACE-001", last_payload={"name": "竞态设备"})

        with patch("apps.drone.views.DroneViewSet.perform_create", side_effect=IntegrityError("UNIQUE constraint failed: drones.device_sn")):
            response = self.client.post(
                "/api/v1/drones",
                {"code": "DJ-RACE-001", "device_sn": "SN-RACE-001"},
                format="json",
            )

        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.data["code"], "C0101")
        self.assertIn("device_sn", response.data["data"])

    def test_claim_serializer_should_raise_integrity_error_when_released_row_is_claimed_after_validate(self):
        DjiDeviceIndex.objects.create(device_sn="SN-RACE-REL-001", last_payload={"name": "竞态回收设备"})
        released_drone = Drone.objects.create(
            tenant=self.tenant,
            code="DJ-RACE-REL-OLD",
            name="旧释放设备",
            model="M30",
            device_sn="SN-RACE-REL-001",
            status=DroneStatus.RELEASED,
        )

        class _Req:
            user = self.user
            tenant_context = self.tenant

        serializer = DroneClaimSerializer(
            data={"code": "DJ-RACE-REL-NEW", "device_sn": "SN-RACE-REL-001"},
            context={"request": _Req()},
        )
        self.assertTrue(serializer.is_valid(), serializer.errors)

        released_drone.status = DroneStatus.CLAIMED
        released_drone.save(update_fields=["status", "updated_at"])

        with self.assertRaises(IntegrityError):
            serializer.save(tenant=self.tenant, created_by_tenant_member_id=self.member.id)

    def test_delete_should_release_drone_and_allow_reclaim_same_device_sn(self):
        DjiDeviceIndex.objects.create(device_sn="SN-RELEASE-001", last_payload={"name": "可释放设备"})
        drone = Drone.objects.create(
            tenant=self.tenant,
            code="DJ-RELEASE-001",
            name="可释放设备",
            model="M30",
            device_sn="SN-RELEASE-001",
            created_by_tenant_member_id=self.member.id,
        )

        delete_response = self.client.delete(f"/api/v1/drones/{drone.id}")

        self.assertEqual(delete_response.status_code, 200)
        drone.refresh_from_db()
        self.assertEqual(drone.status, DroneStatus.RELEASED)

        available_response = self.client.get("/api/v1/drones/available")
        self.assertEqual(available_response.status_code, 200)
        self.assertEqual(available_response.data["data"]["total"], 1)
        self.assertEqual(available_response.data["data"]["list"][0]["device_sn"], "SN-RELEASE-001")

        reclaim_response = self.client.post(
            "/api/v1/drones",
            {"code": "DJ-RELEASE-002", "device_sn": "SN-RELEASE-001"},
            format="json",
        )
        self.assertEqual(reclaim_response.status_code, 201)
        self.assertEqual(reclaim_response.data["data"]["id"], drone.id)
        drone.refresh_from_db()
        self.assertEqual(drone.status, DroneStatus.CLAIMED)
        self.assertEqual(drone.code, "DJ-RELEASE-002")
        self.assertEqual(Drone.objects.filter(device_sn="SN-RELEASE-001").count(), 1)

    def test_delete_should_keep_history_reference_when_flight_record_exists(self):
        drone = Drone.objects.create(
            tenant=self.tenant,
            code="DJ-HISTORY-001",
            name="历史设备",
            model="M30",
            device_sn="SN-HISTORY-001",
            created_by_tenant_member_id=self.member.id,
        )
        flight_record = FlightRecord.objects.create(
            tenant=self.tenant,
            flight_no="FR-HISTORY-001",
            drone=drone,
        )

        delete_response = self.client.delete(f"/api/v1/drones/{drone.id}")

        self.assertEqual(delete_response.status_code, 200)
        drone.refresh_from_db()
        flight_record.refresh_from_db()
        self.assertEqual(drone.status, DroneStatus.RELEASED)
        self.assertEqual(flight_record.drone_id, drone.id)

    def test_delete_and_reclaim_should_not_change_mission_or_flight_record_history_relations(self):
        DjiDeviceIndex.objects.create(device_sn="SN-HISTORY-REL-001", last_payload={"name": "历史关联设备"})
        ensure_tenant_member_position(self.member, code="pilot_operator", name="飞手")
        route = Route.objects.create(tenant=self.tenant, name="历史关联航线")
        drone = Drone.objects.create(
            tenant=self.tenant,
            code="DJ-HISTORY-REL-001",
            name="历史关联设备",
            model="M30",
            device_sn="SN-HISTORY-REL-001",
            created_by_tenant_member_id=self.member.id,
        )
        mission = Mission.objects.create(
            tenant=self.tenant,
            name="历史关联任务",
            route=route,
            route_name=route.name,
            drone=drone,
            drone_name=drone.name,
            pilot=self.member,
            pilot_name=self.member.display_name,
            status=MissionStatus.DRONE_BOUND,
        )
        flight_record = FlightRecord.objects.create(
            tenant=self.tenant,
            flight_no="FR-HISTORY-REL-001",
            mission=mission,
            mission_name=mission.name,
            drone=drone,
            drone_name=drone.name,
            pilot=self.member,
            pilot_name=self.member.display_name,
        )

        delete_response = self.client.delete(f"/api/v1/drones/{drone.id}")
        self.assertEqual(delete_response.status_code, 200)

        reclaim_response = self.client.post(
            "/api/v1/drones",
            {"code": "DJ-HISTORY-REL-002", "device_sn": "SN-HISTORY-REL-001"},
            format="json",
        )
        self.assertEqual(reclaim_response.status_code, 201)
        self.assertEqual(reclaim_response.data["data"]["id"], drone.id)

        mission.refresh_from_db()
        flight_record.refresh_from_db()
        drone.refresh_from_db()
        self.assertEqual(drone.status, DroneStatus.CLAIMED)
        self.assertEqual(mission.drone_id, drone.id)
        self.assertEqual(flight_record.drone_id, drone.id)

    def test_cross_tenant_reclaim_should_not_reuse_other_tenant_released_drone_row(self):
        DjiDeviceIndex.objects.create(device_sn="SN-CROSS-RECLAIM-001", last_payload={"name": "跨租户回收设备"})
        ensure_tenant_member_position(self.member, code="pilot_operator", name="飞手")
        route = Route.objects.create(tenant=self.tenant, name="跨租户历史航线")
        old_drone = Drone.objects.create(
            tenant=self.tenant,
            code="DJ-CROSS-A-001",
            name="租户A设备",
            model="M30",
            device_sn="SN-CROSS-RECLAIM-001",
            created_by_tenant_member_id=self.member.id,
        )
        mission = Mission.objects.create(
            tenant=self.tenant,
            name="跨租户历史任务",
            route=route,
            route_name=route.name,
            drone=old_drone,
            drone_name=old_drone.name,
            pilot=self.member,
            pilot_name=self.member.display_name,
            status=MissionStatus.DRONE_BOUND,
        )
        flight_record = FlightRecord.objects.create(
            tenant=self.tenant,
            flight_no="FR-CROSS-RECLAIM-001",
            mission=mission,
            mission_name=mission.name,
            drone=old_drone,
            drone_name=old_drone.name,
            pilot=self.member,
            pilot_name=self.member.display_name,
        )

        delete_response = self.client.delete(f"/api/v1/drones/{old_drone.id}")
        self.assertEqual(delete_response.status_code, 200)
        old_drone.refresh_from_db()
        self.assertEqual(old_drone.status, DroneStatus.RELEASED)

        other_user = User.objects.create_user(username="drone_other_admin", password="pass1234", status=1)
        ensure_staff_profile(other_user, name="其他租户管理员", employment_status=EmploymentStatus.ACTIVE)
        other_tenant, _other_member, other_role = ensure_tenant_role_binding(
            other_user,
            tenant_code="drone_other_claim_tenant",
            role_code="drone_other_claim_role",
            role_name="无人机跨租户认领角色",
        )
        grant_role_permissions(
            other_role,
            {
                "drone.view_drone": ScopeType.ALL,
                "drone.manage_drone": ScopeType.ALL,
            },
        )
        other_client = APIClient()
        other_client.force_authenticate(other_user)
        other_client.credentials(HTTP_X_TENANT_CODE=other_tenant.code)

        reclaim_response = other_client.post(
            "/api/v1/drones",
            {"code": "DJ-CROSS-B-001", "device_sn": "SN-CROSS-RECLAIM-001"},
            format="json",
        )
        self.assertEqual(reclaim_response.status_code, 201)
        self.assertNotEqual(reclaim_response.data["data"]["id"], old_drone.id)

        old_drone.refresh_from_db()
        new_drone = Drone.objects.get(id=reclaim_response.data["data"]["id"])
        mission.refresh_from_db()
        flight_record.refresh_from_db()

        self.assertEqual(old_drone.tenant_id, self.tenant.id)
        self.assertEqual(old_drone.status, DroneStatus.RELEASED)
        self.assertEqual(new_drone.tenant_id, other_tenant.id)
        self.assertEqual(new_drone.status, DroneStatus.CLAIMED)
        self.assertEqual(mission.drone_id, old_drone.id)
        self.assertEqual(flight_record.drone_id, old_drone.id)

    def test_live_start_should_proxy_exact_upstream_payload(self):
        mock_dji_state.seed_device(device_sn="SN-LIVE-001", name="直播设备", model="M30")
        DjiDeviceIndex.objects.create(device_sn="SN-LIVE-001", last_payload={})
        drone = Drone.objects.create(
            tenant=self.tenant,
            code="DJ-LIVE-001",
            name="直播设备",
            model="M30",
            device_sn="SN-LIVE-001",
        )

        response = self.client.post(
            f"/api/v1/drones/{drone.id}/live/start",
            {"video_id": "SN-LIVE-001/88-0-0/normal-0", "url_type": 1, "video_quality": 0},
            format="json",
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn("url", response.data["data"])
        self.assertIn("rtmp_url", response.data["data"])
        self.assertIn("whep_url", response.data["data"])
        self.assertIn("SN-LIVE-001-88-0-0", response.data["data"]["url"])

    def test_live_start_should_forward_only_client_supplied_upstream_fields(self):
        drone = Drone.objects.create(
            tenant=self.tenant,
            code="DJ-LIVE-DEFAULT-001",
            name="直播透传设备",
            model="M30",
            device_sn="SN-LIVE-DEFAULT-001",
            created_by_tenant_member_id=self.member.id,
        )

        with patch(
            "apps.drone.views.DjiGateway.start_live",
            return_value={"url": "rtmp://example/live", "rtmp_url": "rtmp://example/live"},
        ) as start_mock:
            response = self.client.post(
                f"/api/v1/drones/{drone.id}/live/start",
                {"video_id": "SN-LIVE-DEFAULT-001/88-0-0/normal-0", "video_quality": 3},
                format="json",
            )

        self.assertEqual(response.status_code, 200, response.data)
        start_mock.assert_called_once_with(
            "SN-LIVE-DEFAULT-001",
            video_id="SN-LIVE-DEFAULT-001/88-0-0/normal-0",
            video_quality=3,
        )

    def test_live_start_should_reject_legacy_mapped_fields(self):
        drone = Drone.objects.create(
            tenant=self.tenant,
            code="DJ-LIVE-LEGACY-001",
            name="直播旧字段设备",
            model="M30",
            device_sn="SN-LIVE-LEGACY-001",
            created_by_tenant_member_id=self.member.id,
        )

        response = self.client.post(
            f"/api/v1/drones/{drone.id}/live/start",
            {"camera_index": "88-0-0", "video_index": "normal-0"},
            format="json",
        )

        self.assertEqual(response.status_code, 400, response.data)
        self.assertEqual(response.data["code"], "B0001")
        self.assertEqual(response.data["data"]["camera_index"], ["该字段在此接口不可写"])
        self.assertEqual(response.data["data"]["video_index"], ["该字段在此接口不可写"])

    def test_live_start_should_transparently_return_upstream_invalid_params(self):
        drone = Drone.objects.create(
            tenant=self.tenant,
            code="DJ-LIVE-UPSTREAM-400",
            name="直播上游校验设备",
            model="M30",
            device_sn="SN-LIVE-UPSTREAM-400",
            created_by_tenant_member_id=self.member.id,
        )

        with patch(
            "apps.drone.views.DjiGateway.start_live",
            side_effect=DjiGatewayUpstreamError(
                "DJI upstream business error",
                status_code=200,
                data={
                    "code": "E0001",
                    "msg": "Error Code: 210002, Error Msg: Invalid parameter.. videoQualitymust not be null, Current value is: null",
                },
            ),
        ):
            response = self.client.post(
                f"/api/v1/drones/{drone.id}/live/start",
                {"video_id": "SN-LIVE-UPSTREAM-400/88-0-0/normal-0"},
                format="json",
            )

        self.assertEqual(response.status_code, 400, response.data)
        self.assertEqual(response.data["code"], "E0001")
        self.assertIn("Invalid parameter", response.data["msg"])
        self.assertIsNone(response.data["data"])

    def test_live_start_should_transparently_return_upstream_service_failure(self):
        drone = Drone.objects.create(
            tenant=self.tenant,
            code="DJ-LIVE-UPSTREAM-502",
            name="直播上游服务设备",
            model="M30",
            device_sn="SN-LIVE-UPSTREAM-502",
            created_by_tenant_member_id=self.member.id,
        )

        with patch(
            "apps.drone.views.DjiGateway.start_live",
            side_effect=DjiGatewayUpstreamError(
                "DJI upstream business error",
                status_code=200,
                data={"code": "D0001", "msg": "Please check whether the live stream service is normal."},
            ),
        ):
            response = self.client.post(
                f"/api/v1/drones/{drone.id}/live/start",
                {"video_id": "SN-LIVE-UPSTREAM-502/88-0-0/normal-0", "url_type": 1, "video_quality": 0},
                format="json",
            )

        self.assertEqual(response.status_code, 502, response.data)
        self.assertEqual(response.data["code"], "D0001")
        self.assertEqual(response.data["msg"], "Please check whether the live stream service is normal.")
        self.assertIsNone(response.data["data"])

    def test_live_capacity_should_return_404_when_upstream_capacity_missing(self):
        drone = Drone.objects.create(
            tenant=self.tenant,
            code="DJ-LIVE-404",
            name="无直播能力设备",
            model="M30",
            device_sn="SN-LIVE-404",
            created_by_tenant_member_id=self.member.id,
        )

        response = self.client.get(f"/api/v1/drones/{drone.id}/live/capacity")

        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.data["code"], "C0404")
        self.assertIn("device_sn", response.data["data"])

    def test_live_stop_should_proxy_exact_upstream_payload(self):
        drone = Drone.objects.create(
            tenant=self.tenant,
            code="DJ-LIVE-STOP-001",
            name="直播停止设备",
            model="M30",
            device_sn="SN-LIVE-STOP-001",
            created_by_tenant_member_id=self.member.id,
        )

        with patch(
            "apps.drone.views.DjiGateway.stop_live",
            return_value={"stopped": True},
        ) as stop_mock:
            response = self.client.post(
                f"/api/v1/drones/{drone.id}/live/stop",
                {"video_id": "SN-LIVE-STOP-001/88-0-0/normal-0"},
                format="json",
            )

        self.assertEqual(response.status_code, 200)
        stop_mock.assert_called_once_with(
            "SN-LIVE-STOP-001",
            video_id="SN-LIVE-STOP-001/88-0-0/normal-0",
        )

    def test_live_update_should_proxy_exact_upstream_payload(self):
        drone = Drone.objects.create(
            tenant=self.tenant,
            code="DJ-LIVE-QUALITY-001",
            name="直播画质设备",
            model="M30",
            device_sn="SN-LIVE-QUALITY-001",
            created_by_tenant_member_id=self.member.id,
        )

        with patch(
            "apps.drone.views.DjiGateway.update_live",
            return_value={"updated": True},
        ) as quality_mock:
            response = self.client.post(
                f"/api/v1/drones/{drone.id}/live/update",
                {"video_id": "SN-LIVE-QUALITY-001/88-0-0/normal-0", "video_quality": 3},
                format="json",
            )

        self.assertEqual(response.status_code, 200)
        quality_mock.assert_called_once_with(
            "SN-LIVE-QUALITY-001",
            video_id="SN-LIVE-QUALITY-001/88-0-0/normal-0",
            video_quality=3,
        )

    def test_live_switch_should_proxy_exact_upstream_payload(self):
        drone = Drone.objects.create(
            tenant=self.tenant,
            code="DJ-LIVE-SOURCE-001",
            name="直播视频源设备",
            model="M30",
            device_sn="SN-LIVE-SOURCE-001",
            created_by_tenant_member_id=self.member.id,
        )

        with patch(
            "apps.drone.views.DjiGateway.switch_live",
            return_value={"switched": True},
        ) as source_mock:
            response = self.client.post(
                f"/api/v1/drones/{drone.id}/live/switch",
                {"video_id": "SN-LIVE-SOURCE-001/88-0-0/normal-0", "videoType": "wide"},
                format="json",
            )

        self.assertEqual(response.status_code, 200)
        source_mock.assert_called_once_with(
            "SN-LIVE-SOURCE-001",
            video_id="SN-LIVE-SOURCE-001/88-0-0/normal-0",
            videoType="wide",
        )

    def test_assigned_scope_member_should_only_see_assigned_drones(self):
        pilot_user = User.objects.create_user(username="drone_pilot", password="pass1234", status=1)
        ensure_staff_profile(pilot_user, name="飞手", employment_status=EmploymentStatus.ACTIVE)
        _tenant, pilot_member, pilot_role = ensure_tenant_role_binding(
            pilot_user,
            tenant=self.tenant,
            role_code="pilot_operator",
            role_name="飞手",
        )
        ensure_tenant_member_position(pilot_member, code="pilot_operator", name="飞手")
        grant_role_permissions(pilot_role, {"drone.view_drone": ScopeType.ASSIGNED})

        visible_drone = Drone.objects.create(
            tenant=self.tenant,
            code="DJ-SCOPE-001",
            name="可见设备",
            model="M30",
            device_sn="SN-SCOPE-001",
        )
        hidden_drone = Drone.objects.create(
            tenant=self.tenant,
            code="DJ-SCOPE-002",
            name="不可见设备",
            model="M30",
            device_sn="SN-SCOPE-002",
        )
        DroneAssignment.objects.create(
            tenant=self.tenant,
            drone=visible_drone,
            tenant_member=pilot_member,
            status=DroneAssignmentStatus.ACTIVE,
        )

        pilot_client = APIClient()
        pilot_client.force_authenticate(pilot_user)
        pilot_client.credentials(HTTP_X_TENANT_CODE=self.tenant.code)

        response = pilot_client.get("/api/v1/drones")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["data"]["total"], 1)
        self.assertEqual(response.data["data"]["list"][0]["id"], visible_drone.id)
        self.assertNotEqual(response.data["data"]["list"][0]["id"], hidden_drone.id)
