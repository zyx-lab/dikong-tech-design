from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group, Permission
from django.test import TestCase
from rest_framework.test import APIClient

from apps.access.models import (
    AuditLog,
    EmploymentStatus,
    GroupPermissionScope,
    ScopeStatus,
    ScopeType,
    StaffProfile,
    StaffType,
    StaffTypeGroup,
)
from apps.drone.models import Drone, DroneStatus
from apps.mission.models import Mission, MissionStatus
from apps.route.models import Route, RouteStatus, RouteType
from apps.waypoint.models import Waypoint

User = get_user_model()


class RouteApiTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.staff_type = StaffType.objects.create(code="route_admin_test", name="航线管理员", status=1)
        self.pilot_staff_type = StaffType.objects.create(code="route_pilot_test", name="飞手", status=1)
        self.user = User.objects.create_user(username="route_admin", password="pass1234", status=1)
        self.pilot_user = User.objects.create_user(username="route_pilot", password="pass1234", status=1)
        self.staff = StaffProfile.objects.create(
            user=self.user,
            staff_no="R-001",
            name="航线管理员A",
            employment_status=1,
            staff_type=self.staff_type,
        )
        self.pilot_staff = StaffProfile.objects.create(
            user=self.pilot_user,
            staff_no="P-001",
            name="飞手A",
            employment_status=EmploymentStatus.ACTIVE,
            staff_type=self.pilot_staff_type,
        )
        self.drone = Drone.objects.create(
            code="ROUTE-DRN-001",
            name="航线测试机",
            model="M300",
            serial_no="ROUTE-SN-001",
            status=DroneStatus.ENABLED,
        )

    def _grant_permission(self, permission_code: str, with_scope: bool = True):
        app_label, codename = permission_code.split(".", 1)
        perm = Permission.objects.get(content_type__app_label=app_label, codename=codename)
        group = Group.objects.create(name=f"{permission_code}-group")
        group.permissions.add(perm)
        StaffTypeGroup.objects.create(staff_type=self.staff_type, group=group, status=ScopeStatus.ACTIVE)
        if with_scope:
            GroupPermissionScope.objects.create(
                group=group,
                permission=perm,
                scope_type=ScopeType.ALL,
                status=ScopeStatus.ACTIVE,
            )

    def _create_route(self, *, name: str, status: int = RouteStatus.ACTIVE, route_type: int = RouteType.PENDING_EXTENSION) -> Route:
        return Route.objects.create(
            name=name,
            route_type=route_type,
            status=status,
            creator_name=self.staff.name,
        )

    def _create_waypoint(self, route: Route, *, sequence: int = 1) -> Waypoint:
        return Waypoint.objects.create(
            route=route,
            sequence=sequence,
            latitude="22.54321012",
            longitude="113.98765432",
            altitude="120.50",
        )

    def _create_mission(self, route: Route, *, name: str = "引用航线任务", status: int = MissionStatus.PENDING) -> Mission:
        return Mission.objects.create(
            name=name,
            route=route,
            route_name=route.name,
            drone=self.drone,
            drone_name=self.drone.name,
            pilot=self.pilot_staff,
            pilot_name=self.pilot_staff.name,
            status=status,
        )

    def test_create_route_should_return_success(self):
        self._grant_permission("route.manage_route")
        self.client.force_authenticate(self.user)

        response = self.client.post(
            "/api/v1/routes",
            {
                "name": "城市中心巡检航线",
                "route_type": RouteType.PENDING_EXTENSION,
                "drone_type_id": 1,
                "total_distance": "2063.50",
                "estimated_duration": 1200,
                "waypoint_count": 12,
            },
            format="json",
        )
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data["business_code"], "SUCCESS")
        self.assertEqual(response.data["business_detail_code"], "OK")
        self.assertEqual(response.data["name"], "城市中心巡检航线")
        self.assertEqual(response.data["route_type"], RouteType.PENDING_EXTENSION)
        self.assertEqual(response.data["status"], RouteStatus.ACTIVE)
        self.assertEqual(response.data["creator_name"], self.staff.name)

        route = Route.objects.get(id=response.data["id"])
        self.assertEqual(route.creator_name, self.staff.name)
        self.assertTrue(
            AuditLog.objects.filter(
                action="ROUTE_CREATE",
                target_type="route",
                target_id=str(route.id),
            ).exists()
        )

    def test_create_route_same_name_should_still_be_allowed(self):
        self._grant_permission("route.manage_route")
        self.client.force_authenticate(self.user)
        payload = {
            "name": "重复名称航线",
            "route_type": RouteType.PENDING_EXTENSION,
            "waypoint_count": 8,
        }

        first = self.client.post("/api/v1/routes", payload, format="json")
        self.assertEqual(first.status_code, 201)

        second = self.client.post("/api/v1/routes", payload, format="json")
        self.assertEqual(second.status_code, 201)
        self.assertEqual(second.data["business_code"], "SUCCESS")
        self.assertEqual(second.data["business_detail_code"], "OK")

    def test_create_route_invalid_params_should_return_invalid_params(self):
        self._grant_permission("route.manage_route")
        self.client.force_authenticate(self.user)
        response = self.client.post(
            "/api/v1/routes",
            {
                "route_type": RouteType.PENDING_EXTENSION,
            },
            format="json",
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data["business_code"], "INVALID_PARAMS")
        self.assertEqual(response.data["business_detail_code"], "VALIDATION_ERROR")
        self.assertIn("name", response.data)

    def test_create_route_without_auth_should_return_permission_denied(self):
        response = self.client.post(
            "/api/v1/routes",
            {
                "name": "未认证航线",
                "route_type": RouteType.PENDING_EXTENSION,
            },
            format="json",
        )
        self.assertIn(response.status_code, (401, 403))
        self.assertEqual(response.data["business_code"], "PERMISSION_DENIED")
        self.assertIn(response.data["business_detail_code"], {"NOT_AUTHENTICATED", "FORBIDDEN"})

    def test_create_route_without_permission_should_return_permission_denied(self):
        self.client.force_authenticate(self.user)
        response = self.client.post(
            "/api/v1/routes",
            {
                "name": "无权限航线",
                "route_type": RouteType.PENDING_EXTENSION,
            },
            format="json",
        )
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.data["business_code"], "PERMISSION_DENIED")
        self.assertIn(response.data["business_detail_code"], {"FORBIDDEN", "PERMISSION_DENIED"})

    def test_list_routes_should_return_success(self):
        self._grant_permission("route.view_route")
        self.client.force_authenticate(self.user)
        self._create_route(name="珠海岸线巡查航线")
        self._create_route(name="前山河巡检航线")

        response = self.client.get("/api/v1/routes")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["business_code"], "SUCCESS")
        self.assertEqual(response.data["business_detail_code"], "OK")
        self.assertIn("results", response.data)
        self.assertGreaterEqual(len(response.data["results"]), 2)

    def test_list_routes_with_name_filter_should_return_filtered_results(self):
        self._grant_permission("route.view_route")
        self.client.force_authenticate(self.user)
        self._create_route(name="城市主干道巡检航线")
        self._create_route(name="海岸线巡查航线")

        response = self.client.get("/api/v1/routes", {"name": "海岸线"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["business_code"], "SUCCESS")
        self.assertEqual(response.data["business_detail_code"], "OK")
        self.assertEqual(len(response.data["results"]), 1)
        self.assertEqual(response.data["results"][0]["name"], "海岸线巡查航线")

    def test_list_routes_without_auth_should_return_permission_denied(self):
        response = self.client.get("/api/v1/routes")
        self.assertIn(response.status_code, (401, 403))
        self.assertEqual(response.data["business_code"], "PERMISSION_DENIED")
        self.assertIn(response.data["business_detail_code"], {"NOT_AUTHENTICATED", "FORBIDDEN"})

    def test_list_routes_without_permission_should_return_permission_denied(self):
        self.client.force_authenticate(self.user)
        response = self.client.get("/api/v1/routes")
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.data["business_code"], "PERMISSION_DENIED")
        self.assertIn(response.data["business_detail_code"], {"FORBIDDEN", "PERMISSION_DENIED"})

    def test_retrieve_route_should_return_success(self):
        self._grant_permission("route.view_route")
        self.client.force_authenticate(self.user)
        route = self._create_route(name="详情航线A")

        response = self.client.get(f"/api/v1/routes/{route.id}")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["business_code"], "SUCCESS")
        self.assertEqual(response.data["business_detail_code"], "OK")
        self.assertEqual(response.data["id"], route.id)
        self.assertEqual(response.data["name"], "详情航线A")

    def test_retrieve_route_not_found_should_return_resource_not_found(self):
        self._grant_permission("route.view_route")
        self.client.force_authenticate(self.user)

        response = self.client.get("/api/v1/routes/999999")
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.data["business_code"], "RESOURCE_NOT_FOUND")
        self.assertEqual(response.data["business_detail_code"], "NOT_FOUND")

    def test_retrieve_route_without_auth_should_return_permission_denied(self):
        route = self._create_route(name="详情航线未认证")

        response = self.client.get(f"/api/v1/routes/{route.id}")
        self.assertIn(response.status_code, (401, 403))
        self.assertEqual(response.data["business_code"], "PERMISSION_DENIED")
        self.assertIn(response.data["business_detail_code"], {"NOT_AUTHENTICATED", "FORBIDDEN"})

    def test_retrieve_route_without_permission_should_return_permission_denied(self):
        route = self._create_route(name="详情航线无权限")
        self.client.force_authenticate(self.user)

        response = self.client.get(f"/api/v1/routes/{route.id}")
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.data["business_code"], "PERMISSION_DENIED")
        self.assertIn(response.data["business_detail_code"], {"FORBIDDEN", "PERMISSION_DENIED"})

    def test_patch_route_should_return_success(self):
        self._grant_permission("route.manage_route")
        self.client.force_authenticate(self.user)
        route = self._create_route(name="待更新航线")

        response = self.client.patch(
            f"/api/v1/routes/{route.id}",
            {
                "name": "已更新航线",
                "estimated_duration": 1800,
                "total_distance": "3560.80",
            },
            format="json",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["business_code"], "SUCCESS")
        self.assertEqual(response.data["business_detail_code"], "OK")
        self.assertEqual(response.data["name"], "已更新航线")
        self.assertEqual(response.data["estimated_duration"], 1800)
        self.assertEqual(response.data["total_distance"], "3560.80")
        route.refresh_from_db()
        self.assertEqual(route.name, "已更新航线")
        self.assertEqual(route.estimated_duration, 1800)
        self.assertTrue(
            AuditLog.objects.filter(
                action="ROUTE_UPDATE",
                target_type="route",
                target_id=str(route.id),
            ).exists()
        )

    def test_patch_route_empty_body_should_return_invalid_params(self):
        self._grant_permission("route.manage_route")
        self.client.force_authenticate(self.user)
        route = self._create_route(name="空更新航线")

        response = self.client.patch(
            f"/api/v1/routes/{route.id}",
            {},
            format="json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data["business_code"], "INVALID_PARAMS")
        self.assertEqual(response.data["business_detail_code"], "VALIDATION_ERROR")
        route.refresh_from_db()
        self.assertEqual(route.name, "空更新航线")

    def test_patch_route_should_not_allow_status_field(self):
        """测试 PATCH 不能修改 status 字段"""
        self._grant_permission("route.manage_route")
        self.client.force_authenticate(self.user)
        route = self._create_route(name="状态测试航线", status=RouteStatus.DISABLED)

        original_status = route.status
        response = self.client.patch(
            f"/api/v1/routes/{route.id}",
            {"status": RouteStatus.ACTIVE},
            format="json",
        )

        self.assertEqual(response.status_code, 400)
        route.refresh_from_db()
        self.assertEqual(route.status, original_status)

    def test_patch_route_not_found_should_return_resource_not_found(self):
        self._grant_permission("route.manage_route")
        self.client.force_authenticate(self.user)

        response = self.client.patch(
            "/api/v1/routes/999999",
            {"name": "不存在航线"},
            format="json",
        )

        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.data["business_code"], "RESOURCE_NOT_FOUND")
        self.assertEqual(response.data["business_detail_code"], "NOT_FOUND")

    def test_patch_route_without_auth_should_return_permission_denied(self):
        route = self._create_route(name="未认证更新航线")

        response = self.client.patch(
            f"/api/v1/routes/{route.id}",
            {"name": "未认证更新后"},
            format="json",
        )

        self.assertIn(response.status_code, (401, 403))
        self.assertEqual(response.data["business_code"], "PERMISSION_DENIED")
        self.assertIn(response.data["business_detail_code"], {"NOT_AUTHENTICATED", "FORBIDDEN"})

    def test_patch_route_without_permission_should_return_permission_denied(self):
        route = self._create_route(name="无权限更新航线")
        self.client.force_authenticate(self.user)

        response = self.client.patch(
            f"/api/v1/routes/{route.id}",
            {"name": "无权限更新后"},
            format="json",
        )

        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.data["business_code"], "PERMISSION_DENIED")
        self.assertIn(response.data["business_detail_code"], {"FORBIDDEN", "PERMISSION_DENIED"})

    def test_enable_route_should_return_success(self):
        self._grant_permission("route.manage_route")
        self.client.force_authenticate(self.user)
        route = self._create_route(name="待启用航线", status=RouteStatus.DISABLED)

        response = self.client.post(f"/api/v1/routes/{route.id}/enable")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["business_code"], "SUCCESS")
        self.assertEqual(response.data["business_detail_code"], "OK")
        self.assertEqual(response.data["status"], RouteStatus.ACTIVE)

        route.refresh_from_db()
        self.assertEqual(route.status, RouteStatus.ACTIVE)
        self.assertTrue(
            AuditLog.objects.filter(
                action="ROUTE_ENABLE",
                target_type="route",
                target_id=str(route.id),
            ).exists()
        )

    def test_enable_active_route_should_be_idempotent_success(self):
        self._grant_permission("route.manage_route")
        self.client.force_authenticate(self.user)
        route = self._create_route(name="已启用航线", status=RouteStatus.ACTIVE)

        response = self.client.post(f"/api/v1/routes/{route.id}/enable")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["business_code"], "SUCCESS")
        self.assertEqual(response.data["business_detail_code"], "OK")
        self.assertEqual(response.data["status"], RouteStatus.ACTIVE)

    def test_enable_route_with_body_should_return_invalid_params(self):
        self._grant_permission("route.manage_route")
        self.client.force_authenticate(self.user)
        route = self._create_route(name="启用参数航线", status=RouteStatus.DISABLED)

        response = self.client.post(
            f"/api/v1/routes/{route.id}/enable",
            {"unexpected": True},
            format="json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data["business_code"], "INVALID_PARAMS")
        self.assertEqual(response.data["business_detail_code"], "VALIDATION_ERROR")
        route.refresh_from_db()
        self.assertEqual(route.status, RouteStatus.DISABLED)

    def test_enable_route_not_found_should_return_resource_not_found(self):
        self._grant_permission("route.manage_route")
        self.client.force_authenticate(self.user)

        response = self.client.post("/api/v1/routes/999999/enable")

        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.data["business_code"], "RESOURCE_NOT_FOUND")
        self.assertEqual(response.data["business_detail_code"], "NOT_FOUND")

    def test_enable_route_without_auth_should_return_permission_denied(self):
        route = self._create_route(name="未认证启用航线", status=RouteStatus.DISABLED)

        response = self.client.post(f"/api/v1/routes/{route.id}/enable")

        self.assertIn(response.status_code, (401, 403))
        self.assertEqual(response.data["business_code"], "PERMISSION_DENIED")
        self.assertIn(response.data["business_detail_code"], {"NOT_AUTHENTICATED", "FORBIDDEN"})

    def test_enable_route_without_permission_should_return_permission_denied(self):
        route = self._create_route(name="无权限启用航线", status=RouteStatus.DISABLED)
        self.client.force_authenticate(self.user)

        response = self.client.post(f"/api/v1/routes/{route.id}/enable")

        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.data["business_code"], "PERMISSION_DENIED")
        self.assertIn(response.data["business_detail_code"], {"FORBIDDEN", "PERMISSION_DENIED"})

    def test_disable_route_should_return_success(self):
        self._grant_permission("route.manage_route")
        self.client.force_authenticate(self.user)
        route = self._create_route(name="待禁用航线", status=RouteStatus.ACTIVE)

        response = self.client.post(f"/api/v1/routes/{route.id}/disable")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["business_code"], "SUCCESS")
        self.assertEqual(response.data["business_detail_code"], "OK")
        self.assertEqual(response.data["status"], RouteStatus.DISABLED)

        route.refresh_from_db()
        self.assertEqual(route.status, RouteStatus.DISABLED)
        self.assertTrue(
            AuditLog.objects.filter(
                action="ROUTE_DISABLE",
                target_type="route",
                target_id=str(route.id),
            ).exists()
        )

    def test_disable_disabled_route_should_be_idempotent_success(self):
        self._grant_permission("route.manage_route")
        self.client.force_authenticate(self.user)
        route = self._create_route(name="已禁用航线", status=RouteStatus.DISABLED)

        response = self.client.post(f"/api/v1/routes/{route.id}/disable")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["business_code"], "SUCCESS")
        self.assertEqual(response.data["business_detail_code"], "OK")
        self.assertEqual(response.data["status"], RouteStatus.DISABLED)

    def test_disable_route_with_body_should_return_invalid_params(self):
        self._grant_permission("route.manage_route")
        self.client.force_authenticate(self.user)
        route = self._create_route(name="禁用参数航线", status=RouteStatus.ACTIVE)

        response = self.client.post(
            f"/api/v1/routes/{route.id}/disable",
            {"unexpected": True},
            format="json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data["business_code"], "INVALID_PARAMS")
        self.assertEqual(response.data["business_detail_code"], "VALIDATION_ERROR")
        route.refresh_from_db()
        self.assertEqual(route.status, RouteStatus.ACTIVE)

    def test_disable_route_not_found_should_return_resource_not_found(self):
        self._grant_permission("route.manage_route")
        self.client.force_authenticate(self.user)

        response = self.client.post("/api/v1/routes/999999/disable")

        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.data["business_code"], "RESOURCE_NOT_FOUND")
        self.assertEqual(response.data["business_detail_code"], "NOT_FOUND")

    def test_disable_route_without_auth_should_return_permission_denied(self):
        route = self._create_route(name="未认证禁用航线", status=RouteStatus.ACTIVE)

        response = self.client.post(f"/api/v1/routes/{route.id}/disable")

        self.assertIn(response.status_code, (401, 403))
        self.assertEqual(response.data["business_code"], "PERMISSION_DENIED")
        self.assertIn(response.data["business_detail_code"], {"NOT_AUTHENTICATED", "FORBIDDEN"})

    def test_disable_route_without_permission_should_return_permission_denied(self):
        route = self._create_route(name="无权限禁用航线", status=RouteStatus.ACTIVE)
        self.client.force_authenticate(self.user)

        response = self.client.post(f"/api/v1/routes/{route.id}/disable")

        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.data["business_code"], "PERMISSION_DENIED")
        self.assertIn(response.data["business_detail_code"], {"FORBIDDEN", "PERMISSION_DENIED"})

    def test_delete_route_should_hard_delete_route_and_waypoints(self):
        self._grant_permission("route.manage_route")
        self.client.force_authenticate(self.user)
        route = self._create_route(name="待物理删除航线")
        self._create_waypoint(route, sequence=1)
        self._create_waypoint(route, sequence=2)

        response = self.client.delete(f"/api/v1/routes/{route.id}")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["business_code"], "SUCCESS")
        self.assertEqual(response.data["business_detail_code"], "OK")
        self.assertEqual(response.data["delete_mode"], "hard")
        self.assertEqual(response.data["deleted_waypoint_count"], 2)
        self.assertFalse(Route.objects.filter(id=route.id).exists())
        self.assertFalse(Waypoint.objects.filter(route_id=route.id).exists())
        self.assertTrue(
            AuditLog.objects.filter(
                action="ROUTE_DELETE",
                target_type="route",
                target_id=str(route.id),
            ).exists()
        )

    def test_delete_route_with_missions_should_disable_instead_of_hard_delete(self):
        self._grant_permission("route.manage_route")
        self.client.force_authenticate(self.user)
        route = self._create_route(name="被任务引用航线", status=RouteStatus.ACTIVE)
        self._create_mission(route)

        response = self.client.delete(f"/api/v1/routes/{route.id}")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["business_code"], "SUCCESS")
        self.assertEqual(response.data["business_detail_code"], "OK")
        self.assertEqual(response.data["delete_mode"], "disabled")
        route.refresh_from_db()
        self.assertEqual(route.status, RouteStatus.DISABLED)
        self.assertTrue(Route.objects.filter(id=route.id).exists())
        self.assertTrue(
            AuditLog.objects.filter(
                action="ROUTE_DELETE",
                target_type="route",
                target_id=str(route.id),
            ).exists()
        )

    def test_delete_route_already_disabled_with_missions_should_be_idempotent(self):
        self._grant_permission("route.manage_route")
        self.client.force_authenticate(self.user)
        route = self._create_route(name="已禁用引用航线", status=RouteStatus.DISABLED)
        self._create_mission(route, name="引用禁用航线任务")

        response = self.client.delete(f"/api/v1/routes/{route.id}")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["business_code"], "SUCCESS")
        self.assertEqual(response.data["business_detail_code"], "OK")
        self.assertEqual(response.data["delete_mode"], "disabled")
        route.refresh_from_db()
        self.assertEqual(route.status, RouteStatus.DISABLED)

    def test_delete_route_with_body_should_return_invalid_params(self):
        self._grant_permission("route.manage_route")
        self.client.force_authenticate(self.user)
        route = self._create_route(name="删除参数校验航线")

        response = self.client.delete(
            f"/api/v1/routes/{route.id}",
            {"unexpected": True},
            format="json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data["business_code"], "INVALID_PARAMS")
        self.assertEqual(response.data["business_detail_code"], "VALIDATION_ERROR")
        self.assertTrue(Route.objects.filter(id=route.id).exists())

    def test_delete_route_not_found_should_return_resource_not_found(self):
        self._grant_permission("route.manage_route")
        self.client.force_authenticate(self.user)

        response = self.client.delete("/api/v1/routes/999999")

        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.data["business_code"], "RESOURCE_NOT_FOUND")
        self.assertEqual(response.data["business_detail_code"], "NOT_FOUND")

    def test_delete_route_without_auth_should_return_permission_denied(self):
        route = self._create_route(name="删除未认证航线")

        response = self.client.delete(f"/api/v1/routes/{route.id}")

        self.assertIn(response.status_code, (401, 403))
        self.assertEqual(response.data["business_code"], "PERMISSION_DENIED")
        self.assertIn(response.data["business_detail_code"], {"NOT_AUTHENTICATED", "FORBIDDEN"})

    def test_delete_route_without_permission_should_return_permission_denied(self):
        route = self._create_route(name="删除无权限航线")
        self.client.force_authenticate(self.user)

        response = self.client.delete(f"/api/v1/routes/{route.id}")

        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.data["business_code"], "PERMISSION_DENIED")
        self.assertIn(response.data["business_detail_code"], {"FORBIDDEN", "PERMISSION_DENIED"})
