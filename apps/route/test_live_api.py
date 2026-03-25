"""Route live HTTP test events.

- 航线通过真实 HTTP 完成创建/查询/更新/启停用/删除闭环
- 被任务引用的航线删除时退化为禁用
- PATCH 空 body 与 DELETE 带 body 按合同拒绝
- business route API 强制要求有效 Bearer + X-TENANT-CODE
- platform_admin 即使权限矩阵放开也禁止访问租户业务接口
"""

from apps.access.models import AuditLog, DirectoryStatus, EmploymentStatus, Role, ScopeType
from apps.access.test_live_base import LiveIamApiTestCase, User
from apps.access.test_support import (
    ensure_staff_profile,
    ensure_tenant_member_position,
    ensure_tenant_role_binding,
    grant_role_permissions,
)
from apps.drone.models import Drone, DroneStatus
from apps.mission.models import Mission, MissionStatus
from apps.route.models import Route, RouteStatus, RouteType
from apps.waypoint.models import Waypoint


class LiveRouteApiTestCase(LiveIamApiTestCase):
    def setUp(self):
        super().setUp()
        self.user = User.objects.create_user(username="route_live_admin", password="pass1234", status=1)
        self.pilot_user = User.objects.create_user(username="route_live_pilot", password="pass1234", status=1)
        self.staff = ensure_staff_profile(
            self.user,
            staff_no="RL-001",
            name="实时航线管理员",
            employment_status=EmploymentStatus.ACTIVE,
        )
        self.pilot_staff = ensure_staff_profile(
            self.pilot_user,
            staff_no="RL-P-001",
            name="实时飞手A",
            employment_status=EmploymentStatus.ACTIVE,
        )
        self.tenant, self.member, self.role = ensure_tenant_role_binding(
            self.user,
            tenant_code="route_live_tenant",
            role_code="route_live_role",
            role_name="实时航线角色",
        )
        self.other_user = User.objects.create_user(username="route_live_other_admin", password="pass1234", status=1)
        self.other_staff = ensure_staff_profile(
            self.other_user,
            staff_no="RL-OTHER-001",
            name="其他租户航线管理员",
            employment_status=EmploymentStatus.ACTIVE,
        )
        self.other_tenant, self.other_member, self.other_role = ensure_tenant_role_binding(
            self.other_user,
            tenant_code="route_live_other_tenant",
            role_code="route_live_other_role",
            role_name="其他租户实时航线角色",
        )
        grant_role_permissions(
            self.other_role,
            {
                "route.view_route": ScopeType.ALL,
                "route.manage_route": ScopeType.ALL,
            },
            group_name="route-live-other-group",
        )
        _pilot_tenant, self.pilot_member, _pilot_role = ensure_tenant_role_binding(
            self.pilot_user,
            tenant=self.tenant,
            role_code="pilot_operator",
            role_name="飞手",
        )
        ensure_tenant_member_position(self.pilot_member, code="pilot_operator", name="飞手")
        grant_role_permissions(
            self.role,
            {
                "route.view_route": ScopeType.ALL,
                "route.manage_route": ScopeType.ALL,
            },
            group_name="route-live-group",
        )
        self.drone = Drone.objects.create(
            tenant=self.tenant,
            code="ROUTE-LIVE-DRN-001",
            name="实时航线测试机",
            model="M300",
            serial_no="ROUTE-LIVE-SN-001",
            status=DroneStatus.ENABLED,
        )
        self.login(username="route_live_admin", password="pass1234", tenant_code=self.tenant.code)

    def _create_route(
        self,
        *,
        name: str,
        status: int = RouteStatus.ACTIVE,
        route_type: int = RouteType.PENDING_EXTENSION,
        tenant=None,
        creator_name: str | None = None,
    ) -> Route:
        tenant = tenant or self.tenant
        if creator_name is None:
            creator_name = self.staff.name if tenant == self.tenant else self.other_staff.name
        return Route.objects.create(
            tenant=tenant,
            name=name,
            route_type=route_type,
            status=status,
            creator_name=creator_name,
        )

    def _create_waypoint(self, route: Route, *, sequence: int) -> Waypoint:
        return Waypoint.objects.create(
            route=route,
            sequence=sequence,
            latitude="22.54321012",
            longitude="113.98765432",
            altitude="120.50",
        )

    def _create_mission(self, route: Route, *, name: str = "引用航线任务", status: int = MissionStatus.PENDING) -> Mission:
        return Mission.objects.create(
            tenant=self.tenant,
            name=name,
            route=route,
            route_name=route.name,
            drone=self.drone,
            drone_name=self.drone.name,
            pilot=self.pilot_member,
            pilot_name=self.pilot_staff.name,
            status=status,
        )


class LiveRouteApiTests(LiveRouteApiTestCase):
    def test_route_lifecycle_should_follow_live_http_contract(self):
        create_response = self.client.post(
            "/api/v1/routes",
            {
                "name": "城市中心巡检航线",
                "route_type": RouteType.PENDING_EXTENSION,
                "drone_type_id": 1,
                "total_distance": "2063.50",
                "estimated_duration": 1200,
            },
            format="json",
        )
        self.assertEqual(create_response.status_code, 201)
        self.assertEqual(create_response.json()["code"], "00000")
        create_data = create_response.json()["data"]
        self.assertEqual(create_data["name"], "城市中心巡检航线")
        self.assertEqual(create_data["route_type"], RouteType.PENDING_EXTENSION)
        self.assertEqual(create_data["status"], RouteStatus.ACTIVE)
        self.assertEqual(create_data["creator_name"], self.staff.name)
        route_id = create_data["id"]

        route = Route.objects.get(id=route_id)
        self._create_waypoint(route, sequence=1)
        self._create_waypoint(route, sequence=2)

        list_response = self.client.get("/api/v1/routes", {"name": "城市中心"})
        self.assertEqual(list_response.status_code, 200)
        self.assertEqual(list_response.json()["data"]["total"], 1)
        self.assertEqual(list_response.json()["data"]["list"][0]["id"], route_id)

        retrieve_response = self.client.get(f"/api/v1/routes/{route_id}")
        self.assertEqual(retrieve_response.status_code, 200)
        self.assertEqual(retrieve_response.json()["data"]["name"], "城市中心巡检航线")

        patch_response = self.client.patch(
            f"/api/v1/routes/{route_id}",
            {
                "name": "城市中心巡检航线-修订版",
                "estimated_duration": 1500,
                "total_distance": "3560.80",
            },
            format="json",
        )
        self.assertEqual(patch_response.status_code, 200)
        self.assertEqual(patch_response.json()["data"]["name"], "城市中心巡检航线-修订版")
        self.assertEqual(patch_response.json()["data"]["estimated_duration"], 1500)
        self.assertEqual(patch_response.json()["data"]["total_distance"], "3560.80")

        disable_response = self.client.post(f"/api/v1/routes/{route_id}/disable")
        self.assertEqual(disable_response.status_code, 200)
        self.assertEqual(disable_response.json()["data"]["status"], RouteStatus.DISABLED)

        enable_response = self.client.post(f"/api/v1/routes/{route_id}/enable")
        self.assertEqual(enable_response.status_code, 200)
        self.assertEqual(enable_response.json()["data"]["status"], RouteStatus.ACTIVE)

        delete_response = self.client.delete(f"/api/v1/routes/{route_id}")
        self.assertEqual(delete_response.status_code, 200)
        self.assertEqual(delete_response.json()["code"], "00000")
        self.assertEqual(delete_response.json()["data"]["delete_mode"], "hard")
        self.assertEqual(delete_response.json()["data"]["deleted_waypoint_count"], 2)
        self.assertFalse(Route.objects.filter(id=route_id).exists())
        self.assertFalse(Waypoint.objects.filter(route_id=route_id).exists())

        for action in ["ROUTE_CREATE", "ROUTE_UPDATE", "ROUTE_DISABLE", "ROUTE_ENABLE", "ROUTE_DELETE"]:
            self.assertTrue(
                AuditLog.objects.filter(
                    tenant=self.tenant,
                    action=action,
                    target_type="route",
                    target_id=str(route_id),
                ).exists(),
                action,
            )

    def test_delete_route_with_mission_should_disable_instead_of_hard_delete_over_live_http(self):
        route = self._create_route(name="被任务引用航线", status=RouteStatus.ACTIVE)
        self._create_mission(route)

        response = self.client.delete(f"/api/v1/routes/{route.id}")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["code"], "00000")
        self.assertEqual(response.json()["data"]["delete_mode"], "disabled")
        self.assertEqual(response.json()["data"]["status"], RouteStatus.DISABLED)
        route.refresh_from_db()
        self.assertEqual(route.status, RouteStatus.DISABLED)
        self.assertTrue(Route.objects.filter(id=route.id).exists())

    def test_route_mutations_should_reject_empty_or_unsupported_body_over_live_http(self):
        route = self._create_route(name="请求体校验航线")

        patch_response = self.client.patch(
            f"/api/v1/routes/{route.id}",
            {},
            format="json",
        )
        self.assertEqual(patch_response.status_code, 400)
        self.assertEqual(patch_response.json()["code"], "B0001")
        self.assertIn("body", patch_response.json()["data"])

        delete_response = self.client.delete(
            f"/api/v1/routes/{route.id}",
            {"unexpected": True},
            format="json",
        )
        self.assertEqual(delete_response.status_code, 400)
        self.assertEqual(delete_response.json()["code"], "B0001")
        self.assertIn("body", delete_response.json()["data"])
        self.assertTrue(Route.objects.filter(id=route.id).exists())

    def test_business_route_api_should_require_tenant_context(self):
        tenantless_client = self.new_client()
        self.authenticate_client(
            tenantless_client,
            username="route_live_admin",
            password="pass1234",
        )

        list_response = tenantless_client.get("/api/v1/routes")
        self.assertEqual(list_response.status_code, 403)
        self.assertEqual(list_response.json()["code"], "A0403")

        create_response = tenantless_client.post(
            "/api/v1/routes",
            {"name": "缺少租户上下文航线", "route_type": RouteType.PENDING_EXTENSION},
            format="json",
        )
        self.assertEqual(create_response.status_code, 403)
        self.assertEqual(create_response.json()["code"], "A0403")

    def test_platform_admin_should_be_blocked_from_business_route_api_even_with_permission(self):
        platform_role, _ = Role.objects.update_or_create(
            code="platform_admin",
            defaults={"name": "平台管理员", "status": DirectoryStatus.ACTIVE},
        )
        grant_role_permissions(
            platform_role,
            {"route.view_route": ScopeType.ALL},
            group_name="route-live-platform-group",
        )
        platform_user = User.objects.create_user(
            username="route_live_platform_admin",
            password="pass1234",
            status=1,
            is_platform_admin=True,
        )
        ensure_staff_profile(platform_user, name="平台航线管理员")
        platform_client = self.new_client()
        self.authenticate_client(
            platform_client,
            username="route_live_platform_admin",
            password="pass1234",
            tenant_code=self.tenant.code,
        )

        response = platform_client.get("/api/v1/routes")

        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["code"], "A0403")
        self.assertEqual(response.json()["msg"], "平台管理员不可访问租户业务接口")

    def test_cross_tenant_routes_should_be_invisible_and_immutable_over_live_http(self):
        foreign_route = self._create_route(name="其他租户航线", tenant=self.other_tenant)

        list_response = self.client.get("/api/v1/routes")
        self.assertEqual(list_response.status_code, 200)
        self.assertEqual(list_response.json()["code"], "00000")
        self.assertEqual(list_response.json()["data"]["total"], 0)

        retrieve_response = self.client.get(f"/api/v1/routes/{foreign_route.id}")
        self.assertEqual(retrieve_response.status_code, 404)
        self.assertEqual(retrieve_response.json()["code"], "C0404")

        patch_response = self.client.patch(
            f"/api/v1/routes/{foreign_route.id}",
            {"name": "越权更新航线"},
            format="json",
        )
        self.assertEqual(patch_response.status_code, 404)
        self.assertEqual(patch_response.json()["code"], "C0404")

        enable_response = self.client.post(f"/api/v1/routes/{foreign_route.id}/enable")
        self.assertEqual(enable_response.status_code, 404)
        self.assertEqual(enable_response.json()["code"], "C0404")

        disable_response = self.client.post(f"/api/v1/routes/{foreign_route.id}/disable")
        self.assertEqual(disable_response.status_code, 404)
        self.assertEqual(disable_response.json()["code"], "C0404")

        delete_response = self.client.delete(f"/api/v1/routes/{foreign_route.id}")
        self.assertEqual(delete_response.status_code, 404)
        self.assertEqual(delete_response.json()["code"], "C0404")
        self.assertTrue(Route.objects.filter(id=foreign_route.id).exists())

    def test_route_actions_should_reject_body_over_live_http(self):
        route = self._create_route(name="动作请求体验证航线", status=RouteStatus.DISABLED)

        enable_response = self.client.post(
            f"/api/v1/routes/{route.id}/enable",
            {"unexpected": True},
            format="json",
        )
        self.assertEqual(enable_response.status_code, 400)
        self.assertEqual(enable_response.json()["code"], "B0001")
        self.assertIn("body", enable_response.json()["data"])

        route.status = RouteStatus.ACTIVE
        route.save(update_fields=["status", "updated_at"])
        disable_response = self.client.post(
            f"/api/v1/routes/{route.id}/disable",
            {"unexpected": True},
            format="json",
        )
        self.assertEqual(disable_response.status_code, 400)
        self.assertEqual(disable_response.json()["code"], "B0001")
        self.assertIn("body", disable_response.json()["data"])

    def test_route_create_should_reject_unknown_field_over_live_http(self):
        response = self.client.post(
            "/api/v1/routes",
            {
                "name": "未知字段航线",
                "route_type": RouteType.PENDING_EXTENSION,
                "status": RouteStatus.ACTIVE,
            },
            format="json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["code"], "B0001")
        self.assertIn("status", response.json()["data"])

    def test_route_put_and_idempotent_actions_should_follow_live_http_contract(self):
        route = self._create_route(name="待全量更新航线", status=RouteStatus.ACTIVE)

        put_response = self.client.put(
            f"/api/v1/routes/{route.id}",
            {
                "name": "待全量更新航线-更新后",
                "route_type": RouteType.PENDING_EXTENSION,
                "drone_type_id": 99,
                "total_distance": "4321.50",
                "estimated_duration": 1888,
            },
            format="json",
        )
        self.assertEqual(put_response.status_code, 200)
        self.assertEqual(put_response.json()["code"], "00000")
        self.assertEqual(put_response.json()["data"]["name"], "待全量更新航线-更新后")
        self.assertEqual(put_response.json()["data"]["drone_type_id"], 99)
        self.assertEqual(put_response.json()["data"]["total_distance"], "4321.50")
        self.assertEqual(put_response.json()["data"]["estimated_duration"], 1888)

        enable_idempotent_response = self.client.post(f"/api/v1/routes/{route.id}/enable")
        self.assertEqual(enable_idempotent_response.status_code, 200)
        self.assertEqual(enable_idempotent_response.json()["code"], "00000")
        self.assertEqual(enable_idempotent_response.json()["data"]["status"], RouteStatus.ACTIVE)

        disable_response = self.client.post(f"/api/v1/routes/{route.id}/disable")
        self.assertEqual(disable_response.status_code, 200)
        self.assertEqual(disable_response.json()["data"]["status"], RouteStatus.DISABLED)

        disable_idempotent_response = self.client.post(f"/api/v1/routes/{route.id}/disable")
        self.assertEqual(disable_idempotent_response.status_code, 200)
        self.assertEqual(disable_idempotent_response.json()["code"], "00000")
        self.assertEqual(disable_idempotent_response.json()["data"]["status"], RouteStatus.DISABLED)

        enable_response = self.client.post(f"/api/v1/routes/{route.id}/enable")
        self.assertEqual(enable_response.status_code, 200)
        self.assertEqual(enable_response.json()["data"]["status"], RouteStatus.ACTIVE)

        route.refresh_from_db()
        self.assertEqual(route.status, RouteStatus.ACTIVE)
