"""Mission live HTTP test events.

- 调度员通过真实 HTTP 创建任务并驱动状态流转
- business mission API 强制要求有效 Bearer + X-TENANT-CODE
- 动作接口拒绝 body
- platform_admin 禁止访问租户业务 mission API
- ASSIGNED 作用域飞手仅能读取自己的任务
"""

from apps.access.models import AuditLog, EmploymentStatus, ScopeType, TenantMemberStatus
from apps.access.test_live_base import LiveIamApiTestCase, User
from apps.access.test_support import (
    ensure_staff_profile,
    ensure_tenant_member_position,
    ensure_tenant_role_binding,
    grant_role_permissions,
)
from apps.drone.models import Drone, DroneStatus
from apps.mission.models import Mission, MissionStatus
from apps.route.models import Route, RouteStatus


class LiveMissionApiTestCase(LiveIamApiTestCase):
    def setUp(self):
        super().setUp()
        self.dispatcher_user = User.objects.create_user(username="mission_live_dispatcher", password="pass1234", status=1)
        ensure_staff_profile(
            self.dispatcher_user,
            staff_no="ML-001",
            name="实时任务调度员",
            employment_status=EmploymentStatus.ACTIVE,
        )
        self.tenant, self.dispatcher_member, self.dispatcher_role = ensure_tenant_role_binding(
            self.dispatcher_user,
            tenant_code="mission_live_tenant",
            role_code="mission_live_dispatcher_role",
            role_name="实时任务调度角色",
        )
        grant_role_permissions(
            self.dispatcher_role,
            {
                "mission.view_mission": ScopeType.ALL,
                "mission.manage_mission": ScopeType.ALL,
            },
            group_name="mission-live-dispatcher-group",
        )

        self.other_dispatcher_user = User.objects.create_user(username="mission_live_other_dispatcher", password="pass1234", status=1)
        ensure_staff_profile(
            self.other_dispatcher_user,
            staff_no="ML-OTHER-001",
            name="其他租户任务调度员",
            employment_status=EmploymentStatus.ACTIVE,
        )
        self.other_tenant, self.other_dispatcher_member, self.other_dispatcher_role = ensure_tenant_role_binding(
            self.other_dispatcher_user,
            tenant_code="mission_live_other_tenant",
            role_code="mission_live_other_dispatcher_role",
            role_name="其他租户任务调度角色",
        )
        grant_role_permissions(
            self.other_dispatcher_role,
            {
                "mission.view_mission": ScopeType.ALL,
                "mission.manage_mission": ScopeType.ALL,
            },
            group_name="mission-live-other-dispatcher-group",
        )

        self.pilot_user = User.objects.create_user(username="mission_live_pilot", password="pass1234", status=1)
        self.pilot_staff = ensure_staff_profile(
            self.pilot_user,
            staff_no="ML-P-001",
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

        self.other_pilot_user = User.objects.create_user(username="mission_live_other_pilot", password="pass1234", status=1)
        self.other_pilot_staff = ensure_staff_profile(
            self.other_pilot_user,
            staff_no="ML-P-002",
            name="实时飞手B",
            employment_status=EmploymentStatus.ACTIVE,
        )
        _tenant, self.other_pilot_member, _other_role = ensure_tenant_role_binding(
            self.other_pilot_user,
            tenant=self.tenant,
            role_code="pilot_operator",
            role_name="飞手",
        )
        ensure_tenant_member_position(self.other_pilot_member, code="pilot_operator", name="飞手")

        self.other_tenant_pilot_user = User.objects.create_user(username="mission_live_other_tenant_pilot", password="pass1234", status=1)
        self.other_tenant_pilot_staff = ensure_staff_profile(
            self.other_tenant_pilot_user,
            staff_no="ML-OTHER-P-001",
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

        self.route = Route.objects.create(tenant=self.tenant, name="实时任务航线", status=RouteStatus.ACTIVE)
        self.drone = Drone.objects.create(
            tenant=self.tenant,
            code="MISSION-LIVE-001",
            name="实时任务无人机",
            model="M300",
            serial_no="MISSION-LIVE-SN-001",
            status=DroneStatus.ENABLED,
        )
        self.other_route = Route.objects.create(tenant=self.other_tenant, name="其他租户任务航线", status=RouteStatus.ACTIVE)
        self.other_drone = Drone.objects.create(
            tenant=self.other_tenant,
            code="MISSION-LIVE-OTHER-001",
            name="其他租户任务无人机",
            model="M350",
            serial_no="MISSION-LIVE-OTHER-SN-001",
            status=DroneStatus.ENABLED,
        )
        self.login(username="mission_live_dispatcher", password="pass1234", tenant_code=self.tenant.code)

    def _create_mission(
        self,
        *,
        name: str,
        status: int = MissionStatus.PENDING,
        pilot=None,
        tenant=None,
        route=None,
        drone=None,
        pilot_name: str | None = None,
    ) -> Mission:
        tenant = tenant or self.tenant
        route = route or (self.route if tenant == self.tenant else self.other_route)
        drone = drone or (self.drone if tenant == self.tenant else self.other_drone)
        pilot_member = pilot or self.pilot_member
        if pilot_name is None:
            if pilot_member.id == self.pilot_member.id:
                pilot_name = self.pilot_staff.name
            elif pilot_member.id == self.other_pilot_member.id:
                pilot_name = self.other_pilot_staff.name
            else:
                pilot_name = pilot_member.display_name or pilot_member.user.username
        return Mission.objects.create(
            tenant=tenant,
            name=name,
            route=route,
            route_name=route.name,
            drone=drone,
            drone_name=drone.name,
            pilot=pilot_member,
            pilot_name=pilot_name,
            status=status,
        )


class LiveMissionApiTests(LiveMissionApiTestCase):
    def test_create_and_transition_mission_should_follow_live_http_contract(self):
        create_response = self.client.post(
            "/api/v1/missions",
            {
                "name": "实时巡检任务",
                "route": self.route.id,
                "drone": self.drone.id,
                "pilot": self.pilot_member.id,
                "scheduled_at": "2026-03-21T09:00:00+08:00",
                "remark": "实时环境执行",
            },
            format="json",
        )
        self.assertEqual(create_response.status_code, 201)
        create_data = create_response.json()["data"]
        self.assertEqual(create_response.json()["code"], "00000")
        self.assertEqual(create_data["status"], MissionStatus.PENDING)
        self.assertEqual(create_data["route_name"], self.route.name)
        self.assertEqual(create_data["drone_name"], self.drone.name)
        self.assertEqual(create_data["pilot_name"], self.pilot_staff.name)
        mission_id = create_data["id"]

        self.assertTrue(
            AuditLog.objects.filter(
                tenant=self.tenant,
                action="MISSION_CREATE",
                target_type="mission",
                target_id=str(mission_id),
            ).exists()
        )

        start_response = self.client.post(f"/api/v1/missions/{mission_id}/start")
        self.assertEqual(start_response.status_code, 200)
        self.assertEqual(start_response.json()["data"]["status"], MissionStatus.RUNNING)

        pause_response = self.client.post(f"/api/v1/missions/{mission_id}/pause")
        self.assertEqual(pause_response.status_code, 200)
        self.assertEqual(pause_response.json()["data"]["status"], MissionStatus.PAUSED)

        resume_response = self.client.post(f"/api/v1/missions/{mission_id}/resume")
        self.assertEqual(resume_response.status_code, 200)
        self.assertEqual(resume_response.json()["data"]["status"], MissionStatus.RUNNING)

        complete_response = self.client.post(f"/api/v1/missions/{mission_id}/complete")
        self.assertEqual(complete_response.status_code, 200)
        self.assertEqual(complete_response.json()["data"]["status"], MissionStatus.COMPLETED)

        mission = Mission.objects.get(id=mission_id)
        self.assertEqual(mission.status, MissionStatus.COMPLETED)
        for action in ["MISSION_START", "MISSION_PAUSE", "MISSION_RESUME", "MISSION_COMPLETE"]:
            self.assertTrue(
                AuditLog.objects.filter(
                    tenant=self.tenant,
                    action=action,
                    target_type="mission",
                    target_id=str(mission_id),
                ).exists(),
                action,
            )

    def test_business_mission_api_should_require_tenant_context(self):
        mission = self._create_mission(name="缺少租户上下文任务")
        tenantless_client = self.new_client()
        self.authenticate_client(
            tenantless_client,
            username="mission_live_dispatcher",
            password="pass1234",
        )

        list_response = tenantless_client.get("/api/v1/missions")
        self.assertEqual(list_response.status_code, 403)
        self.assertEqual(list_response.json()["code"], "A0403")

        detail_response = tenantless_client.get(f"/api/v1/missions/{mission.id}")
        self.assertEqual(detail_response.status_code, 403)
        self.assertEqual(detail_response.json()["code"], "A0403")

    def test_mission_action_should_reject_body_over_live_http(self):
        mission = self._create_mission(name="动作请求体任务")

        response = self.client.post(
            f"/api/v1/missions/{mission.id}/start",
            {"unexpected": True},
            format="json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["code"], "B0001")
        self.assertIn("body", response.json()["data"])
        mission.refresh_from_db()
        self.assertEqual(mission.status, MissionStatus.PENDING)

    def test_platform_operator_should_be_forbidden_from_business_mission_api(self):
        platform_user = User.objects.create_user(
            username="mission_live_platform_operator",
            password="pass1234",
            status=1,
            is_platform_admin=True,
        )
        ensure_staff_profile(platform_user, name="平台运营用户")
        platform_client = self.new_client()
        self.authenticate_client(
            platform_client,
            username="mission_live_platform_operator",
            password="pass1234",
            tenant_code=self.tenant.code,
        )

        response = platform_client.get("/api/v1/missions")

        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["code"], "A0403")
        self.assertEqual(response.json()["msg"], "无操作权限")

    def test_cross_tenant_missions_should_be_invisible_and_immutable_over_live_http(self):
        foreign_mission = self._create_mission(
            name="其他租户任务",
            tenant=self.other_tenant,
            route=self.other_route,
            drone=self.other_drone,
            pilot=self.other_tenant_pilot_member,
            pilot_name=self.other_tenant_pilot_staff.name,
        )

        list_response = self.client.get("/api/v1/missions")
        self.assertEqual(list_response.status_code, 200)
        self.assertEqual(list_response.json()["code"], "00000")
        self.assertEqual(list_response.json()["data"]["total"], 0)

        retrieve_response = self.client.get(f"/api/v1/missions/{foreign_mission.id}")
        self.assertEqual(retrieve_response.status_code, 404)
        self.assertEqual(retrieve_response.json()["code"], "C0404")

        patch_response = self.client.patch(
            f"/api/v1/missions/{foreign_mission.id}",
            {"remark": "越权修改"},
            format="json",
        )
        self.assertEqual(patch_response.status_code, 404)
        self.assertEqual(patch_response.json()["code"], "C0404")

        start_response = self.client.post(f"/api/v1/missions/{foreign_mission.id}/start")
        self.assertEqual(start_response.status_code, 404)
        self.assertEqual(start_response.json()["code"], "C0404")

        cancel_response = self.client.post(f"/api/v1/missions/{foreign_mission.id}/cancel")
        self.assertEqual(cancel_response.status_code, 404)
        self.assertEqual(cancel_response.json()["code"], "C0404")

    def test_cross_tenant_resource_binding_should_be_rejected_over_live_http(self):
        route_response = self.client.post(
            "/api/v1/missions",
            {
                "name": "跨租户航线绑定任务",
                "route": self.other_route.id,
                "drone": self.drone.id,
                "pilot": self.pilot_member.id,
            },
            format="json",
        )
        self.assertEqual(route_response.status_code, 400)
        self.assertEqual(route_response.json()["code"], "B0001")
        self.assertIn("route", route_response.json()["data"])

        drone_response = self.client.post(
            "/api/v1/missions",
            {
                "name": "跨租户无人机绑定任务",
                "route": self.route.id,
                "drone": self.other_drone.id,
                "pilot": self.pilot_member.id,
            },
            format="json",
        )
        self.assertEqual(drone_response.status_code, 400)
        self.assertEqual(drone_response.json()["code"], "B0001")
        self.assertIn("drone", drone_response.json()["data"])

        pilot_response = self.client.post(
            "/api/v1/missions",
            {
                "name": "跨租户飞手绑定任务",
                "route": self.route.id,
                "drone": self.drone.id,
                "pilot": self.other_tenant_pilot_member.id,
            },
            format="json",
        )
        self.assertEqual(pilot_response.status_code, 400)
        self.assertEqual(pilot_response.json()["code"], "B0001")
        self.assertIn("pilot", pilot_response.json()["data"])

    def test_mission_write_should_reject_unknown_field_over_live_http(self):
        response = self.client.post(
            "/api/v1/missions",
            {
                "name": "未知字段任务",
                "route": self.route.id,
                "drone": self.drone.id,
                "pilot": self.pilot_member.id,
                "status": MissionStatus.RUNNING,
            },
            format="json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["code"], "B0001")
        self.assertIn("status", response.json()["data"])

    def test_cancel_and_fail_should_follow_live_http_contract(self):
        cancel_mission = self._create_mission(name="待取消任务", status=MissionStatus.PENDING)
        cancel_response = self.client.post(f"/api/v1/missions/{cancel_mission.id}/cancel")
        self.assertEqual(cancel_response.status_code, 200)
        self.assertEqual(cancel_response.json()["data"]["status"], MissionStatus.CANCELED)

        conflict_cancel_response = self.client.post(f"/api/v1/missions/{cancel_mission.id}/cancel")
        self.assertEqual(conflict_cancel_response.status_code, 409)
        self.assertEqual(conflict_cancel_response.json()["code"], "C0201")

        fail_mission = self._create_mission(name="待失败任务", status=MissionStatus.RUNNING)
        fail_response = self.client.post(f"/api/v1/missions/{fail_mission.id}/fail")
        self.assertEqual(fail_response.status_code, 200)
        self.assertEqual(fail_response.json()["data"]["status"], MissionStatus.FAILED)

        fail_again_response = self.client.post(f"/api/v1/missions/{fail_mission.id}/fail")
        self.assertEqual(fail_again_response.status_code, 200)
        self.assertEqual(fail_again_response.json()["data"]["status"], MissionStatus.FAILED)

        invalid_fail_mission = self._create_mission(name="待执行不可失败任务", status=MissionStatus.PENDING)
        invalid_fail_response = self.client.post(f"/api/v1/missions/{invalid_fail_mission.id}/fail")
        self.assertEqual(invalid_fail_response.status_code, 409)
        self.assertEqual(invalid_fail_response.json()["code"], "C0201")

        cancel_with_body_response = self.client.post(
            f"/api/v1/missions/{cancel_mission.id}/cancel",
            {"unexpected": True},
            format="json",
        )
        self.assertEqual(cancel_with_body_response.status_code, 400)
        self.assertEqual(cancel_with_body_response.json()["code"], "B0001")
        self.assertIn("body", cancel_with_body_response.json()["data"])

        fail_with_body_response = self.client.post(
            f"/api/v1/missions/{fail_mission.id}/fail",
            {"unexpected": True},
            format="json",
        )
        self.assertEqual(fail_with_body_response.status_code, 400)
        self.assertEqual(fail_with_body_response.json()["code"], "B0001")
        self.assertIn("body", fail_with_body_response.json()["data"])

        for action, mission_id in [("MISSION_CANCEL", cancel_mission.id), ("MISSION_FAIL", fail_mission.id)]:
            self.assertTrue(
                AuditLog.objects.filter(
                    tenant=self.tenant,
                    action=action,
                    target_type="mission",
                    target_id=str(mission_id),
                ).exists(),
                action,
            )

    def test_mission_update_and_transition_conflicts_should_follow_live_http_contract(self):
        pending_mission = self._create_mission(name="待更新任务", status=MissionStatus.PENDING)
        paused_mission = self._create_mission(name="已暂停任务", status=MissionStatus.PAUSED)
        completed_mission = self._create_mission(name="已完成任务", status=MissionStatus.COMPLETED)
        canceled_mission = self._create_mission(name="已取消任务", status=MissionStatus.CANCELED)
        failed_mission = self._create_mission(name="已失败任务", status=MissionStatus.FAILED)

        put_response = self.client.put(
            f"/api/v1/missions/{pending_mission.id}",
            {
                "name": "待更新任务-全量更新后",
                "route": self.route.id,
                "drone": self.drone.id,
                "pilot": self.pilot_member.id,
                "scheduled_at": "2026-03-22T10:00:00+08:00",
                "remark": "全量更新备注",
            },
            format="json",
        )
        self.assertEqual(put_response.status_code, 200)
        self.assertEqual(put_response.json()["code"], "00000")
        self.assertEqual(put_response.json()["data"]["name"], "待更新任务-全量更新后")
        self.assertEqual(put_response.json()["data"]["remark"], "全量更新备注")

        patch_response = self.client.patch(
            f"/api/v1/missions/{pending_mission.id}",
            {"remark": "局部更新备注"},
            format="json",
        )
        self.assertEqual(patch_response.status_code, 200)
        self.assertEqual(patch_response.json()["code"], "00000")
        self.assertEqual(patch_response.json()["data"]["remark"], "局部更新备注")

        paused_start_response = self.client.post(f"/api/v1/missions/{paused_mission.id}/start")
        self.assertEqual(paused_start_response.status_code, 409)
        self.assertEqual(paused_start_response.json()["code"], "C0201")

        running_start_target = self._create_mission(name="已运行待幂等启动任务", status=MissionStatus.RUNNING)
        running_start_response = self.client.post(f"/api/v1/missions/{running_start_target.id}/start")
        self.assertEqual(running_start_response.status_code, 200)
        self.assertEqual(running_start_response.json()["code"], "00000")
        self.assertEqual(running_start_response.json()["data"]["status"], MissionStatus.RUNNING)

        paused_again_response = self.client.post(f"/api/v1/missions/{paused_mission.id}/pause")
        self.assertEqual(paused_again_response.status_code, 200)
        self.assertEqual(paused_again_response.json()["code"], "00000")
        self.assertEqual(paused_again_response.json()["data"]["status"], MissionStatus.PAUSED)

        pending_pause_response = self.client.post(f"/api/v1/missions/{pending_mission.id}/pause")
        self.assertEqual(pending_pause_response.status_code, 409)
        self.assertEqual(pending_pause_response.json()["code"], "C0201")

        pending_resume_response = self.client.post(f"/api/v1/missions/{pending_mission.id}/resume")
        self.assertEqual(pending_resume_response.status_code, 409)
        self.assertEqual(pending_resume_response.json()["code"], "C0201")

        running_resume_target = self._create_mission(name="已运行任务", status=MissionStatus.RUNNING)
        running_resume_response = self.client.post(f"/api/v1/missions/{running_resume_target.id}/resume")
        self.assertEqual(running_resume_response.status_code, 200)
        self.assertEqual(running_resume_response.json()["code"], "00000")
        self.assertEqual(running_resume_response.json()["data"]["status"], MissionStatus.RUNNING)

        paused_complete_response = self.client.post(f"/api/v1/missions/{paused_mission.id}/complete")
        self.assertEqual(paused_complete_response.status_code, 409)
        self.assertEqual(paused_complete_response.json()["code"], "C0201")

        completed_again_response = self.client.post(f"/api/v1/missions/{completed_mission.id}/complete")
        self.assertEqual(completed_again_response.status_code, 200)
        self.assertEqual(completed_again_response.json()["code"], "00000")
        self.assertEqual(completed_again_response.json()["data"]["status"], MissionStatus.COMPLETED)

        canceled_start_response = self.client.post(f"/api/v1/missions/{canceled_mission.id}/start")
        self.assertEqual(canceled_start_response.status_code, 409)
        self.assertEqual(canceled_start_response.json()["code"], "C0201")

        failed_resume_response = self.client.post(f"/api/v1/missions/{failed_mission.id}/resume")
        self.assertEqual(failed_resume_response.status_code, 409)
        self.assertEqual(failed_resume_response.json()["code"], "C0201")

    def test_mission_write_should_reject_invalid_resource_states_over_live_http(self):
        disabled_route = Route.objects.create(tenant=self.tenant, name="禁用航线", status=RouteStatus.DISABLED)
        maintenance_drone = Drone.objects.create(
            tenant=self.tenant,
            code="MISSION-LIVE-MAINT-001",
            name="维护中任务无人机",
            model="M350",
            serial_no="MISSION-LIVE-MAINT-SN-001",
            status=DroneStatus.MAINTENANCE,
        )

        route_response = self.client.post(
            "/api/v1/missions",
            {
                "name": "绑定禁用航线任务",
                "route": disabled_route.id,
                "drone": self.drone.id,
                "pilot": self.pilot_member.id,
            },
            format="json",
        )
        self.assertEqual(route_response.status_code, 400)
        self.assertEqual(route_response.json()["code"], "B0001")
        self.assertIn("route", route_response.json()["data"])

        drone_response = self.client.post(
            "/api/v1/missions",
            {
                "name": "绑定维护无人机任务",
                "route": self.route.id,
                "drone": maintenance_drone.id,
                "pilot": self.pilot_member.id,
            },
            format="json",
        )
        self.assertEqual(drone_response.status_code, 400)
        self.assertEqual(drone_response.json()["code"], "B0001")
        self.assertIn("drone", drone_response.json()["data"])

    def test_mission_write_should_reject_invalid_pilot_states_over_live_http(self):
        inactive_user = User.objects.create_user(username="mission_live_inactive_member", password="pass1234", status=1)
        ensure_staff_profile(
            inactive_user,
            staff_no="ML-I-001",
            name="禁用飞手成员",
            employment_status=EmploymentStatus.ACTIVE,
        )
        _tenant, inactive_member, _inactive_role = ensure_tenant_role_binding(
            inactive_user,
            tenant=self.tenant,
            role_code="pilot_operator",
            role_name="飞手",
            member_no="ML-I-001",
        )
        ensure_tenant_member_position(inactive_member, code="pilot_operator", name="飞手")
        inactive_member.status = TenantMemberStatus.DISABLED
        inactive_member.save(update_fields=["status", "responded_at", "joined_at", "updated_at"])

        inactive_response = self.client.post(
            "/api/v1/missions",
            {
                "name": "绑定禁用成员任务",
                "route": self.route.id,
                "drone": self.drone.id,
                "pilot": inactive_member.id,
            },
            format="json",
        )
        self.assertEqual(inactive_response.status_code, 400)
        self.assertEqual(inactive_response.json()["code"], "B0001")
        self.assertIn("pilot", inactive_response.json()["data"])

        no_staff_user = User.objects.create_user(username="mission_live_no_staff", password="pass1234", status=1)
        _tenant, no_staff_member, _no_staff_role = ensure_tenant_role_binding(
            no_staff_user,
            tenant=self.tenant,
            role_code="pilot_operator",
            role_name="飞手",
            member_no="ML-NS-001",
        )
        ensure_tenant_member_position(no_staff_member, code="pilot_operator", name="飞手")
        no_staff_user.staff_profile.delete()
        no_staff_response = self.client.post(
            "/api/v1/missions",
            {
                "name": "绑定缺少档案成员任务",
                "route": self.route.id,
                "drone": self.drone.id,
                "pilot": no_staff_member.id,
            },
            format="json",
        )
        self.assertEqual(no_staff_response.status_code, 400)
        self.assertEqual(no_staff_response.json()["code"], "B0001")
        self.assertIn("pilot", no_staff_response.json()["data"])

        inactive_staff_user = User.objects.create_user(username="mission_live_inactive_staff", password="pass1234", status=1)
        ensure_staff_profile(
            inactive_staff_user,
            staff_no="ML-IS-001",
            name="离职飞手",
            employment_status=EmploymentStatus.INACTIVE,
        )
        _tenant, inactive_staff_member, _inactive_staff_role = ensure_tenant_role_binding(
            inactive_staff_user,
            tenant=self.tenant,
            role_code="pilot_operator",
            role_name="飞手",
            member_no="ML-IS-001",
        )
        ensure_tenant_member_position(inactive_staff_member, code="pilot_operator", name="飞手")
        inactive_staff_response = self.client.post(
            "/api/v1/missions",
            {
                "name": "绑定离职飞手任务",
                "route": self.route.id,
                "drone": self.drone.id,
                "pilot": inactive_staff_member.id,
            },
            format="json",
        )
        self.assertEqual(inactive_staff_response.status_code, 400)
        self.assertEqual(inactive_staff_response.json()["code"], "B0001")
        self.assertIn("pilot", inactive_staff_response.json()["data"])

        non_pilot_user = User.objects.create_user(username="mission_live_non_pilot", password="pass1234", status=1)
        ensure_staff_profile(
            non_pilot_user,
            staff_no="ML-NP-001",
            name="普通成员",
            employment_status=EmploymentStatus.ACTIVE,
        )
        _tenant, non_pilot_member, non_pilot_role = ensure_tenant_role_binding(
            non_pilot_user,
            tenant=self.tenant,
            role_code="mission_live_viewer_role",
            role_name="普通成员",
            member_no="ML-NP-001",
        )
        non_pilot_binding = non_pilot_member.role_bindings.get(system_role=non_pilot_role)
        non_pilot_binding.status = 0
        non_pilot_binding.save(update_fields=["status", "updated_at"])
        non_pilot_response = self.client.post(
            "/api/v1/missions",
            {
                "name": "绑定非飞手成员任务",
                "route": self.route.id,
                "drone": self.drone.id,
                "pilot": non_pilot_member.id,
            },
            format="json",
        )
        self.assertEqual(non_pilot_response.status_code, 400)
        self.assertEqual(non_pilot_response.json()["code"], "B0001")
        self.assertIn("pilot", non_pilot_response.json()["data"])


class LiveMissionPilotScopeTests(LiveMissionApiTestCase):
    def setUp(self):
        super().setUp()
        pilot_role = self.pilot_member.role_bindings.get(system_role__code="pilot_operator").system_role
        grant_role_permissions(
            pilot_role,
            {
                "mission.view_mission": ScopeType.ASSIGNED,
                "mission.manage_mission": ScopeType.ASSIGNED,
            },
            group_name="mission-live-pilot-group",
        )
        self.my_mission = self._create_mission(name="我的实时任务", pilot=self.pilot_member)
        self.other_mission = self._create_mission(name="别人的实时任务", pilot=self.other_pilot_member)
        self.pilot_client = self.new_client()
        self.authenticate_client(
            self.pilot_client,
            username="mission_live_pilot",
            password="pass1234",
            tenant_code=self.tenant.code,
        )

    def test_assigned_scope_pilot_should_only_list_and_retrieve_own_missions(self):
        list_response = self.pilot_client.get("/api/v1/missions")

        self.assertEqual(list_response.status_code, 200)
        self.assertEqual(list_response.json()["data"]["total"], 1)
        self.assertEqual(list_response.json()["data"]["list"][0]["id"], self.my_mission.id)

        own_detail_response = self.pilot_client.get(f"/api/v1/missions/{self.my_mission.id}")
        self.assertEqual(own_detail_response.status_code, 200)
        self.assertEqual(own_detail_response.json()["data"]["id"], self.my_mission.id)

        other_detail_response = self.pilot_client.get(f"/api/v1/missions/{self.other_mission.id}")
        self.assertEqual(other_detail_response.status_code, 404)
        self.assertEqual(other_detail_response.json()["code"], "C0404")

    def test_assigned_scope_pilot_should_not_operate_other_missions(self):
        other_patch_response = self.pilot_client.patch(
            f"/api/v1/missions/{self.other_mission.id}",
            {"remark": "越权修改任务"},
            format="json",
        )
        self.assertEqual(other_patch_response.status_code, 404)
        self.assertEqual(other_patch_response.json()["code"], "C0404")

        other_start_response = self.pilot_client.post(f"/api/v1/missions/{self.other_mission.id}/start")
        self.assertEqual(other_start_response.status_code, 404)
        self.assertEqual(other_start_response.json()["code"], "C0404")

        other_cancel_response = self.pilot_client.post(f"/api/v1/missions/{self.other_mission.id}/cancel")
        self.assertEqual(other_cancel_response.status_code, 404)
        self.assertEqual(other_cancel_response.json()["code"], "C0404")

    def test_assigned_scope_pilot_should_operate_own_missions(self):
        own_cancel_target = self._create_mission(name="我的待取消任务", pilot=self.pilot_member, status=MissionStatus.PENDING)
        own_fail_target = self._create_mission(name="我的待失败任务", pilot=self.pilot_member, status=MissionStatus.RUNNING)

        patch_response = self.pilot_client.patch(
            f"/api/v1/missions/{self.my_mission.id}",
            {"remark": "飞手更新自己的任务"},
            format="json",
        )
        self.assertEqual(patch_response.status_code, 200)
        self.assertEqual(patch_response.json()["code"], "00000")
        self.assertEqual(patch_response.json()["data"]["remark"], "飞手更新自己的任务")

        start_response = self.pilot_client.post(f"/api/v1/missions/{self.my_mission.id}/start")
        self.assertEqual(start_response.status_code, 200)
        self.assertEqual(start_response.json()["code"], "00000")
        self.assertEqual(start_response.json()["data"]["status"], MissionStatus.RUNNING)

        pause_response = self.pilot_client.post(f"/api/v1/missions/{self.my_mission.id}/pause")
        self.assertEqual(pause_response.status_code, 200)
        self.assertEqual(pause_response.json()["code"], "00000")
        self.assertEqual(pause_response.json()["data"]["status"], MissionStatus.PAUSED)

        resume_response = self.pilot_client.post(f"/api/v1/missions/{self.my_mission.id}/resume")
        self.assertEqual(resume_response.status_code, 200)
        self.assertEqual(resume_response.json()["code"], "00000")
        self.assertEqual(resume_response.json()["data"]["status"], MissionStatus.RUNNING)

        complete_response = self.pilot_client.post(f"/api/v1/missions/{self.my_mission.id}/complete")
        self.assertEqual(complete_response.status_code, 200)
        self.assertEqual(complete_response.json()["code"], "00000")
        self.assertEqual(complete_response.json()["data"]["status"], MissionStatus.COMPLETED)

        cancel_response = self.pilot_client.post(f"/api/v1/missions/{own_cancel_target.id}/cancel")
        self.assertEqual(cancel_response.status_code, 200)
        self.assertEqual(cancel_response.json()["code"], "00000")
        self.assertEqual(cancel_response.json()["data"]["status"], MissionStatus.CANCELED)

        fail_response = self.pilot_client.post(f"/api/v1/missions/{own_fail_target.id}/fail")
        self.assertEqual(fail_response.status_code, 200)
        self.assertEqual(fail_response.json()["code"], "00000")
        self.assertEqual(fail_response.json()["data"]["status"], MissionStatus.FAILED)
