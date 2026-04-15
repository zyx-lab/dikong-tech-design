"""Flight record live HTTP contract tests."""

from datetime import timedelta

from django.utils import timezone

from apps.access.models import EmploymentStatus, ScopeType
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
    mission = Mission(**kwargs)
    Mission.objects.bulk_create([mission])
    return Mission.objects.get(pk=mission.pk)


class LiveFlightRecordApiTestCase(LiveIamApiTestCase):
    def setUp(self):
        super().setUp()
        self._mission_seq = 1
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

        self.pilot_user = User.objects.create_user(username="flight_record_live_pilot", password="pass1234", status=1)
        self.pilot_staff = ensure_staff_profile(
            self.pilot_user,
            staff_no="FRL-P-001",
            name="实时飞手A",
            employment_status=EmploymentStatus.ACTIVE,
        )
        _tenant, self.pilot_member, self.pilot_role = ensure_tenant_role_binding(
            self.pilot_user,
            tenant=self.tenant,
            role_code="pilot_operator",
            role_name="飞手",
        )
        ensure_tenant_member_position(self.pilot_member, code="pilot_operator", name="飞手")
        grant_role_permissions(
            self.pilot_role,
            {
                "flight_record.view_flight_record": ScopeType.ASSIGNED,
                "flight_record.manage_flight_record": ScopeType.ASSIGNED,
            },
            group_name="flight-record-live-pilot-group",
        )

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

        self.route = Route.objects.create(tenant=self.tenant, name="实时飞行记录航线")
        self.drone = Drone.objects.create(
            tenant=self.tenant,
            code="FRL-DRN-001",
            name="实时飞行记录无人机",
            model="M300",
            device_sn="FRL-SN-001",
            status=DroneStatus.CLAIMED,
        )

        self.login(username="flight_record_live_admin", password="pass1234", tenant_code=self.tenant.code)

    def _create_mission(self, *, pilot=None, pilot_name=None, name: str | None = None) -> Mission:
        pilot = pilot or self.pilot_member
        pilot_name = pilot_name or self.pilot_staff.name
        name = name or f"实时飞行记录任务{self._mission_seq}"
        self._mission_seq += 1
        return _persist_mission_fixture(
            tenant=self.tenant,
            name=name,
            route=self.route,
            route_name=self.route.name,
            drone=self.drone,
            device_sn=self.drone.device_sn,
            drone_name=self.drone.name,
            pilot=pilot,
            pilot_name=pilot_name,
            status=MissionStatus.COMPLETED,
            started_at=timezone.now() - timedelta(minutes=10),
            finished_at=timezone.now() - timedelta(minutes=1),
        )

    def _create_record(self, *, mission=None, pilot=None, pilot_name=None, flight_no: str | None = None) -> FlightRecord:
        mission = mission or self._create_mission(pilot=pilot, pilot_name=pilot_name)
        flight_no = flight_no or f"FRL20260415{self._flight_no_seq:04d}"
        self._flight_no_seq += 1
        return FlightRecord.objects.create(
            tenant=self.tenant,
            flight_no=flight_no,
            mission=mission,
            mission_name=mission.name,
            route_name=mission.route_name,
            airport_name="珠海金湾机场",
            drone=self.drone,
            device_sn=self.drone.device_sn,
            drone_name=self.drone.name,
            pilot=mission.pilot,
            pilot_name=mission.pilot_name,
            start_time=mission.started_at,
            end_time=mission.finished_at,
            flight_duration=int((mission.finished_at - mission.started_at).total_seconds()),
            photo_count=0,
            video_count=2,
            status=FlightRecordStatus.COMPLETED,
        )


class LiveFlightRecordApiTests(LiveFlightRecordApiTestCase):
    def test_list_retrieve_update_delete_should_follow_live_http_contract(self):
        record = self._create_record()

        list_response = self.client.get("/api/v1/flight-records")
        self.assertEqual(list_response.status_code, 200)
        list_data = list_response.json()["data"]
        self.assertEqual(list_data["total"], 1)
        self.assertEqual(list_data["list"][0]["id"], record.id)

        retrieve_response = self.client.get(f"/api/v1/flight-records/{record.id}")
        self.assertEqual(retrieve_response.status_code, 200)
        self.assertEqual(retrieve_response.json()["data"]["device_sn"], self.drone.device_sn)

        put_response = self.client.put(
            f"/api/v1/flight-records/{record.id}",
            {
                "mission_name": "修正后的任务名称",
                "airport_name": "深圳宝安机场",
            },
            format="json",
        )
        self.assertEqual(put_response.status_code, 200)
        put_data = put_response.json()["data"]
        self.assertEqual(put_data["mission_name"], "修正后的任务名称")
        self.assertEqual(put_data["airport_name"], "深圳宝安机场")
        self.assertEqual(put_data["video_count"], record.video_count)

        invalid_put_response = self.client.put(
            f"/api/v1/flight-records/{record.id}",
            {"video_count": 5},
            format="json",
        )
        self.assertEqual(invalid_put_response.status_code, 400)

        delete_response = self.client.delete(f"/api/v1/flight-records/{record.id}")
        self.assertEqual(delete_response.status_code, 200)
        self.assertTrue(delete_response.json()["data"]["deleted"])

        deleted_detail_response = self.client.get(f"/api/v1/flight-records/{record.id}")
        self.assertEqual(deleted_detail_response.status_code, 404)

    def test_disabled_write_actions_should_return_method_not_allowed(self):
        record = self._create_record()

        create_response = self.client.post(
            "/api/v1/flight-records",
            {"flight_no": "FRL202604159999"},
            format="json",
        )
        complete_response = self.client.post(f"/api/v1/flight-records/{record.id}/complete")
        abort_response = self.client.post(f"/api/v1/flight-records/{record.id}/abort")
        patch_response = self.client.patch(
            f"/api/v1/flight-records/{record.id}",
            {"airport_name": "深圳宝安机场"},
            format="json",
        )

        self.assertEqual(create_response.status_code, 405)
        self.assertEqual(complete_response.status_code, 405)
        self.assertEqual(abort_response.status_code, 405)
        self.assertEqual(patch_response.status_code, 405)

    def test_business_flight_record_api_should_require_tenant_context(self):
        record = self._create_record()
        tenantless_client = self.new_client()
        self.authenticate_client(tenantless_client, username="flight_record_live_admin", password="pass1234")

        response = tenantless_client.get(f"/api/v1/flight-records/{record.id}")

        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["code"], "A0403")


class LiveFlightRecordPilotScopeTests(LiveFlightRecordApiTestCase):
    def setUp(self):
        super().setUp()
        self.my_record = self._create_record(pilot=self.pilot_member, pilot_name=self.pilot_staff.name)
        self.other_record = self._create_record(pilot=self.other_pilot_member, pilot_name=self.other_pilot_staff.name)
        self.login(username="flight_record_live_pilot", password="pass1234", tenant_code=self.tenant.code)

    def test_assigned_scope_pilot_should_only_list_and_update_own_flight_records(self):
        list_response = self.client.get("/api/v1/flight-records")
        self.assertEqual(list_response.status_code, 200)
        self.assertEqual(list_response.json()["data"]["total"], 1)
        self.assertEqual(list_response.json()["data"]["list"][0]["id"], self.my_record.id)

        own_update_response = self.client.put(
            f"/api/v1/flight-records/{self.my_record.id}",
            {"airport_name": "深圳宝安机场"},
            format="json",
        )
        self.assertEqual(own_update_response.status_code, 200)

        other_detail_response = self.client.get(f"/api/v1/flight-records/{self.other_record.id}")
        self.assertEqual(other_detail_response.status_code, 404)

        other_update_response = self.client.put(
            f"/api/v1/flight-records/{self.other_record.id}",
            {"airport_name": "深圳宝安机场"},
            format="json",
        )
        self.assertEqual(other_update_response.status_code, 404)
