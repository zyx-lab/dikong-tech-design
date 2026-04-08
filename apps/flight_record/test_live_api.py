"""Flight record live HTTP test events.

- 调度员通过真实 HTTP 完成飞行记录创建/查询/更新/完成/异常终止闭环
- business flight-record API 强制要求有效 Bearer + X-TENANT-CODE
- 完成/异常终止动作拒绝 body，并校验状态冲突与幂等语义
- 跨租户记录不可见，跨租户绑定关系按合同拒绝
- 同一 flight_no 仅在租户内唯一；不同租户可重复
- ASSIGNED 范围飞手仅能读取和创建自己的飞行记录
- platform_admin 即使权限矩阵放开也禁止访问租户业务接口
"""

from datetime import timedelta

from django.utils import timezone

from apps.access.models import AuditLog, DirectoryStatus, EmploymentStatus, Role, ScopeType
from apps.access.test_live_base import LiveIamApiTestCase, User
from apps.access.test_support import (
    ensure_staff_profile,
    ensure_tenant_member_position,
    ensure_tenant_role_binding,
    grant_role_permissions,
)
from apps.drone.models import Drone, DroneStatus
from apps.flight_record.models import FlightRecord, FlightRecordStatus
from apps.mission.models import Mission, MissionStatus
from apps.route.models import Route


def _persist_mission_fixture(**kwargs) -> Mission:
    # Live flight record tests only need persisted missions for downstream APIs.
    mission = Mission(**kwargs)
    Mission.objects.bulk_create([mission])
    return Mission.objects.get(pk=mission.pk)


class LiveFlightRecordApiTestCase(LiveIamApiTestCase):
    def setUp(self):
        super().setUp()
        self._flight_no_seq = 1

        self.admin_user = User.objects.create_user(username="flight_record_live_admin", password="pass1234", status=1)
        ensure_staff_profile(
            self.admin_user,
            staff_no="FRL-001",
            name="实时飞行记录管理员",
            employment_status=EmploymentStatus.ACTIVE,
        )
        self.tenant, self.admin_member, self.admin_role = ensure_tenant_role_binding(
            self.admin_user,
            tenant_code="flight_record_live_tenant",
            role_code="flight_record_live_admin_role",
            role_name="实时飞行记录管理角色",
        )
        grant_role_permissions(
            self.admin_role,
            {
                "flight_record.view_flight_record": ScopeType.ALL,
                "flight_record.manage_flight_record": ScopeType.ALL,
            },
            group_name="flight-record-live-admin-group",
        )

        self.other_admin_user = User.objects.create_user(username="flight_record_live_other_admin", password="pass1234", status=1)
        ensure_staff_profile(
            self.other_admin_user,
            staff_no="FRL-OTHER-001",
            name="其他租户飞行记录管理员",
            employment_status=EmploymentStatus.ACTIVE,
        )
        self.other_tenant, self.other_admin_member, self.other_admin_role = ensure_tenant_role_binding(
            self.other_admin_user,
            tenant_code="flight_record_live_other_tenant",
            role_code="flight_record_live_other_admin_role",
            role_name="其他租户实时飞行记录管理角色",
        )
        grant_role_permissions(
            self.other_admin_role,
            {
                "flight_record.view_flight_record": ScopeType.ALL,
                "flight_record.manage_flight_record": ScopeType.ALL,
            },
            group_name="flight-record-live-other-admin-group",
        )

        self.pilot_user = User.objects.create_user(username="flight_record_live_pilot", password="pass1234", status=1)
        self.pilot_staff = ensure_staff_profile(
            self.pilot_user,
            staff_no="FRL-P-001",
            name="实时飞手A",
            employment_status=EmploymentStatus.ACTIVE,
        )
        _tenant, self.pilot_member, _pilot_role = ensure_tenant_role_binding(
            self.pilot_user,
            tenant=self.tenant,
            role_code="pilot_operator",
            role_name="飞手",
        )
        ensure_tenant_member_position(self.pilot_member, code="pilot_operator", name="飞手")

        self.other_pilot_user = User.objects.create_user(username="flight_record_live_other_pilot", password="pass1234", status=1)
        self.other_pilot_staff = ensure_staff_profile(
            self.other_pilot_user,
            staff_no="FRL-P-002",
            name="实时飞手B",
            employment_status=EmploymentStatus.ACTIVE,
        )
        _tenant, self.other_pilot_member, _other_pilot_role = ensure_tenant_role_binding(
            self.other_pilot_user,
            tenant=self.tenant,
            role_code="pilot_operator",
            role_name="飞手",
        )
        ensure_tenant_member_position(self.other_pilot_member, code="pilot_operator", name="飞手")

        self.other_tenant_pilot_user = User.objects.create_user(
            username="flight_record_live_other_tenant_pilot",
            password="pass1234",
            status=1,
        )
        self.other_tenant_pilot_staff = ensure_staff_profile(
            self.other_tenant_pilot_user,
            staff_no="FRL-OTHER-P-001",
            name="其他租户飞手",
            employment_status=EmploymentStatus.ACTIVE,
        )
        _tenant, self.other_tenant_pilot_member, _other_tenant_pilot_role = ensure_tenant_role_binding(
            self.other_tenant_pilot_user,
            tenant=self.other_tenant,
            role_code="pilot_operator",
            role_name="飞手",
        )
        ensure_tenant_member_position(self.other_tenant_pilot_member, code="pilot_operator", name="飞手")

        self.route = Route.objects.create(tenant=self.tenant, name="实时飞行记录航线")
        self.drone = Drone.objects.create(
            tenant=self.tenant,
            code="FRL-DRN-001",
            name="实时飞行记录无人机",
            model="M300",
            device_sn="FRL-SN-001",
            status=DroneStatus.CLAIMED,
        )
        self.secondary_drone = Drone.objects.create(
            tenant=self.tenant,
            code="FRL-DRN-002",
            name="实时飞行记录无人机2",
            model="M350",
            device_sn="FRL-SN-002",
            status=DroneStatus.CLAIMED,
        )
        self.mission = _persist_mission_fixture(
            tenant=self.tenant,
            name="实时飞行记录任务",
            route=self.route,
            route_name=self.route.name,
            drone=self.drone,
            drone_name=self.drone.name,
            pilot=self.pilot_member,
            pilot_name=self.pilot_staff.name,
            status=MissionStatus.DRONE_BOUND,
        )
        self.other_mission = _persist_mission_fixture(
            tenant=self.tenant,
            name="实时飞行记录他人任务",
            route=self.route,
            route_name=self.route.name,
            drone=self.drone,
            drone_name=self.drone.name,
            pilot=self.other_pilot_member,
            pilot_name=self.other_pilot_staff.name,
            status=MissionStatus.DRONE_BOUND,
        )

        self.other_route = Route.objects.create(tenant=self.other_tenant, name="其他租户飞行记录航线")
        self.other_drone = Drone.objects.create(
            tenant=self.other_tenant,
            code="FRL-OTHER-DRN-001",
            name="其他租户飞行记录无人机",
            model="M350",
            device_sn="FRL-OTHER-SN-001",
            status=DroneStatus.CLAIMED,
        )
        self.other_tenant_mission = _persist_mission_fixture(
            tenant=self.other_tenant,
            name="其他租户飞行记录任务",
            route=self.other_route,
            route_name=self.other_route.name,
            drone=self.other_drone,
            drone_name=self.other_drone.name,
            pilot=self.other_tenant_pilot_member,
            pilot_name=self.other_tenant_pilot_staff.name,
            status=MissionStatus.DRONE_BOUND,
        )

        self.login(username="flight_record_live_admin", password="pass1234", tenant_code=self.tenant.code)

    def _next_flight_no(self) -> str:
        flight_no = f"FRL20260321{self._flight_no_seq:04d}"
        self._flight_no_seq += 1
        return flight_no

    def _create_record(
        self,
        *,
        flight_no: str | None = None,
        tenant=None,
        mission=None,
        drone=None,
        pilot=None,
        pilot_name: str | None = None,
        status: int = FlightRecordStatus.COMPLETED,
        start_time=None,
        end_time=None,
        airport_name: str = "珠海金湾机场",
        photo_count: int = 12,
        video_count: int = 3,
    ) -> FlightRecord:
        tenant = tenant or self.tenant
        mission = mission or (self.mission if tenant == self.tenant else self.other_tenant_mission)
        drone = drone or (self.drone if tenant == self.tenant else self.other_drone)
        pilot = pilot or (self.pilot_member if tenant == self.tenant else self.other_tenant_pilot_member)
        if pilot_name is None:
            if pilot.id == self.pilot_member.id:
                pilot_name = self.pilot_staff.name
            elif pilot.id == self.other_pilot_member.id:
                pilot_name = self.other_pilot_staff.name
            elif pilot.id == self.other_tenant_pilot_member.id:
                pilot_name = self.other_tenant_pilot_staff.name
            else:
                pilot_name = pilot.display_name or pilot.user.username
        start_time = start_time or timezone.now() - timedelta(minutes=20)
        end_time = end_time or timezone.now()
        duration_seconds = int((end_time - start_time).total_seconds()) if start_time and end_time else None
        return FlightRecord.objects.create(
            tenant=tenant,
            flight_no=flight_no or self._next_flight_no(),
            mission=mission,
            mission_name=mission.name if mission else "",
            route_name=mission.route_name if mission else "",
            airport_name=airport_name,
            drone=drone,
            drone_name=drone.name if drone else "",
            pilot=pilot,
            pilot_name=pilot_name,
            start_time=start_time,
            end_time=end_time,
            flight_duration=duration_seconds,
            photo_count=photo_count,
            video_count=video_count,
            status=status,
        )


class LiveFlightRecordApiTests(LiveFlightRecordApiTestCase):
    def test_flight_record_lifecycle_should_follow_live_http_contract(self):
        create_response = self.client.post(
            "/api/v1/flight-records",
            {
                "flight_no": "FRL202603210900",
                "mission": self.mission.id,
                "drone": self.drone.id,
                "pilot": self.pilot_member.id,
                "airport_name": "珠海金湾机场",
                "start_time": "2026-03-21T08:00:00+08:00",
                "end_time": "2026-03-21T08:20:00+08:00",
                "photo_count": 20,
                "video_count": 4,
                "status": FlightRecordStatus.IN_PROGRESS,
            },
            format="json",
        )
        self.assertEqual(create_response.status_code, 201)
        self.assertEqual(create_response.json()["code"], "00000")
        create_data = create_response.json()["data"]
        self.assertEqual(create_data["flight_no"], "FRL202603210900")
        self.assertEqual(create_data["mission"], self.mission.id)
        self.assertEqual(create_data["drone"], self.drone.id)
        self.assertEqual(create_data["pilot"], self.pilot_member.id)
        self.assertEqual(create_data["mission_name"], self.mission.name)
        self.assertEqual(create_data["drone_name"], self.drone.name)
        self.assertEqual(create_data["pilot_name"], self.pilot_staff.name)
        self.assertEqual(create_data["route_name"], self.route.name)
        self.assertEqual(create_data["flight_duration"], 1200)
        self.assertEqual(create_data["status"], FlightRecordStatus.IN_PROGRESS)
        record_id = create_data["id"]

        list_response = self.client.get(
            "/api/v1/flight-records",
            {"status": FlightRecordStatus.IN_PROGRESS, "flight_no": "FRL2026032109"},
        )
        self.assertEqual(list_response.status_code, 200)
        self.assertEqual(list_response.json()["code"], "00000")
        self.assertEqual(list_response.json()["data"]["total"], 1)
        self.assertEqual(list_response.json()["data"]["list"][0]["id"], record_id)

        retrieve_response = self.client.get(f"/api/v1/flight-records/{record_id}")
        self.assertEqual(retrieve_response.status_code, 200)
        self.assertEqual(retrieve_response.json()["data"]["flight_no"], "FRL202603210900")

        patch_response = self.client.patch(
            f"/api/v1/flight-records/{record_id}",
            {
                "airport_name": "深圳宝安机场",
                "end_time": "2026-03-21T08:25:00+08:00",
                "photo_count": 22,
                "video_count": 5,
            },
            format="json",
        )
        self.assertEqual(patch_response.status_code, 200)
        self.assertEqual(patch_response.json()["code"], "00000")
        self.assertEqual(patch_response.json()["data"]["airport_name"], "深圳宝安机场")
        self.assertEqual(patch_response.json()["data"]["photo_count"], 22)
        self.assertEqual(patch_response.json()["data"]["video_count"], 5)
        self.assertEqual(patch_response.json()["data"]["flight_duration"], 1500)
        self.assertEqual(patch_response.json()["data"]["status"], FlightRecordStatus.IN_PROGRESS)

        complete_response = self.client.post(f"/api/v1/flight-records/{record_id}/complete")
        self.assertEqual(complete_response.status_code, 200)
        self.assertEqual(complete_response.json()["data"]["status"], FlightRecordStatus.COMPLETED)

        complete_again_response = self.client.post(f"/api/v1/flight-records/{record_id}/complete")
        self.assertEqual(complete_again_response.status_code, 200)
        self.assertEqual(complete_again_response.json()["data"]["status"], FlightRecordStatus.COMPLETED)

        abort_record = self._create_record(flight_no="FRL202603210901", status=FlightRecordStatus.IN_PROGRESS)
        abort_response = self.client.post(f"/api/v1/flight-records/{abort_record.id}/abort")
        self.assertEqual(abort_response.status_code, 200)
        self.assertEqual(abort_response.json()["data"]["status"], FlightRecordStatus.ABORTED)

        abort_again_response = self.client.post(f"/api/v1/flight-records/{abort_record.id}/abort")
        self.assertEqual(abort_again_response.status_code, 200)
        self.assertEqual(abort_again_response.json()["data"]["status"], FlightRecordStatus.ABORTED)

        record = FlightRecord.objects.get(id=record_id)
        self.assertEqual(record.status, FlightRecordStatus.COMPLETED)
        self.assertTrue(
            AuditLog.objects.filter(
                tenant=self.tenant,
                action="FLIGHT_RECORD_CREATE",
                target_type="flight_record",
                target_id=str(record_id),
            ).exists()
        )
        self.assertTrue(
            AuditLog.objects.filter(
                tenant=self.tenant,
                action="FLIGHT_RECORD_UPDATE",
                target_type="flight_record",
                target_id=str(record_id),
            ).exists()
        )
        self.assertTrue(
            AuditLog.objects.filter(
                tenant=self.tenant,
                action="FLIGHT_RECORD_COMPLETE",
                target_type="flight_record",
                target_id=str(record_id),
            ).exists()
        )
        self.assertTrue(
            AuditLog.objects.filter(
                tenant=self.tenant,
                action="FLIGHT_RECORD_ABORT",
                target_type="flight_record",
                target_id=str(abort_record.id),
            ).exists()
        )

    def test_duplicate_and_invalid_write_contract_should_be_rejected_over_live_http(self):
        existing = self._create_record(
            flight_no="FRL202603211100",
            status=FlightRecordStatus.IN_PROGRESS,
            start_time=timezone.now() - timedelta(minutes=10),
            end_time=timezone.now(),
        )

        duplicate_response = self.client.post(
            "/api/v1/flight-records",
            {
                "flight_no": "FRL202603211100",
                "mission": self.mission.id,
                "drone": self.drone.id,
                "pilot": self.pilot_member.id,
                "status": FlightRecordStatus.IN_PROGRESS,
            },
            format="json",
        )
        self.assertEqual(duplicate_response.status_code, 400)
        self.assertEqual(duplicate_response.json()["code"], "C0101")
        self.assertIn("flight_no", duplicate_response.json()["data"])

        patch_status_response = self.client.patch(
            f"/api/v1/flight-records/{existing.id}",
            {"status": FlightRecordStatus.COMPLETED},
            format="json",
        )
        self.assertEqual(patch_status_response.status_code, 400)
        self.assertEqual(patch_status_response.json()["code"], "B0001")
        self.assertIn("status", patch_status_response.json()["data"])

        empty_patch_response = self.client.patch(
            f"/api/v1/flight-records/{existing.id}",
            {},
            format="json",
        )
        self.assertEqual(empty_patch_response.status_code, 400)
        self.assertEqual(empty_patch_response.json()["code"], "B0001")
        self.assertIn("body", empty_patch_response.json()["data"])

        invalid_time_response = self.client.patch(
            f"/api/v1/flight-records/{existing.id}",
            {"end_time": "2026-03-21T07:00:00+08:00"},
            format="json",
        )
        self.assertEqual(invalid_time_response.status_code, 400)
        self.assertEqual(invalid_time_response.json()["code"], "B0001")
        self.assertIn("end_time", invalid_time_response.json()["data"])

        missing_flight_no_response = self.client.post(
            "/api/v1/flight-records",
            {"mission": self.mission.id, "drone": self.drone.id, "pilot": self.pilot_member.id},
            format="json",
        )
        self.assertEqual(missing_flight_no_response.status_code, 400)
        self.assertEqual(missing_flight_no_response.json()["code"], "B0001")
        self.assertIn("flight_no", missing_flight_no_response.json()["data"])

    def test_transition_contract_should_reject_invalid_body_and_conflicting_statuses_over_live_http(self):
        in_progress_record = self._create_record(flight_no="FRL202603211200", status=FlightRecordStatus.IN_PROGRESS)
        completed_record = self._create_record(flight_no="FRL202603211201", status=FlightRecordStatus.COMPLETED)
        aborted_record = self._create_record(flight_no="FRL202603211202", status=FlightRecordStatus.ABORTED)

        complete_with_body_response = self.client.post(
            f"/api/v1/flight-records/{in_progress_record.id}/complete",
            {"unexpected": True},
            format="json",
        )
        self.assertEqual(complete_with_body_response.status_code, 400)
        self.assertEqual(complete_with_body_response.json()["code"], "B0001")
        self.assertIn("body", complete_with_body_response.json()["data"])

        abort_with_body_response = self.client.post(
            f"/api/v1/flight-records/{in_progress_record.id}/abort",
            {"unexpected": True},
            format="json",
        )
        self.assertEqual(abort_with_body_response.status_code, 400)
        self.assertEqual(abort_with_body_response.json()["code"], "B0001")
        self.assertIn("body", abort_with_body_response.json()["data"])

        abort_completed_response = self.client.post(f"/api/v1/flight-records/{completed_record.id}/abort")
        self.assertEqual(abort_completed_response.status_code, 409)
        self.assertEqual(abort_completed_response.json()["code"], "C0201")

        complete_aborted_response = self.client.post(f"/api/v1/flight-records/{aborted_record.id}/complete")
        self.assertEqual(complete_aborted_response.status_code, 409)
        self.assertEqual(complete_aborted_response.json()["code"], "C0201")

        in_progress_record.refresh_from_db()
        self.assertEqual(in_progress_record.status, FlightRecordStatus.IN_PROGRESS)

    def test_cross_tenant_visibility_and_binding_should_follow_live_http_contract(self):
        foreign_record = self._create_record(
            flight_no="FRL202603211300",
            tenant=self.other_tenant,
            mission=self.other_tenant_mission,
            drone=self.other_drone,
            pilot=self.other_tenant_pilot_member,
            pilot_name=self.other_tenant_pilot_staff.name,
            status=FlightRecordStatus.IN_PROGRESS,
        )

        list_response = self.client.get("/api/v1/flight-records")
        self.assertEqual(list_response.status_code, 200)
        self.assertEqual(list_response.json()["code"], "00000")
        self.assertEqual(list_response.json()["data"]["total"], 0)

        retrieve_response = self.client.get(f"/api/v1/flight-records/{foreign_record.id}")
        self.assertEqual(retrieve_response.status_code, 404)
        self.assertEqual(retrieve_response.json()["code"], "C0404")

        patch_response = self.client.patch(
            f"/api/v1/flight-records/{foreign_record.id}",
            {"airport_name": "越权修改"},
            format="json",
        )
        self.assertEqual(patch_response.status_code, 404)
        self.assertEqual(patch_response.json()["code"], "C0404")

        complete_response = self.client.post(f"/api/v1/flight-records/{foreign_record.id}/complete")
        self.assertEqual(complete_response.status_code, 404)
        self.assertEqual(complete_response.json()["code"], "C0404")

        abort_response = self.client.post(f"/api/v1/flight-records/{foreign_record.id}/abort")
        self.assertEqual(abort_response.status_code, 404)
        self.assertEqual(abort_response.json()["code"], "C0404")

        foreign_mission_response = self.client.post(
            "/api/v1/flight-records",
            {
                "flight_no": "FRL202603211301",
                "mission": self.other_tenant_mission.id,
                "drone": self.drone.id,
                "pilot": self.pilot_member.id,
                "status": FlightRecordStatus.IN_PROGRESS,
            },
            format="json",
        )
        self.assertEqual(foreign_mission_response.status_code, 400)
        self.assertEqual(foreign_mission_response.json()["code"], "B0001")
        self.assertIn("mission", foreign_mission_response.json()["data"])

        foreign_drone_response = self.client.post(
            "/api/v1/flight-records",
            {
                "flight_no": "FRL202603211302",
                "mission": self.mission.id,
                "drone": self.other_drone.id,
                "pilot": self.pilot_member.id,
                "status": FlightRecordStatus.IN_PROGRESS,
            },
            format="json",
        )
        self.assertEqual(foreign_drone_response.status_code, 400)
        self.assertEqual(foreign_drone_response.json()["code"], "B0001")
        self.assertIn("drone", foreign_drone_response.json()["data"])

        foreign_pilot_response = self.client.post(
            "/api/v1/flight-records",
            {
                "flight_no": "FRL202603211303",
                "mission": self.mission.id,
                "drone": self.drone.id,
                "pilot": self.other_tenant_pilot_member.id,
                "status": FlightRecordStatus.IN_PROGRESS,
            },
            format="json",
        )
        self.assertEqual(foreign_pilot_response.status_code, 400)
        self.assertEqual(foreign_pilot_response.json()["code"], "B0001")
        self.assertIn("pilot", foreign_pilot_response.json()["data"])

        mismatch_drone_response = self.client.post(
            "/api/v1/flight-records",
            {
                "flight_no": "FRL202603211304",
                "mission": self.mission.id,
                "drone": self.secondary_drone.id,
                "pilot": self.pilot_member.id,
                "status": FlightRecordStatus.IN_PROGRESS,
            },
            format="json",
        )
        self.assertEqual(mismatch_drone_response.status_code, 400)
        self.assertEqual(mismatch_drone_response.json()["code"], "B0001")
        self.assertIn("drone", mismatch_drone_response.json()["data"])

        mismatch_pilot_response = self.client.post(
            "/api/v1/flight-records",
            {
                "flight_no": "FRL202603211305",
                "mission": self.mission.id,
                "drone": self.drone.id,
                "pilot": self.other_pilot_member.id,
                "status": FlightRecordStatus.IN_PROGRESS,
            },
            format="json",
        )
        self.assertEqual(mismatch_pilot_response.status_code, 400)
        self.assertEqual(mismatch_pilot_response.json()["code"], "B0001")
        self.assertIn("pilot", mismatch_pilot_response.json()["data"])

        duplicate_cross_tenant_allowed_response = self.client.post(
            "/api/v1/flight-records",
            {
                "flight_no": "FRL202603211300",
                "mission": self.mission.id,
                "drone": self.drone.id,
                "pilot": self.pilot_member.id,
                "status": FlightRecordStatus.IN_PROGRESS,
            },
            format="json",
        )
        self.assertEqual(duplicate_cross_tenant_allowed_response.status_code, 201)
        self.assertEqual(duplicate_cross_tenant_allowed_response.json()["code"], "00000")
        self.assertEqual(duplicate_cross_tenant_allowed_response.json()["data"]["flight_no"], "FRL202603211300")

    def test_business_flight_record_api_should_require_tenant_context(self):
        record = self._create_record(flight_no="FRL202603211400")
        tenantless_client = self.new_client()
        self.authenticate_client(
            tenantless_client,
            username="flight_record_live_admin",
            password="pass1234",
        )

        list_response = tenantless_client.get("/api/v1/flight-records")
        self.assertEqual(list_response.status_code, 403)
        self.assertEqual(list_response.json()["code"], "A0403")

        detail_response = tenantless_client.get(f"/api/v1/flight-records/{record.id}")
        self.assertEqual(detail_response.status_code, 403)
        self.assertEqual(detail_response.json()["code"], "A0403")

        create_response = tenantless_client.post(
            "/api/v1/flight-records",
            {
                "flight_no": "FRL202603211401",
                "mission": self.mission.id,
                "drone": self.drone.id,
                "pilot": self.pilot_member.id,
                "status": FlightRecordStatus.IN_PROGRESS,
            },
            format="json",
        )
        self.assertEqual(create_response.status_code, 403)
        self.assertEqual(create_response.json()["code"], "A0403")

    def test_platform_admin_should_be_blocked_from_business_flight_record_api_even_with_permission(self):
        platform_role, _ = Role.objects.update_or_create(
            code="platform_admin",
            defaults={"name": "平台管理员", "status": DirectoryStatus.ACTIVE},
        )
        grant_role_permissions(
            platform_role,
            {
                "flight_record.view_flight_record": ScopeType.ALL,
                "flight_record.manage_flight_record": ScopeType.ALL,
            },
            group_name="flight-record-live-platform-group",
        )
        platform_user = User.objects.create_user(
            username="flight_record_live_platform_admin",
            password="pass1234",
            status=1,
            is_platform_admin=True,
        )
        ensure_staff_profile(platform_user, name="平台飞行记录管理员")
        platform_client = self.new_client()
        self.authenticate_client(
            platform_client,
            username="flight_record_live_platform_admin",
            password="pass1234",
            tenant_code=self.tenant.code,
        )

        response = platform_client.get("/api/v1/flight-records")

        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["code"], "A0403")


class LiveFlightRecordPilotScopeTests(LiveFlightRecordApiTestCase):
    def setUp(self):
        super().setUp()
        pilot_role = self.pilot_member.role_bindings.get(system_role__code="pilot_operator").system_role
        grant_role_permissions(
            pilot_role,
            {
                "flight_record.view_flight_record": ScopeType.ASSIGNED,
                "flight_record.manage_flight_record": ScopeType.ASSIGNED,
            },
            group_name="flight-record-live-pilot-group",
        )
        self.my_record = self._create_record(
            flight_no="FRL202603211500",
            mission=self.mission,
            pilot=self.pilot_member,
            pilot_name=self.pilot_staff.name,
        )
        self.other_record = self._create_record(
            flight_no="FRL202603211501",
            mission=self.other_mission,
            pilot=self.other_pilot_member,
            pilot_name=self.other_pilot_staff.name,
        )
        self.pilot_client = self.new_client()
        self.authenticate_client(
            self.pilot_client,
            username="flight_record_live_pilot",
            password="pass1234",
            tenant_code=self.tenant.code,
        )

    def test_assigned_scope_pilot_should_only_list_and_retrieve_own_flight_records(self):
        list_response = self.pilot_client.get("/api/v1/flight-records")
        self.assertEqual(list_response.status_code, 200)
        self.assertEqual(list_response.json()["code"], "00000")
        self.assertEqual(list_response.json()["data"]["total"], 1)
        self.assertEqual(list_response.json()["data"]["list"][0]["id"], self.my_record.id)

        own_detail_response = self.pilot_client.get(f"/api/v1/flight-records/{self.my_record.id}")
        self.assertEqual(own_detail_response.status_code, 200)
        self.assertEqual(own_detail_response.json()["data"]["id"], self.my_record.id)

        other_detail_response = self.pilot_client.get(f"/api/v1/flight-records/{self.other_record.id}")
        self.assertEqual(other_detail_response.status_code, 404)
        self.assertEqual(other_detail_response.json()["code"], "C0404")

    def test_assigned_scope_pilot_should_only_create_own_flight_records(self):
        response = self.pilot_client.post(
            "/api/v1/flight-records",
            {
                "flight_no": "FRL202603211502",
                "mission": self.other_mission.id,
                "drone": self.drone.id,
                "airport_name": "珠海金湾机场",
                "start_time": "2026-03-21T09:00:00+08:00",
                "end_time": "2026-03-21T09:10:00+08:00",
                "status": FlightRecordStatus.IN_PROGRESS,
            },
            format="json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["code"], "B0001")
        self.assertIn("pilot", response.json()["data"])

    def test_assigned_scope_pilot_should_not_mutate_other_flight_records(self):
        patch_response = self.pilot_client.patch(
            f"/api/v1/flight-records/{self.other_record.id}",
            {"airport_name": "越权修改"},
            format="json",
        )
        self.assertEqual(patch_response.status_code, 404)
        self.assertEqual(patch_response.json()["code"], "C0404")

        put_response = self.pilot_client.put(
            f"/api/v1/flight-records/{self.other_record.id}",
            {
                "flight_no": self.other_record.flight_no,
                "mission": self.other_mission.id,
                "drone": self.drone.id,
                "pilot": self.other_pilot_member.id,
                "airport_name": "越权全量修改",
                "start_time": self.other_record.start_time.isoformat(),
                "end_time": self.other_record.end_time.isoformat(),
                "photo_count": self.other_record.photo_count,
                "video_count": self.other_record.video_count,
                "status": self.other_record.status,
            },
            format="json",
        )
        self.assertEqual(put_response.status_code, 404)
        self.assertEqual(put_response.json()["code"], "C0404")

        complete_response = self.pilot_client.post(f"/api/v1/flight-records/{self.other_record.id}/complete")
        self.assertEqual(complete_response.status_code, 404)
        self.assertEqual(complete_response.json()["code"], "C0404")

        abort_response = self.pilot_client.post(f"/api/v1/flight-records/{self.other_record.id}/abort")
        self.assertEqual(abort_response.status_code, 404)
        self.assertEqual(abort_response.json()["code"], "C0404")
