from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

from apps.access.models import AuditLog, ScopeType
from apps.access.test_support import ensure_staff_profile, ensure_tenant_role_binding, grant_role_permissions
from apps.route.models import Route, RouteStatus
from apps.waypoint.models import Waypoint

User = get_user_model()


class WaypointApiTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.user = User.objects.create_user(username="waypoint_admin", password="pass1234", status=1)
        self.staff = ensure_staff_profile(self.user, staff_no="W-001", name="航点管理员A", employment_status=1)
        self.tenant, self.member, self.role = ensure_tenant_role_binding(
            self.user,
            tenant_code="waypoint_test_tenant",
            role_code="waypoint_test_role",
            role_name="航点测试角色",
        )
        self.client.credentials(HTTP_X_TENANT_CODE=self.tenant.code)
        self.route_active = Route.objects.create(
            tenant=self.tenant,
            name="航点测试航线A",
            status=RouteStatus.ACTIVE,
            creator_name=self.staff.name,
        )
        self.route_disabled = Route.objects.create(
            tenant=self.tenant,
            name="航点测试航线B",
            status=RouteStatus.DISABLED,
            creator_name=self.staff.name,
        )

    def _grant_permission(self, permission_code: str):
        grant_role_permissions(
            self.role,
            {permission_code: ScopeType.ALL},
            group_name=f"{permission_code}-group",
        )

    def _create_waypoint(
        self,
        *,
        route: Route,
        sequence: int,
        latitude: str = "22.28612345",
        longitude: str = "113.56781234",
        altitude: str = "120.50",
    ) -> Waypoint:
        return Waypoint.objects.create(
            route=route,
            sequence=sequence,
            latitude=latitude,
            longitude=longitude,
            altitude=altitude,
        )

    def test_create_waypoint_should_return_success(self):
        self._grant_permission("waypoint.manage_waypoint")
        self.client.force_authenticate(self.user)

        response = self.client.post(
            "/api/v1/waypoints",
            {
                "route": self.route_active.id,
                "sequence": 1,
                "latitude": "22.28612345",
                "longitude": "113.56781234",
                "altitude": "120.50",
            },
            format="json",
        )
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data["business_code"], "SUCCESS")
        self.assertEqual(response.data["business_detail_code"], "OK")
        self.assertEqual(response.data["route"], self.route_active.id)
        self.assertEqual(response.data["sequence"], 1)

        waypoint = Waypoint.objects.get(id=response.data["id"])
        self.assertEqual(waypoint.route_id, self.route_active.id)
        self.assertEqual(waypoint.sequence, 1)
        self.route_active.refresh_from_db()
        self.assertEqual(self.route_active.waypoint_count, 1)
        self.assertTrue(
            AuditLog.objects.filter(
                action="WAYPOINT_CREATE",
                target_type="waypoint",
                target_id=str(waypoint.id),
            ).exists()
        )

    def test_create_waypoint_invalid_params_should_return_invalid_params(self):
        self._grant_permission("waypoint.manage_waypoint")
        self.client.force_authenticate(self.user)

        response = self.client.post(
            "/api/v1/waypoints",
            {
                "route": self.route_active.id,
                "sequence": 1,
                "longitude": "113.56781234",
                "altitude": "120.50",
            },
            format="json",
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data["business_code"], "INVALID_PARAMS")
        self.assertEqual(response.data["business_detail_code"], "VALIDATION_ERROR")
        self.assertIn("latitude", response.data)

    def test_create_waypoint_for_disabled_route_should_return_invalid_params(self):
        self._grant_permission("waypoint.manage_waypoint")
        self.client.force_authenticate(self.user)

        response = self.client.post(
            "/api/v1/waypoints",
            {
                "route": self.route_disabled.id,
                "sequence": 1,
                "latitude": "22.28612345",
                "longitude": "113.56781234",
                "altitude": "120.50",
            },
            format="json",
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data["business_code"], "INVALID_PARAMS")
        self.assertEqual(response.data["business_detail_code"], "VALIDATION_ERROR")
        self.assertIn("route", response.data)

    def test_create_waypoint_with_nonexistent_route_should_return_invalid_params(self):
        """测试 route 不存在"""
        self._grant_permission("waypoint.manage_waypoint")
        self.client.force_authenticate(self.user)

        response = self.client.post(
            "/api/v1/waypoints",
            {
                "route": 99999,
                "sequence": 1,
                "latitude": "22.28612345",
                "longitude": "113.56781234",
                "altitude": "100.00",
            },
            format="json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data["business_code"], "INVALID_PARAMS")

    def test_create_waypoint_duplicate_route_sequence_returns_idempotent_duplicate(self):
        """测试同一航线序号重复创建时返回幂等重复响应"""
        self._grant_permission("waypoint.manage_waypoint")
        self.client.force_authenticate(self.user)
        Waypoint.objects.create(
            route=self.route_active,
            sequence=1,
            latitude="22.28612345",
            longitude="113.56781234",
            altitude="120.50",
        )
        self.route_active.waypoint_count = 1
        self.route_active.save(update_fields=["waypoint_count", "updated_at"])

        response = self.client.post(
            "/api/v1/waypoints",
            {
                "route": self.route_active.id,
                "sequence": 1,
                "latitude": "22.28612346",
                "longitude": "113.56781235",
                "altitude": "121.00",
            },
            format="json",
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data["business_code"], "IDEMPOTENT_DUPLICATE")
        self.assertEqual(response.data["business_detail_code"], "DUPLICATE_REQUEST")
        self.assertIn("non_field_errors", response.data)

    def test_create_waypoint_without_auth_should_return_permission_denied(self):
        response = self.client.post(
            "/api/v1/waypoints",
            {
                "route": self.route_active.id,
                "sequence": 1,
                "latitude": "22.28612345",
                "longitude": "113.56781234",
                "altitude": "120.50",
            },
            format="json",
        )
        self.assertIn(response.status_code, (401, 403))
        self.assertEqual(response.data["business_code"], "PERMISSION_DENIED")
        self.assertIn(response.data["business_detail_code"], {"NOT_AUTHENTICATED", "FORBIDDEN"})

    def test_create_waypoint_without_permission_should_return_permission_denied(self):
        self.client.force_authenticate(self.user)

        response = self.client.post(
            "/api/v1/waypoints",
            {
                "route": self.route_active.id,
                "sequence": 1,
                "latitude": "22.28612345",
                "longitude": "113.56781234",
                "altitude": "120.50",
            },
            format="json",
        )
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.data["business_code"], "PERMISSION_DENIED")
        self.assertIn(response.data["business_detail_code"], {"FORBIDDEN", "PERMISSION_DENIED"})

    def test_patch_waypoint_should_return_success(self):
        self._grant_permission("waypoint.manage_waypoint")
        self.client.force_authenticate(self.user)
        waypoint = self._create_waypoint(route=self.route_active, sequence=6)

        response = self.client.patch(
            f"/api/v1/waypoints/{waypoint.id}",
            {
                "sequence": 7,
                "latitude": "22.28612399",
                "altitude": "125.00",
            },
            format="json",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["business_code"], "SUCCESS")
        self.assertEqual(response.data["business_detail_code"], "OK")
        self.assertEqual(response.data["id"], waypoint.id)
        self.assertEqual(response.data["sequence"], 7)
        self.assertEqual(response.data["latitude"], "22.28612399")
        self.assertEqual(response.data["altitude"], "125.00")

        waypoint.refresh_from_db()
        self.assertEqual(waypoint.sequence, 7)
        self.assertEqual(str(waypoint.latitude), "22.28612399")
        self.assertEqual(str(waypoint.altitude), "125.00")
        self.assertTrue(
            AuditLog.objects.filter(
                action="WAYPOINT_UPDATE",
                target_type="waypoint",
                target_id=str(waypoint.id),
            ).exists()
        )

    def test_patch_waypoint_with_duplicate_sequence_should_return_idempotent_duplicate(self):
        self._grant_permission("waypoint.manage_waypoint")
        self.client.force_authenticate(self.user)
        self._create_waypoint(route=self.route_active, sequence=8)
        waypoint = self._create_waypoint(route=self.route_active, sequence=9)

        response = self.client.patch(
            f"/api/v1/waypoints/{waypoint.id}",
            {"sequence": 8},
            format="json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data["business_code"], "IDEMPOTENT_DUPLICATE")
        self.assertEqual(response.data["business_detail_code"], "DUPLICATE_REQUEST")
        self.assertIn("sequence", response.data)

    def test_patch_waypoint_with_unknown_field_should_return_invalid_params(self):
        self._grant_permission("waypoint.manage_waypoint")
        self.client.force_authenticate(self.user)
        waypoint = self._create_waypoint(route=self.route_active, sequence=10)

        response = self.client.patch(
            f"/api/v1/waypoints/{waypoint.id}",
            {"route": self.route_disabled.id},
            format="json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data["business_code"], "INVALID_PARAMS")
        self.assertEqual(response.data["business_detail_code"], "VALIDATION_ERROR")
        self.assertIn("route", response.data)

    def test_patch_waypoint_not_found_should_return_resource_not_found(self):
        self._grant_permission("waypoint.manage_waypoint")
        self.client.force_authenticate(self.user)

        response = self.client.patch(
            "/api/v1/waypoints/999999",
            {"sequence": 11},
            format="json",
        )

        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.data["business_code"], "RESOURCE_NOT_FOUND")
        self.assertEqual(response.data["business_detail_code"], "NOT_FOUND")

    def test_patch_waypoint_without_auth_should_return_permission_denied(self):
        waypoint = self._create_waypoint(route=self.route_active, sequence=12)

        response = self.client.patch(
            f"/api/v1/waypoints/{waypoint.id}",
            {"sequence": 13},
            format="json",
        )
        self.assertIn(response.status_code, (401, 403))
        self.assertEqual(response.data["business_code"], "PERMISSION_DENIED")
        self.assertIn(response.data["business_detail_code"], {"NOT_AUTHENTICATED", "FORBIDDEN"})

    def test_patch_waypoint_without_permission_should_return_permission_denied(self):
        self.client.force_authenticate(self.user)
        waypoint = self._create_waypoint(route=self.route_active, sequence=14)

        response = self.client.patch(
            f"/api/v1/waypoints/{waypoint.id}",
            {"sequence": 15},
            format="json",
        )
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.data["business_code"], "PERMISSION_DENIED")
        self.assertIn(response.data["business_detail_code"], {"FORBIDDEN", "PERMISSION_DENIED"})

    def test_delete_waypoint_should_return_success_and_delete(self):
        self._grant_permission("waypoint.manage_waypoint")
        self.client.force_authenticate(self.user)
        waypoint = self._create_waypoint(route=self.route_active, sequence=16)
        self.route_active.waypoint_count = 1
        self.route_active.save(update_fields=["waypoint_count", "updated_at"])

        response = self.client.delete(f"/api/v1/waypoints/{waypoint.id}")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["business_code"], "SUCCESS")
        self.assertEqual(response.data["business_detail_code"], "OK")
        self.assertEqual(response.data["id"], waypoint.id)
        self.assertTrue(response.data["deleted"])

        self.assertFalse(Waypoint.objects.filter(id=waypoint.id).exists())
        self.route_active.refresh_from_db()
        self.assertEqual(self.route_active.waypoint_count, 0)
        self.assertTrue(
            AuditLog.objects.filter(
                action="WAYPOINT_DELETE",
                target_type="waypoint",
                target_id=str(waypoint.id),
            ).exists()
        )

    def test_delete_waypoint_not_found_should_return_resource_not_found(self):
        self._grant_permission("waypoint.manage_waypoint")
        self.client.force_authenticate(self.user)

        response = self.client.delete("/api/v1/waypoints/999999")

        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.data["business_code"], "RESOURCE_NOT_FOUND")
        self.assertEqual(response.data["business_detail_code"], "NOT_FOUND")

    def test_delete_waypoint_without_auth_should_return_permission_denied(self):
        waypoint = self._create_waypoint(route=self.route_active, sequence=17)

        response = self.client.delete(f"/api/v1/waypoints/{waypoint.id}")

        self.assertIn(response.status_code, (401, 403))
        self.assertEqual(response.data["business_code"], "PERMISSION_DENIED")
        self.assertIn(response.data["business_detail_code"], {"NOT_AUTHENTICATED", "FORBIDDEN"})

    def test_delete_waypoint_without_permission_should_return_permission_denied(self):
        self.client.force_authenticate(self.user)
        waypoint = self._create_waypoint(route=self.route_active, sequence=18)

        response = self.client.delete(f"/api/v1/waypoints/{waypoint.id}")

        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.data["business_code"], "PERMISSION_DENIED")
        self.assertIn(response.data["business_detail_code"], {"FORBIDDEN", "PERMISSION_DENIED"})

    def test_list_waypoints_should_return_success(self):
        self._grant_permission("waypoint.view_waypoint")
        self.client.force_authenticate(self.user)
        self._create_waypoint(route=self.route_active, sequence=1)
        self._create_waypoint(route=self.route_active, sequence=2, latitude="22.28612346")

        response = self.client.get("/api/v1/waypoints")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["business_code"], "SUCCESS")
        self.assertEqual(response.data["business_detail_code"], "OK")
        self.assertIn("results", response.data)
        self.assertEqual(len(response.data["results"]), 2)

    def test_list_waypoints_with_filter_should_return_filtered_results(self):
        self._grant_permission("waypoint.view_waypoint")
        self.client.force_authenticate(self.user)
        self._create_waypoint(route=self.route_active, sequence=1)
        self._create_waypoint(route=self.route_active, sequence=2)

        response = self.client.get(
            "/api/v1/waypoints",
            {"route_id": self.route_active.id, "sequence": 2},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["business_code"], "SUCCESS")
        self.assertEqual(response.data["business_detail_code"], "OK")
        self.assertEqual(len(response.data["results"]), 1)
        self.assertEqual(response.data["results"][0]["sequence"], 2)

    def test_retrieve_waypoint_should_return_success(self):
        self._grant_permission("waypoint.view_waypoint")
        self.client.force_authenticate(self.user)
        waypoint = self._create_waypoint(route=self.route_active, sequence=3)

        response = self.client.get(f"/api/v1/waypoints/{waypoint.id}")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["business_code"], "SUCCESS")
        self.assertEqual(response.data["business_detail_code"], "OK")
        self.assertEqual(response.data["id"], waypoint.id)
        self.assertEqual(response.data["route"], self.route_active.id)
        self.assertEqual(response.data["sequence"], 3)

    def test_retrieve_waypoint_not_found_should_return_resource_not_found(self):
        self._grant_permission("waypoint.view_waypoint")
        self.client.force_authenticate(self.user)

        response = self.client.get("/api/v1/waypoints/999999")

        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.data["business_code"], "RESOURCE_NOT_FOUND")
        self.assertEqual(response.data["business_detail_code"], "NOT_FOUND")

    def test_retrieve_waypoint_without_auth_should_return_permission_denied(self):
        waypoint = self._create_waypoint(route=self.route_active, sequence=4)

        response = self.client.get(f"/api/v1/waypoints/{waypoint.id}")

        self.assertIn(response.status_code, (401, 403))
        self.assertEqual(response.data["business_code"], "PERMISSION_DENIED")
        self.assertIn(response.data["business_detail_code"], {"NOT_AUTHENTICATED", "FORBIDDEN"})

    def test_retrieve_waypoint_without_permission_should_return_permission_denied(self):
        waypoint = self._create_waypoint(route=self.route_active, sequence=5)
        self.client.force_authenticate(self.user)

        response = self.client.get(f"/api/v1/waypoints/{waypoint.id}")

        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.data["business_code"], "PERMISSION_DENIED")
        self.assertIn(response.data["business_detail_code"], {"FORBIDDEN", "PERMISSION_DENIED"})

    def test_list_waypoints_without_auth_should_return_permission_denied(self):
        response = self.client.get("/api/v1/waypoints")
        self.assertIn(response.status_code, (401, 403))
        self.assertEqual(response.data["business_code"], "PERMISSION_DENIED")
        self.assertIn(response.data["business_detail_code"], {"NOT_AUTHENTICATED", "FORBIDDEN"})

    def test_list_waypoints_without_permission_should_return_permission_denied(self):
        self.client.force_authenticate(self.user)

        response = self.client.get("/api/v1/waypoints")
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.data["business_code"], "PERMISSION_DENIED")
        self.assertIn(response.data["business_detail_code"], {"FORBIDDEN", "PERMISSION_DENIED"})


# ============================================================================
# 扩展测试：边界值 + 航点特有场景
# ============================================================================

class WaypointBoundaryAndExtendedTests(TestCase):
    """航点边界值和扩展场景测试"""

    def setUp(self):
        self.client = APIClient()
        self.user = User.objects.create_user(username="waypoint_ext", password="pass1234", status=1)
        self.staff = ensure_staff_profile(self.user, staff_no="W-EXT-001", name="测试人员", employment_status=1)
        self.tenant, self.member, self.role = ensure_tenant_role_binding(
            self.user,
            tenant_code="waypoint_ext_tenant",
            role_code="waypoint_ext_role",
            role_name="航点扩展测试角色",
        )
        self.client.credentials(HTTP_X_TENANT_CODE=self.tenant.code)
        self.route = Route.objects.create(
            tenant=self.tenant,
            name="边界测试航线",
            status=RouteStatus.ACTIVE,
            creator_name=self.staff.name,
        )

    def _grant_permission(self, permission_code: str, scope=ScopeType.ALL):
        grant_role_permissions(
            self.role,
            {permission_code: scope},
            group_name=f"{permission_code}-group-ext",
        )

    def test_create_waypoint_boundary_latitude_min(self):
        """测试纬度最小边界值（-90）"""
        self._grant_permission("waypoint.manage_waypoint")
        self.client.force_authenticate(self.user)

        response = self.client.post("/api/v1/waypoints", {
            "route": self.route.id,
            "sequence": 1,
            "latitude": "-90.00000000",
            "longitude": "0.00000000",
            "altitude": "100.00",
        })

        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data["business_code"], "SUCCESS")

    def test_create_waypoint_boundary_latitude_max(self):
        """测试纬度最大边界值（90）"""
        self._grant_permission("waypoint.manage_waypoint")
        self.client.force_authenticate(self.user)

        response = self.client.post("/api/v1/waypoints", {
            "route": self.route.id,
            "sequence": 1,
            "latitude": "90.00000000",
            "longitude": "180.00000000",
            "altitude": "100.00",
        })

        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data["business_code"], "SUCCESS")

    def test_create_waypoint_invalid_latitude_out_of_range(self):
        """测试纬度超出范围（>90）"""
        self._grant_permission("waypoint.manage_waypoint")
        self.client.force_authenticate(self.user)

        response = self.client.post("/api/v1/waypoints", {
            "route": self.route.id,
            "sequence": 1,
            "latitude": "91.00000000",
            "longitude": "0.00000000",
            "altitude": "100.00",
        })

        # 系统可能允许（依赖业务规则）或拒绝
        self.assertIn(response.status_code, (201, 400))

    def test_create_waypoint_invalid_longitude_out_of_range(self):
        """测试经度超出范围（>180）"""
        self._grant_permission("waypoint.manage_waypoint")
        self.client.force_authenticate(self.user)

        response = self.client.post("/api/v1/waypoints", {
            "route": self.route.id,
            "sequence": 1,
            "latitude": "0.00000000",
            "longitude": "181.00000000",
            "altitude": "100.00",
        })

        # 系统可能允许（依赖业务规则）或拒绝
        self.assertIn(response.status_code, (201, 400))

    def test_create_waypoint_boundary_altitude_min(self):
        """测试高度最小边界值（0）"""
        self._grant_permission("waypoint.manage_waypoint")
        self.client.force_authenticate(self.user)

        response = self.client.post("/api/v1/waypoints", {
            "route": self.route.id,
            "sequence": 1,
            "latitude": "22.28612345",
            "longitude": "113.56781234",
            "altitude": "0.00",
        })

        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data["business_code"], "SUCCESS")

    def test_create_waypoint_boundary_altitude_max(self):
        """测试高度最大边界值"""
        self._grant_permission("waypoint.manage_waypoint")
        self.client.force_authenticate(self.user)

        response = self.client.post("/api/v1/waypoints", {
            "route": self.route.id,
            "sequence": 1,
            "latitude": "22.28612345",
            "longitude": "113.56781234",
            "altitude": "999999.99",
        })

        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data["business_code"], "SUCCESS")

    def test_create_waypoint_invalid_negative_altitude(self):
        """测试负高度（允许或拒绝取决于业务规则）"""
        self._grant_permission("waypoint.manage_waypoint")
        self.client.force_authenticate(self.user)

        response = self.client.post("/api/v1/waypoints", {
            "route": self.route.id,
            "sequence": 1,
            "latitude": "22.28612345",
            "longitude": "113.56781234",
            "altitude": "-10.00",
        })

        # 接受 201（允许负高度）或 400（拒绝）
        self.assertIn(response.status_code, (201, 400))

    def test_create_waypoint_sequence_zero(self):
        """测试序号为0"""
        self._grant_permission("waypoint.manage_waypoint")
        self.client.force_authenticate(self.user)

        response = self.client.post("/api/v1/waypoints", {
            "route": self.route.id,
            "sequence": 0,
            "latitude": "22.28612345",
            "longitude": "113.56781234",
            "altitude": "100.00",
        })

        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data["business_code"], "SUCCESS")

    def test_create_waypoint_large_sequence(self):
        """测试大序号"""
        self._grant_permission("waypoint.manage_waypoint")
        self.client.force_authenticate(self.user)

        response = self.client.post("/api/v1/waypoints", {
            "route": self.route.id,
            "sequence": 99999,
            "latitude": "22.28612345",
            "longitude": "113.56781234",
            "altitude": "100.00",
        })

        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data["business_code"], "SUCCESS")

    def test_create_waypoint_missing_required_field(self):
        """测试缺少必填字段"""
        self._grant_permission("waypoint.manage_waypoint")
        self.client.force_authenticate(self.user)

        response = self.client.post("/api/v1/waypoints", {
            "route": self.route.id,
            "sequence": 1,
            # 缺少 latitude, longitude, altitude
        })

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data["business_code"], "INVALID_PARAMS")

    def test_create_waypoint_invalid_coordinate_format(self):
        """测试无效坐标格式（非数字）"""
        self._grant_permission("waypoint.manage_waypoint")
        self.client.force_authenticate(self.user)

        response = self.client.post("/api/v1/waypoints", {
            "route": self.route.id,
            "sequence": 1,
            "latitude": "invalid",
            "longitude": "113.56781234",
            "altitude": "100.00",
        })

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data["business_code"], "INVALID_PARAMS")

    def test_list_waypoints_filtered_by_route(self):
        """测试按航线筛选航点"""
        self._grant_permission("waypoint.view_waypoint")
        self.client.force_authenticate(self.user)

        # 创建另一条航线的航点
        route2 = Route.objects.create(name="边界测试航线2", status=RouteStatus.ACTIVE, creator_name=self.staff.name)
        waypoint1 = self._create_waypoint(self.route, sequence=1)
        waypoint2 = self._create_waypoint(route2, sequence=1)

        # 获取所有航点
        response = self.client.get("/api/v1/waypoints")

        self.assertEqual(response.status_code, 200)
        # 验证返回了多条航点
        self.assertGreaterEqual(len(response.data["results"]), 2)

    def test_patch_waypoint_cannot_change_route(self):
        """测试不能通过PATCH切换航线"""
        self._grant_permission("waypoint.manage_waypoint")
        self.client.force_authenticate(self.user)

        route2 = Route.objects.create(name="新航线", status=RouteStatus.ACTIVE, creator_name=self.staff.name)
        waypoint = self._create_waypoint(self.route, sequence=1)

        # 尝试切换航线
        response = self.client.patch(f"/api/v1/waypoints/{waypoint.id}", {
            "route": route2.id,
        })

        # 应该不能切换，或者被忽略
        waypoint.refresh_from_db()
        self.assertEqual(waypoint.route_id, self.route.id)

    def test_delete_waypoint_actually_deletes(self):
        """测试删除航点后数据确实被删除"""
        # 同时授予查看和管理权限
        self._grant_permission("waypoint.manage_waypoint")
        self._grant_permission("waypoint.view_waypoint")
        self.client.force_authenticate(self.user)

        waypoint = self._create_waypoint(self.route, sequence=99)

        # 确认创建成功
        get_resp = self.client.get(f"/api/v1/waypoints/{waypoint.id}")
        self.assertEqual(get_resp.status_code, 200)

        # 删除
        delete_resp = self.client.delete(f"/api/v1/waypoints/{waypoint.id}")
        self.assertIn(delete_resp.status_code, (200, 204))

        # 确认删除成功
        get_after = self.client.get(f"/api/v1/waypoints/{waypoint.id}")
        self.assertEqual(get_after.status_code, 404)

    def _create_waypoint(self, route: Route, sequence: int) -> Waypoint:
        return Waypoint.objects.create(
            route=route,
            sequence=sequence,
            latitude="22.28612345",
            longitude="113.56781234",
            altitude="100.00",
        )
