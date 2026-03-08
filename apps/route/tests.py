from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group, Permission
from django.test import TestCase
from rest_framework.test import APIClient

from apps.access.models import AuditLog, GroupPermissionScope, ScopeStatus, ScopeType, StaffProfile, StaffType, StaffTypeGroup
from apps.route.models import Route, RouteStatus, RouteType

User = get_user_model()


class RouteApiTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.staff_type = StaffType.objects.create(code="route_admin_test", name="航线管理员", status=1)
        self.user = User.objects.create_user(username="route_admin", password="pass1234", status=1)
        self.staff = StaffProfile.objects.create(
            user=self.user,
            staff_no="R-001",
            name="航线管理员A",
            employment_status=1,
            staff_type=self.staff_type,
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
